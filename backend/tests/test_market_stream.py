import asyncio
import datetime as dt

import httpx
import pytest

from app.bus import EventBus
from app.market.bars import BarStore, aggregate, merge
from app.market.biquote import BiquoteProvider, normalize_tick, parse_bars
from app.market.feed import MarketFeed
from app.market.provider import Bar, Quote, parse_ts
from app.market.signalr import SignalRClient


# ------------------------------------------------------------------ normalisation / validation
def test_normalize_tick_variants():
    q = normalize_tick({"symbol": "eurusd", "bid": 1.1, "ask": 1.1002, "mid": 1.1001, "timestamp": "2026-10-28T08:00:00Z"})
    assert q and q.symbol == "EURUSD" and q.mid == 1.1001 and q.ts_source == dt.datetime(2026, 10, 28, 8, 0, 0)
    q = normalize_tick({"Symbol": "XAUUSD", "Bid": 2000, "Ask": 2000.4})
    assert q and abs(q.mid - 2000.2) < 1e-9  # mid reconstitué
    q = normalize_tick({"symbol": "BTCUSD", "mid": 70000, "last": 0, "volume": 0, "marketState": "closed", "quoteAgeSeconds": 7200})
    assert q and q.market_state == "closed" and q.age_at_recv_s == 7200


@pytest.mark.parametrize("raw", [None, [], {}, {"mid": 1.0}, {"symbol": ""}, {"symbol": "X", "mid": 0}, {"symbol": "X", "mid": "abc"},
                                  {"symbol": "X", "mid": float("nan")}, {"symbol": "X", "bid": 2.0, "ask": 1.0},
                                  {"symbol": "X", "bid": 1.0, "ask": 1.1, "mid": 5.0}, {"symbol": "X", "mid": -1}])
def test_normalize_tick_rejects_invalid(raw):
    q = normalize_tick(raw)
    # le seul cas « récupérable » est bid>ask sans mid => mid absent => rejet ; sinon rejet franc
    assert q is None


def test_parse_ts_forms():
    assert parse_ts("2026-10-28T08:00:00+01:00") == dt.datetime(2026, 10, 28, 7, 0, 0)
    assert parse_ts(1782633600) == parse_ts(1782633600000)
    assert parse_ts("garbage") is None and parse_ts(None) is None and parse_ts(-5) is None


def test_quote_age_independent_of_source_clock():
    now = dt.datetime(2026, 10, 28, 8, 0, 0)
    q = Quote("EURUSD", None, None, 1.1, ts_source=now - dt.timedelta(days=400), ts_recv=now, age_at_recv_s=0)
    assert q.age_s(now + dt.timedelta(seconds=5)) == 5  # horloge source absurde ignorée
    q2 = Quote("EURUSD", None, None, 1.1, None, now, age_at_recv_s=172800)
    assert q2.age_s(now) == 172800


