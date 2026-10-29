import datetime as dt

import pytest

from app.agents.entry import EntryAgent
from app.agents.exit import ExitAgent
from app.paper.engine import OrderRequest
from tests.helpers import make_ctx, make_world

TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 8, 30)


@pytest.fixture
def ctx(db, tmp_path):
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    return make_ctx(feed, NOW, tmp_path)


async def test_plan_from_entry(ctx):
    e = await EntryAgent().run(ctx, symbol="EURUSD")
    t = e.output["trigger"]
    r = await ExitAgent().run(ctx, symbol="EURUSD", entry=t["price"], opening_range=e.output["opening_range"],
                              entry_time=t["closes_at"])
    assert r.status == "ok", r.summary
    pl = r.output["plan"]
    orr = e.output["opening_range"]
    assert pl["stop"] < pl["entry"] < pl["target"]
    assert abs(pl["stop"] - (orr["high"] + orr["low"]) / 2) < 2e-5   # stop = milieu de la fourchette
    assert 1.9 <= pl["reward_risk"] <= 2.1
    assert pl["flat_by_utc"].startswith("2026-10-28T13:25")   # 15:55 mappé = 07:00 + 6h25


async def test_atr_stop_and_errors(ctx):
    r = await ExitAgent().run(ctx, symbol="EURUSD", entry=1.1, stop="atr:2", target="1.5R")
    assert r.status == "ok" and r.output["plan"]["atr"] > 0
    bad = await ExitAgent().run(ctx, symbol="EURUSD", entry=1.1, stop="price:1.2")
    assert bad.status == "error" and "stop" in bad.summary


async def test_monitor_closes_at_flat_by(ctx):
    pid = ctx.engine.create_portfolio("t", 10000, leverage=30)["id"]
    plan = {"flat_by_utc": "2026-10-28T08:00:00Z"}
    o = ctx.engine.place_order(OrderRequest(pid, "EURUSD", "buy", qty=10000, plan=plan, source="test"))
    assert o["status"] == "filled"
    r = await ExitAgent().run(ctx, mode="monitor")
    assert len(r.output["closed"]) == 1 and not ctx.engine.list_positions(pid)
