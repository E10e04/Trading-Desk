"""Source crypto : Kraken (données publiques, sans compte ni clé API, gratuites).

Pourquoi : les cryptos de BiQuote sont des CFD d'un courtier MetaTrader 5, pas le prix du marché. Kraken donne le prix
réel du marché (paires en dollar : BTC/USD), proche de ce que TradingView affiche.

Faits établis à partir de la documentation Kraken :
  * flux WebSocket v2 public  : `wss://ws.kraken.com/v2`, canal `ticker` (sans authentification), symboles `BTC/USD`,
    plusieurs symboles par message d'abonnement, `event_trigger: "bbo"` = mise à jour à chaque changement du meilleur prix ;
  * Cloudflare : ~150 tentatives de connexion par 10 min et par IP, sinon bannissement de 10 min -> pauses de 5 s au moins ;
  * connexion inactive ~1 min fermée ; battements de cœur (heartbeat) ~1/s une fois abonné ;
  * REST public : /0/public/AssetPairs, /0/public/Ticker?pair=A,B, /0/public/OHLC?pair=&interval= (720 bougies max).
Le REST n'est utilisé que pour le catalogue, l'historique des bougies et un rafraîchissement des paires peu actives ;
il passe par un limiteur (>= 1,1 s entre deux appels) pour ne jamais approcher les limites de Kraken.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import random
import ssl
import time
from typing import Any

import httpx
from websockets.asyncio.client import connect as ws_connect

from .provider import Bar, Instrument, MarketDataProvider, ProviderError, Quote, StateHandler, TickHandler, parse_ts

log = logging.getLogger(__name__)
INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
ALIAS = {"XBT": "BTC", "XDG": "DOGE"}
NOT_CRYPTO = {"USDT", "USDC", "DAI", "PYUSD", "USDG", "TUSD", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "EURT", "EURR", "USD", "RLUSD", "FDUSD"}
NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP", "ADA": "Cardano", "DOGE": "Dogecoin", "LTC": "Litecoin",
         "DOT": "Polkadot", "LINK": "Chainlink", "AVAX": "Avalanche", "BCH": "Bitcoin Cash", "XLM": "Stellar", "ATOM": "Cosmos"}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover
        return ssl.create_default_context()


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 and x != float("inf") else None


def norm_base(b: str) -> str:
    b = b.upper()
    return ALIAS.get(b, b)


class KrakenProvider(MarketDataProvider):
    name = "kraken"
    WS_URL = "wss://ws.kraken.com/v2"
    REST_URL = "https://api.kraken.com"

    def __init__(self, ws_url: str | None = None, rest_url: str | None = None, *, top_n: int = 40,
                 http_transport: httpx.AsyncBaseTransport | None = None, min_rest_gap: float = 1.1,
                 reconnect_min: float = 5.0, reconnect_max: float = 120.0, refresh_after: float = 20.0,
                 refresh_every: float = 15.0, ssl_context: Any = "auto") -> None:
        self.ws_url = ws_url or self.WS_URL
        self.top_n = top_n
        self._http = httpx.AsyncClient(base_url=(rest_url or self.REST_URL).rstrip("/"), timeout=15, transport=http_transport,
                                       headers={"User-Agent": "TradingDesk/0.2 (paper-trading)"})
        self._min_gap = min_rest_gap
        self._rest_lock = asyncio.Lock()
        self._last_rest = 0.0
        self._rmin, self._rmax = reconnect_min, reconnect_max
        self._refresh_after, self._refresh_every = refresh_after, refresh_every
        self._ssl = ssl_context
        self.pairs: dict[str, dict[str, str]] = {}   # SYMBOLE -> {alt, ws, rest}
        self._by_ws: dict[str, str] = {}              # "BTC/USD" -> SYMBOLE
        self._symbols: set[str] = set()
        self._last_seen: dict[str, float] = {}
        self.unavailable: dict[str, str] = {}
        self.clock_skew_s = 0.0
        self._on_tick: TickHandler | None = None
        self._on_state: StateHandler | None = None
        self._tasks: list[asyncio.Task] = []
        self._ws: Any = None
        self._stop = asyncio.Event()
        self._connected = False
        self._subscribed: set[str] = set()
        self.status: dict[str, Any] = {"state": "disconnected", "connected_since": None, "last_tick_at": None, "ticks_total": 0,
                                       "ticks_invalid": 0, "reconnects": 0, "last_error": None, "mode": "stream", "rest_calls": 0}
        self._rate: list[float] = []

    # ------------------------------------------------------------------ REST (limité)
    async def _get(self, path: str, params: dict | None = None) -> Any:
        async with self._rest_lock:  # un appel à la fois, espacés d'au moins `min_rest_gap` s
            wait = self._min_gap - (time.monotonic() - self._last_rest)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                r = await self._http.get(path, params=params)
            finally:
                self._last_rest = time.monotonic()
                self.status["rest_calls"] += 1
        self._update_skew(r)
        if r.status_code == 429:
            raise ProviderError("Kraken : trop de requêtes (429), pause avant de réessayer")
        if r.status_code >= 400:
            raise ProviderError(f"Kraken {r.status_code} sur {path}")
        data = r.json()
        if data.get("error"):
            raise ProviderError(f"Kraken : {data['error']}")
        return data["result"]

    def _update_skew(self, r: httpx.Response) -> None:
        d = r.headers.get("date")
        if not d:
            return
        try:
            from email.utils import parsedate_to_datetime
            server = parsedate_to_datetime(d).astimezone(dt.timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            return
        skew = (utcnow() - server).total_seconds()
        if abs(skew) <= 3600:
            self.clock_skew_s = skew if self.clock_skew_s == 0.0 else 0.8 * self.clock_skew_s + 0.2 * skew

    async def list_instruments(self) -> list[Instrument]:
        """Paires en dollar en ligne, hors stablecoins/devises, limitées aux `top_n` plus échangées sur 24 h."""
        res = await self._get("/0/public/AssetPairs")
        cand: dict[str, dict[str, Any]] = {}
        for rest_name, p in res.items():
            if not isinstance(p, dict) or rest_name.endswith(".d") or p.get("status", "online") != "online":
                continue
            ws = str(p.get("wsname") or "")
            if "/" not in ws or ws.split("/")[1] != "USD":
                continue
            base = norm_base(ws.split("/")[0])
            if base in NOT_CRYPTO:
                continue
            cand[f"{base}USD"] = {"alt": str(p.get("altname") or rest_name), "ws": f"{base}/USD", "rest": rest_name,
                                  "digits": int(p.get("pair_decimals") or 2), "tick": _f(p.get("tick_size")), "base": base}
        volume: dict[str, float] = {}
        try:
            names = sorted(v["alt"] for v in cand.values())
            for i in range(0, len(names), 100):
                tick = await self._get("/0/public/Ticker", {"pair": ",".join(names[i:i + 100])})
                for sym, v in cand.items():
                    t = tick.get(v["rest"]) or tick.get(v["alt"])
                    if t:
                        vol, vw = _f((t.get("v") or [None, None])[1]), _f((t.get("p") or [None, None])[1])
                        volume[sym] = (vol or 0.0) * (vw or 0.0)  # volume 24 h en dollars
        except ProviderError as e:
            log.info("classement par volume impossible (%s) : ordre alphabétique", e)
        keep = sorted(cand, key=lambda s: (-volume.get(s, 0.0), s))[: self.top_n]
        self.pairs = {s: cand[s] for s in keep}
        self._by_ws = {v["ws"]: s for s, v in self.pairs.items()}
        out = []
        for s in keep:
            v = cand[s]
            out.append(Instrument(symbol=s, description=f"{NAMES.get(v['base'], v['base'])} / US Dollar", asset_class="crypto",
                                  exchange="Kraken", digits=v["digits"], tick_size=v["tick"], base_ccy=v["base"], quote_ccy="USD",
                                  live=True, raw_type="crypto"))
        return out

    def _mkquote(self, sym: str, bid: Any, ask: Any, last: Any, ts: Any, now: dt.datetime | None = None) -> Quote | None:
        bid_f, ask_f, last_f = _f(bid), _f(ask), _f(last)
        if bid_f is not None and ask_f is not None and bid_f > ask_f:
            bid_f = ask_f = None
        mid = (bid_f + ask_f) / 2 if bid_f is not None and ask_f is not None else last_f
        if mid is None:
            return None
        recv = now or utcnow()
        ts_s = parse_ts(ts)
        age = min(max((recv - ts_s).total_seconds() - self.clock_skew_s, 0.0), 1e7) if ts_s else 0.0
        return Quote(sym, bid_f, ask_f, mid, ts_s, recv, "open", self.name, age)  # la crypto cote 24 h/24

    async def latest(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        known = [s.upper() for s in symbols if s.upper() in self.pairs]
        for i in range(0, len(known), 50):
            chunk = known[i:i + 50]
            res = await self._get("/0/public/Ticker", {"pair": ",".join(self.pairs[s]["alt"] for s in chunk)})
            for s in chunk:
                t = res.get(self.pairs[s]["rest"]) or res.get(self.pairs[s]["alt"])
                if isinstance(t, dict):
                    q = self._mkquote(s, (t.get("b") or [None])[0], (t.get("a") or [None])[0], (t.get("c") or [None])[0], None)
                    if q:
                        out[s] = q
        return out

    async def history(self, symbol: str, interval: str = "5m", limit: int = 500,
                      from_: dt.datetime | None = None, to: dt.datetime | None = None) -> list[Bar]:
        if interval not in INTERVALS:
            raise ProviderError(f"intervalle inconnu: {interval}")
        p = self.pairs.get(symbol.upper())
        if not p:
            raise ProviderError(f"{symbol} : paire inconnue de Kraken")
        params: dict[str, Any] = {"pair": p["alt"], "interval": INTERVALS[interval]}
        if from_:
            params["since"] = int(from_.replace(tzinfo=dt.timezone.utc).timestamp())
        res = await self._get("/0/public/OHLC", params)
        rows = next((v for k, v in res.items() if k != "last" and isinstance(v, list)), [])
        bars: dict[int, Bar] = {}
        for r in rows:
            try:
                ts, o, h, l, c = int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])
            except (TypeError, ValueError, IndexError):
                continue
            if min(o, h, l, c) <= 0 or h < l:
                continue
            bars[ts] = Bar(ts=ts, o=o, h=max(h, o, c), l=min(l, o, c), c=c, v=float(r[6]) if len(r) > 6 else 0.0)
        out = [bars[k] for k in sorted(bars)]
        if out:  # la dernière bougie renvoyée par Kraken est la bougie en cours
            out[-1].closed = False
        return out[-limit:]

    # ------------------------------------------------------------------ flux
    async def start_stream(self, on_tick: TickHandler, on_state: StateHandler | None = None) -> None:
        if self._tasks:
            return
        self._on_tick, self._on_state = on_tick, on_state
        self._stop.clear()
        self._tasks = [asyncio.create_task(self._supervise(), name="kraken-stream"),
                       asyncio.create_task(self._refresh_loop(), name="kraken-refresh")]

    async def subscribe(self, symbols: list[str]) -> None:
        new = {s.upper() for s in symbols if s.upper() in self.pairs} - self._symbols
        self._symbols |= new
        if self._connected and new:
            await self._send_subscribe(sorted(new))

    async def _send_subscribe(self, syms: list[str]) -> None:
        ws = self._ws
        if ws is None:
            return
        for i in range(0, len(syms), 100):
            chunk = syms[i:i + 100]
            await ws.send(json.dumps({"method": "subscribe", "params": {"channel": "ticker", "snapshot": True, "event_trigger": "bbo",
                                                                          "symbol": [self.pairs[s]["ws"] for s in chunk]}}))
            self._subscribed.update(chunk)

    async def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("method") == "subscribe" and msg.get("success") is False:
            err = str(msg.get("error") or "abonnement refusé")
            for ws_sym, sym in self._by_ws.items():
                if ws_sym in err:
                    self.unavailable[sym] = err[:200]
                    self._subscribed.discard(sym)
            return
        if msg.get("channel") != "ticker":
            return
        for row in msg.get("data") or []:
            sym = self._by_ws.get(str(row.get("symbol", "")))
            q = self._mkquote(sym, row.get("bid"), row.get("ask"), row.get("last"), row.get("timestamp")) if sym else None
            if q is None:
                self.status["ticks_invalid"] += 1
                continue
            now = time.monotonic()
            self._last_seen[sym] = now
            self._rate = (self._rate + [now])[-1000:]
            self.status["ticks_total"] += 1
            self.status["last_tick_at"] = q.ts_recv.isoformat() + "Z"
            self.unavailable.pop(sym, None)
            if self._on_tick:
                r = self._on_tick(q)
                if asyncio.iscoroutine(r):
                    await r

    async def _supervise(self) -> None:
        backoff, attempt = self._rmin, 0
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.status["state"] = "connecting"
                kw: dict[str, Any] = {}
                if self.ws_url.startswith("wss://"):
                    kw["ssl"] = _ssl_context() if self._ssl == "auto" else self._ssl
                async with ws_connect(self.ws_url, open_timeout=10, ping_interval=None, max_size=None, close_timeout=2, **kw) as ws:
                    self._ws, self._connected = ws, True
                    self.status.update(state="connected", connected_since=utcnow().isoformat() + "Z", last_error=None)
                    if attempt:
                        self.status["reconnects"] += 1
                    attempt += 1
                    self._subscribed.clear()
                    if self._symbols:
                        await self._send_subscribe(sorted(self._symbols))
                    last_ping = time.monotonic()
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            raise ConnectionError("aucun message de Kraken depuis 30 s") from None
                        await self._handle(raw if isinstance(raw, str) else raw.decode())
                        if time.monotonic() - last_ping > 30:
                            await ws.send(json.dumps({"method": "ping"}))
                            last_ping = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.status["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                attempt += 1
            finally:
                self._ws, self._connected = None, False
                self.status["state"] = "reconnecting" if not self._stop.is_set() else "disconnected"
                self.status["connected_since"] = None
            if self._stop.is_set():
                break
            if time.monotonic() - t0 > 60:
                backoff = self._rmin
            delay = max(self._rmin, min(backoff, self._rmax)) * (0.9 + random.random() * 0.3)  # jamais sous le plancher de 5 s
            log.warning("flux Kraken interrompu (%s) — nouvelle tentative dans %.1fs", self.status.get("last_error"), delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self._rmax)
        self.status["state"] = "disconnected"

    async def _refresh_loop(self) -> None:
        """Rafraîchit par REST (un seul appel pour 50 paires) les paires peu actives, ou toutes si le flux est coupé, afin qu'un
        prix valide mais calme ne soit pas marqué « périmé »."""
        while not self._stop.is_set():
            try:
                await asyncio.sleep(self._refresh_every if self._connected else min(self._refresh_every, 5.0))
                if not self._symbols:
                    continue
                now = time.monotonic()
                due = sorted(s for s in self._symbols if not self._connected or now - self._last_seen.get(s, 0.0) > self._refresh_after)
                self.status["mode"] = "stream" if self._connected else "polling"
                if not due:
                    continue
                for q in (await self.latest(due)).values():
                    self._last_seen[q.symbol] = time.monotonic()
                    if self._on_tick:
                        r = self._on_tick(q)
                        if asyncio.iscoroutine(r):
                            await r
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.status["last_poll_error"] = f"{type(e).__name__}: {str(e)[:160]}"

    async def stop_stream(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        self.status["state"] = "disconnected"

    def stream_status(self) -> dict[str, Any]:
        now = time.monotonic()
        tps = round(sum(1 for t in self._rate if now - t <= 10) / 10, 2)
        return {**self.status, "subscribed": len(self._subscribed), "requested": len(self._symbols), "ticks_per_second": tps,
                "unavailable": dict(list(self.unavailable.items())[:20]), "hub": self.ws_url}

    async def diagnose(self) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []

        def add(n: str, ok: bool, d: str) -> None:
            steps.append({"step": n, "ok": ok, "detail": d[:300]})

        try:
            insts = await self.list_instruments()
            add("Kraken · catalogue", bool(insts), f"{len(insts)} paires en dollar")
            q = (await self.latest(["BTCUSD"])).get("BTCUSD")
            add("Kraken · prix BTC/USD (REST)", q is not None, f"{q.mid}" if q else "indisponible")
        except Exception as e:  # noqa: BLE001
            add("Kraken · REST", False, f"{type(e).__name__}: {e}")
            return steps
        got: list[Quote] = []
        probe = KrakenProvider(self.ws_url, str(self._http.base_url), top_n=self.top_n, ssl_context=self._ssl)
        probe.pairs, probe._by_ws = self.pairs, self._by_ws
        try:
            await probe.start_stream(lambda q: got.append(q))
            await probe.subscribe(["BTCUSD"])
            for _ in range(100):
                if got:
                    break
                await asyncio.sleep(0.1)
            err = probe.status.get("last_error")
            add("Kraken · flux WebSocket", bool(got), f"tick BTC/USD reçu : {got[0].mid}" if got else f"aucun tick en 10 s ({err or 'pas d erreur'})")
        finally:
            await probe.stop_stream()
            await probe._http.aclose()
        return steps

    async def aclose(self) -> None:
        await self.stop_stream()
        await self._http.aclose()
