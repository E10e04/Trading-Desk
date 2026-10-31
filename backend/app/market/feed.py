"""MarketFeed : point central des données de marché (quotes, bougies, fraîcheur, diffusion).

Indépendant des agents et de l'interface : il consomme un `MarketDataProvider` et publie sur le bus.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any, Awaitable, Callable

from ..bus import EventBus
from .bars import LIVE_TFS, BarStore, aggregate, merge
from .biquote import INTERVALS, utcnow
from .instruments import classify, session_profile, spec_for
from .provider import Bar, Instrument, MarketDataProvider, ProviderError, Quote

log = logging.getLogger(__name__)
QuoteListener = Callable[[Quote], Awaitable[None] | None]


class MarketFeed:
    def __init__(self, provider: MarketDataProvider, bus: EventBus, *, live_s: float = 30.0, hard_stale_s: float = 300.0,
                 publish_interval: float = 0.25, max_subscribe: int = 2000) -> None:
        self.provider = provider
        self.bus = bus
        self.live_s = live_s
        self.hard_stale_s = hard_stale_s
        self.publish_interval = publish_interval
        self.max_subscribe = max_subscribe
        self.instruments: dict[str, Instrument] = {}
        self.quotes: dict[str, Quote] = {}
        self.states: dict[str, str] = {}
        self.bars = BarStore()
        self._listeners: list[QuoteListener] = []
        self._last_pub: dict[str, float] = {}
        self._hist_cache: dict[tuple[str, str], tuple[float, list[Bar], int]] = {}
        self._seeding: dict[tuple[str, str], asyncio.Task] = {}
        self._bg: list[asyncio.Task] = []
        self.catalogue_error: str | None = None
        self.started_at: dt.datetime | None = None
        self.instruments_loaded_at: dt.datetime | None = None
        self.subscribe_mode = "all"

    # ------------------------------------------------------------------ cycle de vie
    async def start(self) -> None:
        self.started_at = utcnow()
        await self.provider.start_stream(self._on_tick, self._on_state)
        self._bg.append(asyncio.create_task(self._load_catalogue_loop(), name="feed-catalogue"))
        self._bg.append(asyncio.create_task(self._status_loop(), name="feed-status"))

    async def stop(self) -> None:
        for t in self._bg:
            t.cancel()
        for t in list(self._seeding.values()):
            t.cancel()
        await self.provider.stop_stream()

    async def _load_catalogue_loop(self) -> None:
        delay = 5.0
        while True:
            try:
                insts = await self.provider.list_instruments()
                if insts:
                    self.instruments = {i.symbol: i for i in insts}
                    self.instruments_loaded_at = utcnow()
                    self.catalogue_error = None
                    symbols = sorted(self.instruments)[: self.max_subscribe]
                    await self.provider.subscribe(symbols)
                    await self._prime_quotes(symbols)
                    await self.bus.publish("catalogue", {"count": len(self.instruments)})
                    await asyncio.sleep(3600)  # rafraîchissement horaire du catalogue
                    continue
                self.catalogue_error = "catalogue vide"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.catalogue_error = f"{type(e).__name__}: {str(e)[:160]}"
                log.warning("catalogue BiQuote indisponible: %s", self.catalogue_error)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

    async def _prime_quotes(self, symbols: list[str]) -> None:
        """Dernière cotation connue (même marché fermé) pour afficher des prix immédiatement."""
        try:
            got = await self.provider.latest(symbols)
        except Exception as e:
            log.info("amorçage des cotations impossible: %s", e)
            return
        for sym, q in got.items():
            if sym not in self.quotes:
                self.quotes[sym] = q
                if q.market_state != "unknown":
                    self.states[sym] = q.market_state

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            await self.bus.publish("feed_status", self.health())

    # ------------------------------------------------------------------ flux
    def add_listener(self, cb: QuoteListener) -> None:
        self._listeners.append(cb)

    async def _on_state(self, symbol: str, state: str) -> None:
        self.states[symbol] = state

    async def _on_tick(self, q: Quote) -> None:
        if q.market_state == "unknown" and q.symbol in self.states:
            q.market_state = self.states[q.symbol]
        self.quotes[q.symbol] = q
        for tf, bar in self.bars.update(q):
            await self.bus.publish("bar_closed", {"symbol": q.symbol, "tf": tf, "bar": bar.to_dict()})
        for cb in self._listeners:
            try:
                r = cb(q)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("écouteur de cotation en échec")
        now = time.monotonic()
        if now - self._last_pub.get(q.symbol, 0.0) >= self.publish_interval:
            self._last_pub[q.symbol] = now
            await self.bus.publish("tick", self.quote_view(q))

    # ------------------------------------------------------------------ lecture
    def freshness(self, symbol: str, now: dt.datetime | None = None) -> dict[str, Any]:
        """Statut de la donnée : live | stale | closed | unknown (+ âge en secondes)."""
        q = self.quotes.get(symbol)
        if q is None:
            return {"status": "unknown", "age_s": None}
        age = q.age_s(now)
        state = self.states.get(symbol, q.market_state)
        if state == "closed":
            status = "closed"
        elif age <= self.live_s:
            status = "live"
        else:
            status = "stale"
        return {"status": status, "age_s": round(age, 1), "hard_stale": age > self.hard_stale_s}

    @staticmethod
    def source_lag(q: Quote) -> float | None:
        """Retard entre l'horodatage de la source et la réception (s). None si la source n'en donne pas ; peut être négatif
        (horloges désynchronisées). C'est la mesure du retard réel du fournisseur, que l'âge de cotation ne montre pas."""
        return round((q.ts_recv - q.ts_source).total_seconds(), 1) if q.ts_source else None

    def quote_view(self, q: Quote) -> dict[str, Any]:
        inst = self.instruments.get(q.symbol)
        f = self.freshness(q.symbol)
        return {"symbol": q.symbol, "bid": q.bid, "ask": q.ask, "mid": q.mid, "spread": q.spread,
                "ts": (q.ts_source or q.ts_recv).isoformat() + "Z", "recv": q.ts_recv.isoformat() + "Z",
                "lag_s": self.source_lag(q), "freshness": f["status"], "age_s": f["age_s"], "market_state": self.states.get(q.symbol, q.market_state),
                "digits": inst.digits if inst else None}

    def instrument_view(self, symbol: str) -> dict[str, Any] | None:
        inst = self.instruments.get(symbol)
        if inst is None:
            return None
        d = inst.to_dict()
        d["profile"] = session_profile(inst.asset_class, inst.exchange, inst.symbol)
        d["qty_step"] = spec_for(inst.asset_class).qty_step
        q = self.quotes.get(symbol)
        d["quote"] = self.quote_view(q) if q else None
        return d

    def info(self, symbol: str) -> Instrument:
        """Instrument connu, ou classé à la volée si le catalogue n'est pas (encore) chargé."""
        inst = self.instruments.get(symbol)
        if inst:
            return inst
        cls, base, quote = classify(symbol)
        return Instrument(symbol=symbol, asset_class=cls, base_ccy=base, quote_ccy=quote)

    def fx_to_usd(self, ccy: str | None) -> float | None:
        """Taux de conversion devise de cotation -> USD, depuis les cotations du flux. None si indisponible."""
        if not ccy:
            return None
        if ccy == "USD":
            return 1.0
        for sym, inv in ((f"{ccy}USD", False), (f"USD{ccy}", True)):
            q = self.quotes.get(sym)
            if q and self.freshness(sym).get("hard_stale") is False and q.mid > 0:
                return (1.0 / q.mid) if inv else q.mid
        for sym, inv in ((f"{ccy}USD", False), (f"USD{ccy}", True)):  # dernier recours : cotation, même ancienne
            q = self.quotes.get(sym)
            if q and q.mid > 0:
                return (1.0 / q.mid) if inv else q.mid
        return None

    # ------------------------------------------------------------------ bougies
    async def get_bars(self, symbol: str, tf: str = "5m", limit: int = 500) -> list[Bar]:
        if tf not in INTERVALS:
            raise ProviderError(f"unité de temps inconnue: {tf}")
        symbol = symbol.upper()
        hist = await self._history(symbol, tf, limit)
        if tf in LIVE_TFS:
            if not self.bars.has_seed(symbol, tf):
                self.bars.seed(symbol, tf, hist)
            return merge(hist, self.bars.live_bars(symbol, tf), limit)
        # unités plus larges : bougie courante reconstruite depuis les bougies 1m live
        live = aggregate(self.bars.live_bars(symbol, "1m"), INTERVALS[tf])
        return merge(hist, live[-1:], limit)

    async def _history(self, symbol: str, tf: str, limit: int) -> list[Bar]:
        key = (symbol, tf)
        ttl = 20.0 if INTERVALS[tf] <= 300 else 60.0
        cached = self._hist_cache.get(key)
        if cached and time.monotonic() - cached[0] < ttl:
            _, rows, asked = cached
            # réutilisable si la requête précédente demandait au moins autant, ou si la source n'avait déjà plus rien de plus
            if limit <= asked or len(rows) < asked:
                return rows[-limit:]
        asked = max(limit, 100)
        bars = await self.provider.history(symbol, tf, limit=asked)
        self._hist_cache[key] = (time.monotonic(), bars, asked)
        if len(self._hist_cache) > 800:
            self._hist_cache.pop(next(iter(self._hist_cache)))
        return bars[-limit:]

    # ------------------------------------------------------------------ santé
    def health(self) -> dict[str, Any]:
        st = self.provider.stream_status()
        total = len(self.instruments)
        live = sum(1 for s in self.quotes if self.freshness(s)["status"] == "live")
        stale = sum(1 for s in self.quotes if self.freshness(s)["status"] == "stale")
        closed = sum(1 for s in self.quotes if self.freshness(s)["status"] == "closed")
        lags = sorted(l for l in (self.source_lag(q) for q in self.quotes.values() if q.age_at_recv_s == 0 and self.freshness(q.symbol)["status"] == "live") if l is not None)
        return {"provider": self.provider.name, "stream": st, "source_lag_median_s": lags[len(lags) // 2] if lags else None, "instruments": total, "quotes": len(self.quotes),
                "live": live, "stale": stale, "closed": closed, "catalogue_error": self.catalogue_error,
                "catalogue_loaded_at": self.instruments_loaded_at.isoformat() + "Z" if self.instruments_loaded_at else None,
                "subscribe_mode": self.subscribe_mode, "live_threshold_s": self.live_s}
