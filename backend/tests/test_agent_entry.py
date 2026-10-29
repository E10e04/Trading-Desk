import datetime as dt

import pytest

from app.agents.entry import EntryAgent
from tests.helpers import make_ctx, make_world

TODAY = dt.date(2026, 10, 28)  # mercredi
NOW = dt.datetime(2026, 10, 28, 8, 30)


@pytest.fixture
def world(db, tmp_path):
    feed, prov = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    return make_ctx(feed, NOW, tmp_path)


async def test_orb_triggered(world):
    r = await EntryAgent().run(world, symbol="EURUSD")
    assert r.output["status"] == "triggered", r.summary
    t = r.output["trigger"]
    assert t["price"] > r.output["opening_range"]["high"]
    assert t["closes_at"].startswith("2026-10-28T07:20")
    assert 60 < t["signal_age_min"] < 80
    assert r.output["signal_key"] == "EURUSD|orb|2026-10-28"


async def test_waiting_before_range_complete(db, tmp_path):
    now = dt.datetime(2026, 10, 28, 7, 10)
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=now, orb={"EURUSD": 3})
    r = await EntryAgent().run(make_ctx(feed, now, tmp_path), symbol="EURUSD")
    assert r.output["status"] == "waiting" and r.output["phase"] == "forming"


async def test_no_data_and_unknown(world):
    r = await EntryAgent().run(world, symbol="NOPE")
    assert r.status in ("error", "skipped")
