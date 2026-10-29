import datetime as dt

import pytest

from app import settings_store
from app.bus import EventBus
from app.market.biquote import BiquoteProvider
from app.market.feed import MarketFeed
from app.market.instruments import build_instrument
from app.market.provider import Quote
from app.paper.engine import OrderRejected, OrderRequest, PaperEngine


def now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def env(db):
    settings_store.reset_cache()
    feed = MarketFeed(BiquoteProvider(base_url="http://127.0.0.1:9"), EventBus(), live_s=30, hard_stale_s=300)
    for raw in ({"symbol": "EURUSD", "type": "forex"}, {"symbol": "USDJPY", "type": "forex"}, {"symbol": "XAUUSD", "type": "metal"},
                {"symbol": "BTCUSD", "type": "crypto"}):
        feed.instruments[raw["symbol"]] = build_instrument(raw)
    eng = PaperEngine(feed, feed.bus)
    feed.add_listener(eng.on_quote)
    return feed, eng


async def tick(feed, sym, bid, ask, state="open", age=0.0, ts=None):
    await feed._on_tick(Quote(sym, bid, ask, (bid + ask) / 2, None, ts or now(), state, age_at_recv_s=age))


def mk(eng, capital=100_000.0, **kw):
    return eng.create_portfolio(kw.pop("name", "P"), capital, **kw)["id"]


async def test_market_buy_pays_ask_slippage_and_fee(env):
    feed, eng = env
    pid = mk(eng, leverage=30, commission_bps=2, slippage_bps=1)
    await tick(feed, "EURUSD", 1.1000, 1.1002)
    o = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 10_000))
    assert o["status"] == "filled"
    exp_px = 1.1002 * (1 + 1e-4)
    assert abs(o["avg_fill_price"] - round(exp_px, 6)) < 1e-9
    f = eng.list_fills(pid)[0]
    assert abs(f["fee"] - 10_000 * f["price"] * 1.0 * 2 / 1e4) < 1e-9  # EURUSD cotée en USD : fx = 1
    s = eng.portfolio_summary(pid)
    assert abs(s["balance"] - (100_000 - f["fee"])) < 0.01
    pos = s["positions"][0]
    assert pos["qty"] == 10_000 and abs(pos["avg_price"] - f["price"]) < 1e-12
    # valorisé au bid : perte latente = spread + slippage
    assert pos["unrealized_pnl"] == round((1.1000 - f["price"]) * 10_000, 2)
    assert s["margin_used"] > 0 and abs(s["equity"] - (s["balance"] + s["unrealized_pnl"])) < 0.02


async def test_round_trip_realized_pnl_net_of_fees_and_jpy_conversion(env):
    feed, eng = env
    pid = mk(eng, commission_bps=0, slippage_bps=0)
    await tick(feed, "USDJPY", 150.00, 150.02)   # taux de conversion JPY->USD ~ 1/150.01
    await tick(feed, "EURUSD", 1.1, 1.1)
    # JPY est la devise de cotation de USDJPY : on exige qu'un taux soit disponible
    o = eng.place_order(OrderRequest(pid, "USDJPY", "buy", "market", 10_000))
    assert o["status"] == "filled" and o["avg_fill_price"] == 150.02
    await tick(feed, "USDJPY", 151.00, 151.02)
    c = eng.close_position(pid, "USDJPY")
    assert c["status"] == "filled" and c["avg_fill_price"] == 151.00
    fx = 1 / ((151.0 + 151.02) / 2)   # dernière cotation utilisée pour convertir
    s = eng.portfolio_summary(pid)
    assert not s["positions"]
    assert abs(s["realized_pnl"] - (151.00 - 150.02) * 10_000 * fx) < 0.006  # le résumé arrondit à 2 décimales
    assert abs(s["balance"] - (100_000 + s["realized_pnl"])) < 0.011


