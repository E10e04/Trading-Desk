"""Fournisseur BiQuote (https://biquote.io) : REST + flux SignalR.

Faits établis à partir du SDK officiel (halitcanbaba/biquote-sdk, v0.3.0) :
  * hub SignalR  : `<base>/hubs/tick` ; méthode serveur `Subscribe(symbol)` (UN symbole par appel) ;
                   événements `ReceiveTick` et `ReceiveSubscriptionState` ;
  * REST         : /api/symbols, /api/latest (<=100 symboles), /api/{sym}/ohlc (1m 5m 15m 30m 1h 4h 1d, ~1 an),
                   /api/news/{market|crypto|...}, /api/calendar ;
  * `last` et `volume` valent toujours 0 (CFD) -> le prix est `mid` ; `marketState` / `quoteAgeSeconds` sont fournis.
Les champs exacts d'un tick au-delà de `symbol` et `mid` ne sont pas documentés dans le SDK : le parseur est
tolérant (clés insensibles à la casse, alias) et rejette tout tick invalide.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import random
import time
from typing import Any

import httpx

from ..config import get_settings
from .instruments import build_instrument
from .provider import (Bar, Instrument, MarketDataProvider, ProviderError, Quote, StateHandler, TickHandler,
                       parse_ts)
from .signalr import SignalRClient, SignalRError

log = logging.getLogger(__name__)
INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _pick(d: dict, *names: str) -> Any:
    low = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] is not None:
            return low[n.lower()]
    return None


def normalize_tick(raw: Any, provider: str = "biquote", now: dt.datetime | None = None) -> Quote | None:
    """Valide et normalise un tick brut. Retourne None si le tick est inutilisable."""
    if not isinstance(raw, dict):
        return None
    sym = _pick(raw, "symbol", "s", "ticker", "name")
    if not isinstance(sym, str) or not sym.strip():
        return None
    sym = sym.strip().upper()
    bid, ask, mid = _num(_pick(raw, "bid", "b")), _num(_pick(raw, "ask", "a", "offer")), _num(_pick(raw, "mid", "price", "m"))
    if bid is not None and ask is not None and bid > ask:  # spread inversé : anomalie de données
        bid = ask = None
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / 2
    if mid is None:
        return None
    if bid is not None and ask is not None and not (bid <= mid <= ask or abs(mid - (bid + ask) / 2) / mid < 0.01):
        return None  # mid incohérent avec bid/ask
    state = str(_pick(raw, "marketState", "state") or "unknown").lower()
    if state not in ("open", "closed"):
        state = "unknown"
    age = _pick(raw, "quoteAgeSeconds", "ageSeconds")
    try:
        age_f = min(max(float(age), 0.0), 1e7) if age is not None else 0.0
    except (TypeError, ValueError):
        age_f = 0.0
    return Quote(symbol=sym, bid=bid, ask=ask, mid=mid,
                 ts_source=parse_ts(_pick(raw, "timestamp", "time", "ts", "lastQuoteAt", "t")),
                 ts_recv=now or utcnow(), market_state=state, provider=provider, age_at_recv_s=age_f)


def parse_bars(payload: Any) -> list[Bar]:
    """`/api/{sym}/ohlc` -> {"bars": [...]} (le plus récent d'abord d'après le SDK). Tri chronologique garanti."""
    rows = payload.get("bars") if isinstance(payload, dict) else payload
    out: list[Bar] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ts = parse_ts(_pick(r, "t", "time", "timestamp", "openTime", "date", "ts"))
        o, h, l, c = (_num(_pick(r, *k)) for k in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")))
        if ts is None or None in (o, h, l, c) or h < l:
            continue
        vol = _pick(r, "v", "volume", "tickVolume", "ticks")
        try:
            vol_f = float(vol) if vol is not None else 0.0
        except (TypeError, ValueError):
            vol_f = 0.0
        out.append(Bar(ts=int(ts.replace(tzinfo=dt.timezone.utc).timestamp()), o=o, h=max(h, o, c), l=min(l, o, c), c=c, v=vol_f))
    out.sort(key=lambda b: b.ts)
    dedup: dict[int, Bar] = {b.ts: b for b in out}
    return [dedup[k] for k in sorted(dedup)]


class BiquoteProvider(MarketDataProvider):
    name = "biquote"

    def __init__(self, base_url: str | None = None, hub_path: str | None = None, api_key: str | None = None, *,
                 http_transport: httpx.AsyncBaseTransport | None = None, signalr_kwargs: dict | None = None,
                 timeout: float = 10.0, backoff_max: float = 30.0, sub_concurrency: int = 20) -> None:
        s = get_settings()
        self.base_url = (base_url or s.biquote_base_url).rstrip("/")
        self.hub_path = hub_path or s.biquote_hub_path
        key = api_key if api_key is not None else s.biquote_api_key
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TradingDesk/0.2; paper-trading)"}
        if key:  # facultatif : les endpoints publics de lecture n'en demandent pas
            headers["X-API-Key"] = key
        self._headers = headers
        self._transport = http_transport
        self._signalr_kwargs = signalr_kwargs or {}
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, headers=headers, transport=http_transport)
        self._backoff_max = backoff_max
        self._sub_sem = asyncio.Semaphore(sub_concurrency)
        # état du flux
        self._symbols: set[str] = set()
        self._subscribed: set[str] = set()
        self._batch_ok: bool | None = None
        self.unavailable: dict[str, str] = {}
        self._sub_states: dict[str, str] = {}
        self._on_tick: TickHandler | None = None
        self._on_state: StateHandler | None = None
        self._task: asyncio.Task | None = None
        self._client: SignalRClient | None = None
        self._stop = asyncio.Event()
        self.status: dict[str, Any] = {
            "state": "disconnected", "connected_since": None, "last_tick_at": None, "ticks_total": 0, "ticks_invalid": 0,
            "reconnects": 0, "last_error": None, "last_disconnect_reason": None, "mode": "stream",
        }
        self._poll_task: asyncio.Task | None = None
        self._poll_seen: dict[str, Any] = {}
        self.poll_interval = 5.0
        self.poll_after = 12.0  # secondes sans flux avant de basculer sur le REST
        self._rate_window: list[float] = []

    # ------------------------------------------------------------------ REST
    async def _get(self, path: str, params: Any = None, retries: int = 3) -> Any:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                r = await self._http.get(path, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    raise ProviderError(f"BiQuote {r.status_code} sur {path}")
                if r.status_code >= 400:
                    raise ProviderError(f"BiQuote {r.status_code} sur {path}: {r.text[:200]}")
                return r.json()
            except (httpx.HTTPError, ProviderError, ValueError) as e:
                last = e
                if isinstance(e, ProviderError) and "BiQuote 4" in str(e) and "429" not in str(e):
                    break  # erreur client définitive (symbole inconnu...)
                await asyncio.sleep(min(0.5 * 2 ** attempt, 4) + random.random() * 0.2)
        raise ProviderError(f"BiQuote injoignable ({path}): {last}") from last

    async def list_instruments(self) -> list[Instrument]:
        rows = await self._get("/api/symbols", params={"quotedWithinDays": 7})
        if isinstance(rows, dict):
            rows = rows.get("symbols") or rows.get("items") or []
        insts = [i for i in (build_instrument(r) for r in rows if isinstance(r, dict)) if i]
        try:  # symboles actifs à cet instant (best effort)
            live = await self._get("/api/active", retries=1)
            live_syms = {str(_pick(r, "symbol", "name") or "").upper() for r in (live if isinstance(live, list) else [])}
            for i in insts:
                i.live = i.symbol in live_syms
        except ProviderError:
            pass
        return insts

    async def latest(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for i in range(0, len(symbols), 100):
            chunk = [s.upper() for s in symbols[i:i + 100]]
            params = [("symbols", s) for s in chunk] + [("allowStale", "true")]
            data = await self._get("/api/latest", params=params)
            rows = data.items() if isinstance(data, dict) else ((None, r) for r in data)
            for key, raw in rows:
                if isinstance(raw, dict) and "symbol" not in {k.lower() for k in raw} and key:
                    raw = {**raw, "symbol": key}
                q = normalize_tick(raw, self.name)
                if q:
                    out[q.symbol] = q
        return out

    async def history(self, symbol: str, interval: str = "5m", limit: int = 500,
                      from_: dt.datetime | None = None, to: dt.datetime | None = None) -> list[Bar]:
        if interval not in INTERVALS:
            raise ProviderError(f"intervalle inconnu: {interval}")
        params: dict[str, Any] = {"interval": interval, "limit": limit}
        if from_:
            params["from"] = from_.strftime("%Y-%m-%dT%H:%M:%SZ")
        if to:
            params["to"] = to.strftime("%Y-%m-%dT%H:%M:%SZ")
        return parse_bars(await self._get(f"/api/{symbol.upper()}/ohlc", params=params))

    async def news(self, kind: str = "market", **params: Any) -> list[dict]:
        path = "/api/news/search" if kind == "search" else f"/api/news/{kind}"
        data = await self._get(path, params=params or None, retries=2)
        return data if isinstance(data, list) else (data.get("items") or data.get("news") or data.get("articles") or [])

    async def calendar_upcoming(self, **params: Any) -> list[dict]:
        data = await self._get("/api/calendar/upcoming", params=params or None, retries=2)
        return data if isinstance(data, list) else (data.get("events") or data.get("items") or [])

    # ------------------------------------------------------------------ flux
    async def start_stream(self, on_tick: TickHandler, on_state: StateHandler | None = None) -> None:
        if self._task and not self._task.done():
            return
        self._on_tick, self._on_state = on_tick, on_state
        self._stop.clear()
        self._task = asyncio.create_task(self._supervise(), name="biquote-stream")
        self._poll_task = asyncio.create_task(self._poll_fallback(), name="biquote-poll")

    async def _poll_fallback(self) -> None:
        """Secours : si le flux SignalR n'est pas connecté (ou muet) depuis `poll_after` s, on lit `/api/latest` en REST
        (lots de 100 symboles, bien sous la limite de 15 000 requêtes/minute) pour que les prix continuent d'arriver.
        Dès que le flux fonctionne à nouveau, le REST s'arrête."""
        down_since: float | None = None
        while not self._stop.is_set():
            try:
                await asyncio.sleep(self.poll_interval)
                streaming = self._client is not None and self._client.state == "connected" and (
                    self.status["ticks_total"] > 0 and time.monotonic() - (self._rate_window[-1] if self._rate_window else 0) < 60)
                if streaming or not self._symbols:
                    down_since = None
                    self.status["mode"] = "stream"
                    continue
                down_since = down_since or time.monotonic()
                if time.monotonic() - down_since < self.poll_after:
                    continue
                self.status["mode"] = "polling"
                syms = sorted(self._symbols)
                got: dict[str, Quote] = {}
                for i in range(0, len(syms), 100):
                    got.update(await self.latest(syms[i:i + 100]))
                for sym, q in got.items():
                    key = (q.ts_source, q.mid)
                    if self._poll_seen.get(sym) == key:
                        continue
                    self._poll_seen[sym] = key
                    self.status["ticks_total"] += 1
                    self.status["last_tick_at"] = q.ts_recv.isoformat() + "Z"
                    if self._on_tick:
                        r = self._on_tick(q)
                        if asyncio.iscoroutine(r):
                            await r
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status["last_poll_error"] = f"{type(e).__name__}: {str(e)[:160]}"

    async def subscribe(self, symbols: list[str]) -> None:
        new = {s.upper() for s in symbols} - self._symbols
        self._symbols |= new
        if self._client and self._client.state == "connected" and new:
            await self._subscribe_many(sorted(new))

    BATCH = 50

    async def _subscribe_many(self, symbols: list[str]) -> None:
        """Documentation BiQuote : `Subscribe(symbols: string[])`. On abonne par lots ; si le serveur refuse un lot (symbole
        inconnu, ou ancienne version qui n'accepte qu'un symbole), on retombe sur un abonnement symbole par symbole,
        ce qui isole les symboles indisponibles sans perdre les autres."""
        async def one(sym: str) -> None:
            async with self._sub_sem:
                c = self._client
                if not c or c.state != "connected":
                    return
                try:
                    await c.invoke("Subscribe", sym, timeout=15)
                    self._subscribed.add(sym)
                    self.unavailable.pop(sym, None)
                except SignalRError as e:
                    self.unavailable[sym] = str(e)[:200]

        async def batch(chunk: list[str]) -> None:
            c = self._client
            if not c or c.state != "connected":
                return
            if self._batch_ok is not False:
                async with self._sub_sem:
                    try:
                        await c.invoke("Subscribe", chunk, timeout=20)
                        self._subscribed.update(chunk)
                        for sym in chunk:
                            self.unavailable.pop(sym, None)
                        self._batch_ok = True
                        return
                    except SignalRError as e:
                        log.info("abonnement par lot refusé (%s) : repli symbole par symbole", str(e)[:120])
            await asyncio.gather(*(one(s) for s in chunk))

        await asyncio.gather(*(batch(symbols[i:i + self.BATCH]) for i in range(0, len(symbols), self.BATCH)))

    async def _handle_tick(self, *args: Any) -> None:
        rows: list[Any] = []
        for a in args:
            rows.extend(a if isinstance(a, list) else [a])
        for raw in rows:
            q = normalize_tick(raw, self.name)
            if q is None:
                self.status["ticks_invalid"] += 1
                continue
            self.status["ticks_total"] += 1
            self.status["last_tick_at"] = q.ts_recv.isoformat() + "Z"
            now = time.monotonic()
            self._rate_window.append(now)
            if len(self._rate_window) > 2000:
                self._rate_window = self._rate_window[-1000:]
            if self._on_tick:
                r = self._on_tick(q)
                if asyncio.iscoroutine(r):
                    await r

    async def _handle_state(self, *args: Any) -> None:
        """`ReceiveSubscriptionState` : open | closed | unknown (« unknown » = symbole inconnu)."""
        sym, state = None, None
        if len(args) == 1 and isinstance(args[0], dict):
            sym = _pick(args[0], "symbol", "s")
            state = _pick(args[0], "state", "marketState", "status")
        elif len(args) >= 2:
            sym, state = args[0], args[1]
        if not sym:
            return
        sym, state = str(sym).upper(), str(state or "unknown").lower()
        self._sub_states[sym] = state
        if state == "unknown":
            self.unavailable[sym] = "symbole inconnu du fournisseur"
        if self._on_state:
            r = self._on_state(sym, state)
            if asyncio.iscoroutine(r):
                await r

    def ticks_per_second(self, window: float = 10.0) -> float:
        now = time.monotonic()
        n = sum(1 for t in self._rate_window if now - t <= window)
        return round(n / window, 2)

    async def _supervise(self) -> None:
        backoff, attempt = 1.0, 0
        while not self._stop.is_set():
            client = SignalRClient(self.base_url, self.hub_path, headers=self._headers, http_transport=self._transport,
                                   **self._signalr_kwargs)
            client.on("ReceiveTick", self._handle_tick)
            client.on("ReceiveSubscriptionState", self._handle_state)  # évite l'avertissement « méthode cliente absente »
            self._client = client
            self.status["state"] = "connecting"
            t0 = time.monotonic()
            try:
                await client.connect()
                self.status.update(state="connected", connected_since=utcnow().isoformat() + "Z", last_error=None)
                self._subscribed.clear()
                if attempt:
                    self.status["reconnects"] += 1
                attempt += 1
                await self._subscribe_many(sorted(self._symbols))
                await client.wait_closed()
                self.status["last_disconnect_reason"] = client.close_reason
            except asyncio.CancelledError:
                await client.close()
                raise
            except Exception as e:
                self.status["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                attempt += 1
            finally:
                self.status["state"] = "reconnecting" if not self._stop.is_set() else "disconnected"
                self.status["connected_since"] = None
                await client.close()
            if self._stop.is_set():
                break
            if time.monotonic() - t0 > 30:  # connexion stable -> on repart de zéro
                backoff = 1.0
            delay = min(backoff, self._backoff_max) * (0.8 + random.random() * 0.4)
            log.warning("flux BiQuote interrompu (%s) — nouvelle tentative dans %.1fs",
                        self.status.get("last_error") or self.status.get("last_disconnect_reason"), delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self._backoff_max)
        self.status["state"] = "disconnected"

    async def stop_stream(self) -> None:
        self._stop.set()
        if self._client:
            await self._client.close()
        for attr in ("_task", "_poll_task"):
            t = getattr(self, attr)
            if t:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, attr, None)
        self.status["state"] = "disconnected"

    async def diagnose(self) -> list[dict[str, Any]]:
        """Teste chaque étape séparément (REST, négociation, WebSocket, abonnement, réception) et dit laquelle échoue."""
        steps: list[dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str) -> None:
            steps.append({"step": name, "ok": ok, "detail": detail[:300]})

        try:
            r = await self._http.get("/api/EURUSD", timeout=10)
            add("REST /api/EURUSD", r.status_code == 200, f"HTTP {r.status_code}" + (f" — mid {r.json().get('mid')}" if r.status_code == 200 else f" — {r.text[:120]}"))
        except Exception as e:  # noqa: BLE001
            add("REST /api/EURUSD", False, f"{type(e).__name__}: {e}")
        try:
            rows = await self._get("/api/symbols", retries=1)
            n = len(rows if isinstance(rows, list) else (rows.get("symbols") or rows.get("items") or []))
            add("Catalogue /api/symbols", n > 0, f"{n} symboles")
        except Exception as e:  # noqa: BLE001
            add("Catalogue /api/symbols", False, str(e))
        c = SignalRClient(self.base_url, self.hub_path, headers=self._headers, http_transport=self._transport, **self._signalr_kwargs)
        got: list[Any] = []
        c.on("ReceiveTick", lambda *a: got.append(a))
        try:
            try:
                await c._negotiate()
                add("Négociation SignalR", True, f"transports : {[t.get('transport') for t in c.negotiated.get('availableTransports', [])] or 'non précisés'}")
            except Exception as e:  # noqa: BLE001
                add("Négociation SignalR", False, f"{type(e).__name__}: {e}")
            await c.connect()
            add("WebSocket + handshake", True, "connecté")
            await c.invoke("Subscribe", ["EURUSD"], timeout=10)
            add("Subscribe(['EURUSD'])", True, "accepté par le serveur")
            for _ in range(80):
                if got:
                    break
                await asyncio.sleep(0.1)
            add("Réception ReceiveTick", bool(got), "tick reçu" if got else "aucun tick en 8 s (marché peut-être fermé)")
        except Exception as e:  # noqa: BLE001
            add("WebSocket / abonnement", False, f"{type(e).__name__}: {e}")
        finally:
            await c.close()
        return steps

    def stream_status(self) -> dict[str, Any]:
        return {**self.status, "subscribed": len(self._subscribed), "requested": len(self._symbols),
                "unavailable": dict(list(self.unavailable.items())[:50]), "unavailable_count": len(self.unavailable),
                "ticks_per_second": self.ticks_per_second(), "hub": f"{self.base_url}{self.hub_path}"}

    async def aclose(self) -> None:
        await self.stop_stream()
        await self._http.aclose()
