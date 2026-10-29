import datetime as dt

import pytest

from app.agents.entry import EntryAgent
from app.agents.exit import ExitAgent
from app.agents.timemachine import TimeMachineAgent
from tests.helpers import make_ctx, make_world

TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 8, 30)


async def _plan(ctx):
    e = await EntryAgent().run(ctx, symbol="EURUSD")
    t = e.output["trigger"]
    x = await ExitAgent().run(ctx, symbol="EURUSD", entry=t["price"], opening_range=e.output["opening_range"], entry_time=t["closes_at"])
    return x.output["plan"]


async def test_simulation_deterministic(db, tmp_path):
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3}, n_hist=30)
    ctx = make_ctx(feed, NOW, tmp_path, agent_params={"replays": 1500})
    plan = await _plan(ctx)
    r1 = await TimeMachineAgent().run(ctx, plan=plan)
    r2 = await TimeMachineAgent().run(ctx, plan=plan)
    assert r1.status == "ok", r1.summary
    o = r1.output
    assert o["pool_sessions"] >= 20 and 0 <= o["p_target"] <= 1 and 0 <= o["p_stop"] <= 1
    assert o["p_target"] + o["p_stop"] + o["p_neither"] == pytest.approx(1, abs=1e-3)
    assert (o["p_target"], o["p_stop"]) == (r2.output["p_target"], r2.output["p_stop"])  # même graine, mêmes chiffres
    assert o["gate"] == "pass"


async def test_insufficient_history_is_honest(db, tmp_path):
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3}, n_hist=8)
    ctx = make_ctx(feed, NOW, tmp_path, agent_params={"replays": 500})
    plan = await _plan(ctx)
    r = await TimeMachineAgent().run(ctx, plan=plan)
    assert r.status == "partial" and r.output["insufficient_data"] and "p_target" not in r.output


async def test_min_edge_gate(db, tmp_path):
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    ctx = make_ctx(feed, NOW, tmp_path, agent_params={"replays": 1000, "min_edge": 0.9})
    r = await TimeMachineAgent().run(ctx, plan=await _plan(ctx))
    assert r.output["gate"] == "block"


async def test_history_cache_does_not_truncate_larger_requests(db):
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, n_hist=30)
    small = await feed.get_bars("EURUSD", "5m", 320)
    big = await feed.get_bars("EURUSD", "5m", 3200)
    assert len(small) == 320 and len(big) > 2000