async def test_rejections_are_deterministic_and_audited(env):
    feed, eng = env
    pid = mk(eng, capital=1_000, leverage=1)
    await tick(feed, "EURUSD", 1.1, 1.1002)
    cases = [
        (OrderRequest(pid, "EURUSD", "buy", "market", 0), "positive"),
        (OrderRequest(pid, "EURUSD", "buy", "market", 1500), "multiple de 1000"),
        (OrderRequest(pid, "EURUSD", "hold", "market", 1000), "sens invalide"),
        (OrderRequest(pid, "EURUSD", "buy", "twap", 1000), "type d'ordre"),
        (OrderRequest(pid, "EURUSD", "buy", "limit", 1000), "limit_price requis"),
        (OrderRequest(pid, "EURUSD", "buy", "market", 1000, limit_price=1.0), "non applicable"),
        (OrderRequest(pid, "NOPE", "buy", "market", 1000), "instrument inconnu"),
        (OrderRequest(pid, "EURUSD", "sell", "market", 1000), "découvert"),
        (OrderRequest(pid, "EURUSD", "buy", "market", 100_000), "marge insuffisante"),
        (OrderRequest(pid, "EURUSD", "buy", "market", float("nan")), "positive"),
    ]
    for req, frag in cases:
        o = eng.place_order(req)
        assert o["status"] == "rejected" and frag in o["reject_reason"], (req, o["reject_reason"])
    assert eng.portfolio_summary(pid)["balance"] == 1_000
    kinds = [e["kind"] for e in eng.ledger(pid)]
    assert kinds.count("order_rejected") == len(cases)


async def test_stale_quote_and_closed_market_block_execution(env):
    feed, eng = env
    pid = mk(eng)
    await tick(feed, "EURUSD", 1.1, 1.1002, ts=now() - dt.timedelta(seconds=120))
    o = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 1000))
    assert o["status"] == "rejected" and "périmée" in o["reject_reason"]
    await tick(feed, "EURUSD", 1.1, 1.1002, state="closed", age=7200)
    o = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 1000))
    assert o["status"] == "rejected" and "marché fermé" in o["reject_reason"]
    await tick(feed, "GBPUSD", 1.3, 1.3002)  # instrument absent du catalogue mais coté
    assert eng.place_order(OrderRequest(pid, "GBPUSD", "buy", "market", 1000))["status"] == "filled"


async def test_no_fx_rate_means_refusal_not_a_guess(env):
    feed, eng = env
    pid = mk(eng)
    await tick(feed, "USDJPY", 150, 150.02)
    feed.quotes.pop("USDJPY"); await tick(feed, "USDJPY", 150, 150.02)
    # supprimer toute cotation permettant JPY->USD (USDJPY est la paire elle-même : on la retire)
    feed.quotes.pop("USDJPY")
    await tick(feed, "EURJPY", 160.0, 160.03)
    o = eng.place_order(OrderRequest(pid, "EURJPY", "buy", "market", 1000))
    assert o["status"] == "rejected" and "conversion" in o["reject_reason"]


async def test_limit_stop_and_stoplimit_triggering(env):
    feed, eng = env
    pid = mk(eng, commission_bps=0, slippage_bps=0)
    await tick(feed, "EURUSD", 1.1000, 1.1002)
    lim = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "limit", 1000, limit_price=1.0950))
    assert lim["status"] == "open"
    await tick(feed, "EURUSD", 1.0990, 1.0992)
    assert eng.list_orders(pid, "open")  # toujours en attente : ask > limite
    await tick(feed, "EURUSD", 1.0946, 1.0948)
    o = [x for x in eng.list_orders(pid) if x["id"] == lim["id"]][0]
    assert o["status"] == "filled" and o["avg_fill_price"] == 1.0948  # exécuté au meilleur : ask < limite
    # stop de vente : se déclenche et s'exécute AU MARCHÉ, donc possiblement pire que le stop
    st = eng.place_order(OrderRequest(pid, "EURUSD", "sell", "stop", 1000, stop_price=1.0900))
    assert st["status"] == "open"
    await tick(feed, "EURUSD", 1.0850, 1.0852)  # gap sous le stop
    o = [x for x in eng.list_orders(pid) if x["id"] == st["id"]][0]
    assert o["status"] == "filled" and o["avg_fill_price"] == 1.0850 < 1.0900
    # stop-limit d'achat : déclenché mais limite non atteinte -> reste « triggered »
    await tick(feed, "EURUSD", 1.1000, 1.1002)
    sl = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "stop_limit", 1000, stop_price=1.1050, limit_price=1.1060))
    await tick(feed, "EURUSD", 1.1068, 1.1070)
    cur = [x for x in eng.list_orders(pid) if x["id"] == sl["id"]][0]
    assert cur["status"] == "triggered"
    await tick(feed, "EURUSD", 1.1056, 1.1058)
    cur = [x for x in eng.list_orders(pid) if x["id"] == sl["id"]][0]
    assert cur["status"] == "filled" and cur["avg_fill_price"] == 1.1058