def test_parse_bars_sorting_and_dedup():
    payload = {"bars": [
        {"time": "2026-10-28T08:05:00Z", "open": 2, "high": 3, "low": 1, "close": 2.5},
        {"time": "2026-10-28T08:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 2},
        {"time": "2026-10-28T08:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 2},
        {"time": "2026-10-28T08:10:00Z", "open": 1, "high": 0.5, "low": 2, "close": 1},  # h<l : rejetée
        {"open": 1, "high": 1, "low": 1, "close": 1},  # sans heure
    ]}
    bars = parse_bars(payload)
    assert [b.ts for b in bars] == sorted(b.ts for b in bars) and len(bars) == 2


# ------------------------------------------------------------------ bougies
def test_barstore_builds_and_closes():
    bs = BarStore()
    t0 = dt.datetime(2026, 10, 28, 8, 0, 5)

    def tick(sec, price):
        return Quote("EURUSD", None, None, price, None, t0 + dt.timedelta(seconds=sec))

    assert bs.update(tick(0, 1.0)) == [] and bs.update(tick(10, 1.2)) == [] and bs.update(tick(20, 0.9)) == []
    closed = bs.update(tick(65, 1.1))
    assert [tf for tf, _ in closed] == ["1m"]
    b = closed[0][1]
    assert (b.o, b.h, b.l, b.c, b.v) == (1.0, 1.2, 0.9, 0.9, 3) and b.closed
    live = bs.live_bars("EURUSD", "1m")
    assert len(live) == 2 and live[-1].closed is False
    # tick dormant (marché fermé) ignoré
    stale = Quote("EURUSD", None, None, 5.0, None, t0 + dt.timedelta(seconds=70), age_at_recv_s=3600)
    assert bs.update(stale) == []


def test_merge_and_aggregate():
    hist = [Bar(ts=60 * i, o=1, h=2, l=0.5, c=1.5) for i in range(5)]
    live = [Bar(ts=120, o=9, h=9, l=9, c=9, closed=False)]
    m = merge(hist, live, 100)
    assert len(m) == 5 and m[2].o == 9
    agg = aggregate([Bar(ts=300 + 60 * i, o=i + 1, h=i + 2, l=i, c=i + 1.5) for i in range(5)], 300)
    assert len(agg) == 1 and agg[0].o == 1 and agg[0].h == 6 and agg[0].l == 0 and agg[0].c == 5.5


# ------------------------------------------------------------------ SignalR contre un faux hub (HTTP + WebSocket réels)
async def test_signalr_client_roundtrip(fake_hub):
    got = []
    c = SignalRClient(fake_hub.url, "/hubs/tick", ping_interval=0.5)
    c.on("ReceiveTick", lambda t: got.append(t))
    await c.connect()
    assert c.state == "connected" and fake_hub.negotiate_calls == 1
    await c.invoke("Subscribe", "EURUSD")
    await asyncio.sleep(0.4)
    assert got and got[0]["symbol"] == "EURUSD"
    with pytest.raises(Exception, match="unknown symbol"):
        await c.invoke("Subscribe", "BAD")
    await c.close()
    assert c.state == "disconnected"


async def test_signalr_falls_back_to_direct_websocket_when_negotiate_fails(fake_hub):
    fake_hub.fail_negotiate = True
    c = SignalRClient(fake_hub.url, "/hubs/tick")
    await c.connect()
    assert c.state == "connected"
    await c.close()


async def test_provider_subscribe_unavailable_state_and_reconnect(fake_hub):
    ticks, states = [], []
    p = BiquoteProvider(base_url=fake_hub.url, backoff_max=0.3, signalr_kwargs={"ping_interval": 0.5})
    await p.subscribe(["EURUSD", "XAUUSD", "BAD"])  # demandé avant connexion
    await p.start_stream(lambda q: ticks.append(q), lambda s, st: states.append((s, st)))
    for _ in range(60):
        if len(ticks) >= 4:
            break
        await asyncio.sleep(0.05)
    st = p.stream_status()
    assert st["state"] == "connected" and st["subscribed"] == 2 and "BAD" in st["unavailable"]
    assert {q.symbol for q in ticks} == {"EURUSD", "XAUUSD"}
    assert ("EURUSD", "open") in states  # ReceiveSubscriptionState géré (plus d'avertissement)

    # coupure côté serveur -> reconnexion automatique + réabonnement
    for ws in list(fake_hub.sockets):
        await ws.close(code=1011)
    for _ in range(100):
        if fake_hub.connections >= 2 and p.stream_status()["state"] == "connected":
            break
        await asyncio.sleep(0.05)
    assert fake_hub.connections >= 2
    n = len(ticks)
    await asyncio.sleep(0.5)
    assert len(ticks) > n, "les ticks doivent reprendre après reconnexion"
    assert p.stream_status()["reconnects"] >= 1
    await p.aclose()
    assert p.stream_status()["state"] == "disconnected"


async def test_provider_survives_unreachable_server():
    p = BiquoteProvider(base_url="http://127.0.0.1:9", backoff_max=0.2)
    await p.start_stream(lambda q: None)
    await asyncio.sleep(0.8)
    st = p.stream_status()
    assert st["state"] in ("connecting", "reconnecting") and st["last_error"]
    await p.aclose()  # arrêt propre même en échec de connexion


# ------------------------------------------------------------------ REST (MockTransport)
def _mock_rest():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/symbols":
            return httpx.Response(200, json=[{"symbol": "EURUSD", "type": "forex", "digits": 5}, {"symbol": "XAUUSD", "type": "metal"},
                                             {"symbol": "BTCUSD", "type": "crypto"}, {"symbol": "AAPL", "type": "stock", "exchange": "NASDAQ"}])
        if path == "/api/active":
            return httpx.Response(200, json=[{"symbol": "EURUSD"}])
        if path == "/api/latest":
            syms = req.url.params.get_list("symbols")
            return httpx.Response(200, json={s: {"symbol": s, "bid": 1.0, "ask": 1.001, "mid": 1.0005, "marketState": "closed", "quoteAgeSeconds": 99999} for s in syms})
        if path.endswith("/ohlc"):
            return httpx.Response(200, json={"bars": [{"time": "2026-10-28T08:05:00Z", "open": 2, "high": 3, "low": 1, "close": 2.5},
                                                      {"time": "2026-10-28T08:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 2}]})
        return httpx.Response(404, json={"error": "unknown"})
    return httpx.MockTransport(handler)


async def test_provider_rest_catalogue_latest_history():
    p = BiquoteProvider(base_url="https://biquote.test", http_transport=_mock_rest())
    insts = {i.symbol: i for i in await p.list_instruments()}
    assert insts["EURUSD"].asset_class == "forex" and insts["EURUSD"].live and insts["EURUSD"].quote_ccy == "USD"
    assert insts["XAUUSD"].asset_class == "metal" and insts["BTCUSD"].asset_class == "crypto" and insts["AAPL"].asset_class == "equity"
    q = await p.latest(["EURUSD", "XAUUSD"])
    assert q["EURUSD"].market_state == "closed" and q["EURUSD"].age_at_recv_s == 99999
    bars = await p.history("EURUSD", "5m", 10)
    assert [b.ts for b in bars] == sorted(b.ts for b in bars) and len(bars) == 2
    with pytest.raises(Exception):
        await p.history("EURUSD", "7m")
    await p.aclose()


async def test_rest_unknown_symbol_is_a_clean_error():
    p = BiquoteProvider(base_url="https://biquote.test", http_transport=_mock_rest())
    with pytest.raises(Exception, match="404"):
        await p._get("/api/NOPE", retries=3)
    await p.aclose()


# ------------------------------------------------------------------ MarketFeed : fraîcheur, périmé, conversion FX
async def test_feed_freshness_and_fx():
    class Dummy(BiquoteProvider):
        pass

    p = Dummy(base_url="https://biquote.test", http_transport=_mock_rest())
    feed = MarketFeed(p, EventBus(), live_s=30, hard_stale_s=300)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    await feed._on_tick(Quote("EURUSD", 1.0, 1.001, 1.0005, None, now, "open"))
    assert feed.freshness("EURUSD")["status"] == "live"
    await feed._on_tick(Quote("USDJPY", 150.0, 150.02, 150.01, None, now - dt.timedelta(seconds=120), "open"))
    assert feed.freshness("USDJPY")["status"] == "stale" and feed.freshness("USDJPY")["hard_stale"] is False
    await feed._on_tick(Quote("GBPUSD", 1.3, 1.3002, 1.3001, None, now - dt.timedelta(seconds=1000), "unknown"))
    assert feed.freshness("GBPUSD")["hard_stale"] is True
    await feed._on_tick(Quote("XAGUSD", 30, 30.1, 30.05, None, now, "closed"))
    assert feed.freshness("XAGUSD")["status"] == "closed"
    assert feed.freshness("NOPE")["status"] == "unknown"
    assert feed.fx_to_usd("USD") == 1.0
    assert abs(feed.fx_to_usd("EUR") - 1.0005) < 1e-9
    assert abs(feed.fx_to_usd("JPY") - 1 / 150.01) < 1e-9
    assert feed.fx_to_usd("ZZZ") is None and feed.fx_to_usd(None) is None
    await p.aclose()


async def _wait_ticks(ticks, n=2):
    for _ in range(80):
        if len(ticks) >= n:
            return
        await asyncio.sleep(0.05)


async def test_subscribe_envoie_un_tableau_de_symboles_comme_dans_la_doc(fake_hub):
    ticks = []
    p = BiquoteProvider(base_url=fake_hub.url, backoff_max=0.3, signalr_kwargs={"ping_interval": 0.5})
    await p.subscribe(["EURUSD", "XAUUSD", "GBPUSD"])
    await p.start_stream(lambda q: ticks.append(q))
    await _wait_ticks(ticks, 3)
    assert fake_hub.calls[0] == ["EURUSD", "GBPUSD", "XAUUSD"]  # un seul appel, tableau
    assert p.stream_status()["subscribed"] == 3
    await p.stop_stream()
    await p.aclose()


async def test_subscribe_repli_symbole_par_symbole_si_le_serveur_refuse_le_tableau(fake_hub):
    fake_hub.single_only = True
    ticks = []
    p = BiquoteProvider(base_url=fake_hub.url, backoff_max=0.3, signalr_kwargs={"ping_interval": 0.5})
    await p.subscribe(["EURUSD", "XAUUSD"])
    await p.start_stream(lambda q: ticks.append(q))
    await _wait_ticks(ticks)
    assert p.stream_status()["subscribed"] == 2
    await p.stop_stream()
    await p.aclose()


async def test_un_symbole_inconnu_dans_un_lot_n_empeche_pas_les_autres(fake_hub):
    ticks = []
    p = BiquoteProvider(base_url=fake_hub.url, backoff_max=0.3, signalr_kwargs={"ping_interval": 0.5})
    await p.subscribe(["EURUSD", "BAD", "XAUUSD"])
    await p.start_stream(lambda q: ticks.append(q))
    await _wait_ticks(ticks)
    st = p.stream_status()
    assert st["subscribed"] == 2 and "BAD" in st["unavailable"]
    await p.stop_stream()
    await p.aclose()
