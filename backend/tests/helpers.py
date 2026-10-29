"""Données synthétiques déterministes pour les tests d'agents (aucun réseau)."""
from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo

import httpx

from app.bus import EventBus
from app.config import SecretStore
from app.llm.openrouter import OpenRouterClient
from app.market.feed import MarketFeed
from app.market.instruments import build_instrument
from app.market.provider import Bar, Instrument, MarketDataProvider, Quote
from app.paper.engine import PaperEngine

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


def real_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(tzinfo=None)


def weekdays_before(end: dt.date, n: int) -> list[dt.date]:
    out, d = [], end
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return sorted(out)


def session_start(day: dt.date, profile: str, anchor: str = "07:00") -> dt.datetime:
    """Début de séance en UTC naïf."""
    if profile == "us_equity":
        return dt.datetime.combine(day, dt.time(9, 30), NY).astimezone(UTC).replace(tzinfo=None)
    h, m = anchor.split(":")
    return dt.datetime.combine(day, dt.time(int(h), int(m)))


def ts_of(d: dt.datetime) -> int:
    return int(d.replace(tzinfo=UTC).timestamp())


def random_session(start: dt.datetime, rng: random.Random, p0: float, vol: float = 0.0006, n: int = 78) -> list[Bar]:
    bars, p = [], p0
    for i in range(n):
        o = p
        steps = [o * (1 + rng.gauss(0, vol)) for _ in range(3)]
        c = steps[-1]
        h, l = max([o, c] + steps), min([o, c] + steps)
        bars.append(Bar(ts=ts_of(start) + 300 * i, o=o, h=h, l=l, c=c, v=1))
        p = c
    return bars


def orb_session(start: dt.datetime, base: float, n: int, up_at: int = 3, up_pct: float = 0.0015, rng: random.Random | None = None) -> list[Bar]:
    """Séance du jour : fourchette plate sur 3 bougies puis clôture au-dessus du haut de la fourchette à la bougie `up_at`."""
    rng = rng or random.Random(5)
    bars = []
    rng_hi, rng_lo = base * 1.0005, base * 0.9995
    p = base
    for i in range(n):
        if i < 3:
            o, c = (base * 0.9998, base * 1.0002) if i % 2 == 0 else (base * 1.0002, base * 0.9999)
            h, l = (rng_hi, min(o, c, rng_lo + base * 0.0001)) if i == 1 else (max(o, c) * 1.0001, min(o, c) * 0.9999)
            h, l = min(h, rng_hi), max(l, rng_lo)
        elif i < up_at:
            o, c = p, base * 1.0002
            h, l = max(o, c) * 1.0001, min(o, c) * 0.9999
        else:
            o = p
            c = base * (1 + up_pct) + (i - up_at) * base * 0.00008
            h, l = max(o, c) * 1.0001, min(o, c) * 0.9999
        bars.append(Bar(ts=ts_of(start) + 300 * i, o=o, h=h, l=l, c=c, v=1))
        p = c
    return bars


def daily_from(bars: list[Bar]) -> list[Bar]:
    by_day: dict[dt.date, list[Bar]] = {}
    for b in bars:
        by_day.setdefault(dt.datetime.fromtimestamp(b.ts, UTC).date(), []).append(b)
    out = []
    for d in sorted(by_day):
        bs = by_day[d]
        out.append(Bar(ts=ts_of(dt.datetime.combine(d, dt.time(0))), o=bs[0].o, h=max(b.h for b in bs), l=min(b.l for b in bs), c=bs[-1].c, v=len(bs)))
    return out


class SynthProvider(MarketDataProvider):
    name = "synth"

    def __init__(self) -> None:
        self.hist: dict[tuple[str, str], list[Bar]] = {}
        self.fail: set[str] = set()

    async def list_instruments(self) -> list[Instrument]:
        return []

    async def latest(self, symbols):
        return {}

    async def history(self, symbol, interval="5m", limit=500, from_=None, to=None):
        if symbol in self.fail:
            raise RuntimeError("panne simulée")
        return list(self.hist.get((symbol, interval), []))[-limit:]

    async def start_stream(self, on_tick, on_state=None): ...
    async def subscribe(self, symbols): ...
    async def stop_stream(self): ...
    def stream_status(self):
        return {"state": "connected"}


def make_world(symbols: dict[str, dict], *, today: dt.date, now_utc: dt.datetime, n_hist: int = 30, seed: int = 11,
               orb: dict[str, int] | None = None, anchor: str = "07:00", cutoff_bars: int | None = None):
    """Construit feed + moteur avec historique (n_hist séances aléatoires + séance du jour avec cassure).

    symbols: {SYM: {"type":..., "price":..., "exchange":..., "profile": "continuous"|"us_equity"}}
    """
    prov = SynthProvider()
    feed = MarketFeed(prov, EventBus(), live_s=30, hard_stale_s=300)
    rng = random.Random(seed)
    for sym, spec in symbols.items():
        inst = build_instrument({"symbol": sym, "type": spec.get("type", "forex"), "exchange": spec.get("exchange", "")})
        feed.instruments[sym] = inst
        prof = spec.get("profile", "continuous")
        days = weekdays_before(today, n_hist)
        allb: list[Bar] = []
        p = spec["price"]
        for d in days:
            s = random_session(session_start(d, prof, anchor), rng, p, vol=spec.get("vol", 0.0006))
            allb += s
            p = s[-1].c
        # séance du jour jusqu'à `now`
        st = session_start(today, prof, anchor)
        n_today = max(0, int((now_utc - st).total_seconds() // 300))
        if n_today:
            day_bars = orb_session(st, p, 78, up_at=(orb or {}).get(sym, 3)) if (orb is None or sym in orb) else random_session(st, rng, p)
            allb += [b for b in day_bars[:n_today]]
        prov.hist[(sym, "5m")] = allb
        prov.hist[(sym, "1d")] = daily_from(allb)
        last = allb[-1].c
        q = Quote(sym, last * 0.99999, last * 1.00001, last, None, real_now(), "open")
        feed.quotes[sym] = q
        feed.states[sym] = "open"
    # taux FX pour les devises de cotation
    for ccy_pair, px in (("EURUSD", 1.10), ("GBPUSD", 1.30)):
        if ccy_pair not in feed.quotes:
            feed.quotes[ccy_pair] = Quote(ccy_pair, px, px * 1.00002, px, None, real_now(), "open")
    return feed, prov


def make_ctx(feed: MarketFeed, now_utc: dt.datetime, tmp_path=None, **kw):
    from app.agents.base import AgentContext
    engine = PaperEngine(feed, feed.bus)
    feed.add_listener(engine.on_quote)
    store = SecretStore(tmp_path) if tmp_path else SecretStore()
    llm = OpenRouterClient(store, transport=kw.pop("llm_transport", None))
    http = httpx.AsyncClient(transport=kw.pop("http_transport", httpx.MockTransport(lambda r: httpx.Response(503))))
    return AgentContext(feed=feed, engine=engine, bus=feed.bus, llm=llm, http=http, clock=lambda: now_utc, **kw)