async def test_bracket_creates_oco_legs_and_cancels_sibling(env):
    feed, eng = env
    pid = mk(eng, commission_bps=0, slippage_bps=0)
    await tick(feed, "EURUSD", 1.1000, 1.1002)
    o = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 10_000, stop_loss=1.0950, take_profit=1.1100, source="agent:entry"))
    assert o["status"] == "filled"
    legs = [x for x in eng.list_orders(pid, "active")]
    assert {x["role"] for x in legs} == {"stop_loss", "take_profit"} and len({x["group_id"] for x in legs}) == 1
    await tick(feed, "EURUSD", 1.1110, 1.1112)
    allo = {x["role"]: x for x in eng.list_orders(pid)}
    assert allo["take_profit"]["status"] == "filled" and allo["stop_loss"]["status"] == "cancelled"
    assert not eng.portfolio_summary(pid)["positions"]
    assert eng.portfolio_summary(pid)["realized_pnl"] > 0


async def test_close_position_cancels_protective_orders(env):
    feed, eng = env
    pid = mk(eng, commission_bps=0, slippage_bps=0)
    await tick(feed, "XAUUSD", 2000.0, 2000.5)
    eng.place_order(OrderRequest(pid, "XAUUSD", "buy", "market", 1.0, stop_loss=1990, take_profit=2020))
    assert len(eng.list_orders(pid, "active")) == 2
    eng.close_position(pid, "XAUUSD")
    assert not eng.list_orders(pid, "active") and not eng.portfolio_summary(pid)["positions"]


async def test_exposure_cap_and_margin_reservation(env):
    feed, eng = env
    pid = mk(eng, capital=10_000, leverage=100, risk={"max_exposure_pct": 200})
    await tick(feed, "EURUSD", 1.1, 1.1002)
    ok = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 15_000))   # 16 500 USD = 165%
    assert ok["status"] == "filled"
    over = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 10_000))  # -> 275%
    assert over["status"] == "rejected" and "exposition" in over["reject_reason"]


async def test_day_orders_expire(env):
    feed, eng = env
    pid = mk(eng)
    await tick(feed, "EURUSD", 1.1, 1.1002)
    o = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "limit", 1000, limit_price=1.0, tif="day",
                                     expire_at=now() - dt.timedelta(seconds=1)))
    assert o["status"] == "open"
    await tick(feed, "EURUSD", 1.1001, 1.1003)
    assert eng.list_orders(pid)[0]["status"] == "expired"


async def test_persistence_and_replay_consistency(env):
    feed, eng = env
    pid = mk(eng, commission_bps=1.5, slippage_bps=0.7)
    await tick(feed, "EURUSD", 1.10, 1.1002)
    await tick(feed, "BTCUSD", 70000, 70040)
    eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 7000))
    eng.place_order(OrderRequest(pid, "BTCUSD", "buy", "market", 0.5))
    await tick(feed, "EURUSD", 1.105, 1.1052)
    eng.place_order(OrderRequest(pid, "EURUSD", "sell", "market", 3000))
    eng.place_order(OrderRequest(pid, "EURUSD", "buy", "market", 2000))
    r = eng.replay_from_fills(pid)
    assert r["consistent"], r
    # redémarrage : un nouveau moteur relit l'état depuis la base
    eng2 = PaperEngine(feed, feed.bus)
    eng2.load_state()
    assert eng2.portfolio_summary(pid)["balance"] == eng.portfolio_summary(pid)["balance"]
    assert {p["symbol"] for p in eng2.list_positions(pid)} == {"EURUSD", "BTCUSD"}


async def test_portfolios_are_independent_and_reset(env):
    feed, eng = env
    a, b = mk(eng, name="A"), mk(eng, name="B", capital=5_000)
    await tick(feed, "EURUSD", 1.1, 1.1002)
    eng.place_order(OrderRequest(a, "EURUSD", "buy", "market", 5000))
    assert eng.portfolio_summary(b)["balance"] == 5_000 and not eng.portfolio_summary(b)["positions"]
    with pytest.raises(ValueError):
        eng.create_portfolio("A", 1000)
    with pytest.raises(ValueError):
        eng.create_portfolio("C", -5)
    eng.reset_portfolio(a)
    s = eng.portfolio_summary(a)
    assert s["balance"] == 100_000 and not s["positions"] and not eng.list_orders(a)


async def test_realized_pnl_never_counts_unfilled(env):
    feed, eng = env
    pid = mk(eng)
    await tick(feed, "EURUSD", 1.1, 1.1002)
    o = eng.place_order(OrderRequest(pid, "EURUSD", "buy", "limit", 1000, limit_price=1.05))
    assert o["status"] == "open" and o["filled_qty"] == 0
    assert eng.list_fills(pid) == [] and eng.portfolio_summary(pid)["balance"] == 100_000
    eng.cancel_order(o["id"])
    with pytest.raises(ValueError):
        eng.cancel_order(o["id"])
