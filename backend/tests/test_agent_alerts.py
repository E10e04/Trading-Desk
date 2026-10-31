import datetime as dt

import pytest

from app.agents.alerts import AlertsAgent
from app.market.provider import Quote
from app.models import Notification
from app.db import session_scope
from tests.helpers import make_ctx, make_world, real_now

TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 8, 30)


@pytest.fixture
def ctx(db, tmp_path):
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    return make_ctx(feed, NOW, tmp_path)


def setq(ctx, px, fresh=True):
    ts = real_now() if fresh else real_now() - dt.timedelta(seconds=900)
    ctx.feed.quotes["EURUSD"] = Quote("EURUSD", px * 0.99999, px * 1.00001, px, None, ts, "open")


def count():
    with session_scope() as s:
        return s.query(Notification).count()


async def test_price_rule_rings_once_in_real_units(ctx):
    px = ctx.feed.quotes["EURUSD"].mid
    AlertsAgent.add_rule({"id": "r1", "symbol": "EURUSD", "type": "above", "price": round(px * 1.001, 5)})
    setq(ctx, px)
    r = await AlertsAgent().run(ctx)
    assert not r.output["rings"]
    setq(ctx, px * 1.002)
    r = await AlertsAgent().run(ctx)
    assert len(r.output["rings"]) == 1 and "cible" not in r.output["rings"][0]["message"]
    assert "$" not in r.output["rings"][0]["message"]
    r2 = await AlertsAgent().run(ctx)
    assert not r2.output["rings"]            # une seule sonnerie


async def test_no_alert_on_stale_quote(ctx):
    px = ctx.feed.quotes["EURUSD"].mid
    AlertsAgent.add_rule({"id": "r2", "symbol": "EURUSD", "type": "below", "price": px * 2})
    setq(ctx, px, fresh=False)
    n0 = count()
    r = await AlertsAgent().run(ctx)
    assert not r.output["rings"] and r.output["skipped_stale"]
    assert count() >= n0   # seules d'éventuelles alertes opérationnelles


async def test_plan_alerts_and_flat_by(ctx):
    plan = {"symbol": "EURUSD", "date": "2026-10-28", "stop": 1.09, "target": 1.12, "flat_by": "11:00", "stop_label": "x", "target_label": "y"}
    rules = AlertsAgent.add_from_plan(plan, None)
    assert len(rules) == 3
    r = await AlertsAgent().run(ctx)    # 08:30 UTC = 11:00 mappé : l'heure flat-by est atteinte
    assert any(x["type"] == "time" for x in r.output["rings"])


async def test_operational_kill_switch(ctx):
    from app import settings_store
    settings_store.update({"kill_switch": True})
    r = await AlertsAgent().run(ctx)
    assert any(o["key"] == "kill" for o in r.output["operational"])
    r = await AlertsAgent().run(ctx)
    assert not any(o["key"] == "kill" for o in r.output["operational"])   # limité (throttle)
    settings_store.update({"kill_switch": False})


async def test_alerte_sur_tous_les_actifs(ctx):
    from app import settings_store
    from app.agents import alerts as al
    al._ALL_RUNG.clear()
    settings_store.update({"alert_all_assets": True, "alert_all_pct": 3.0})
    ctx.feed.states["EURUSD"] = "open"
    q = ctx.feed.quotes["EURUSD"]
    ctx.feed.quotes["EURUSD"] = Quote("EURUSD", q.mid, q.mid, q.mid, None, real_now(), "open", change_pct=1.0)
    r = await AlertsAgent().run(ctx)
    assert r.output["all_assets"]["checked"] == 1 and not r.output["all_assets"]["rang"]       # 1 % < 3 %
    ctx.feed.quotes["EURUSD"] = Quote("EURUSD", q.mid, q.mid, q.mid, None, real_now(), "open", change_pct=-3.4)
    r = await AlertsAgent().run(ctx)
    ring = r.output["all_assets"]["rang"]
    assert len(ring) == 1 and ring[0]["symbol"] == "EURUSD" and "3.40 %" in ring[0]["message"]
    r = await AlertsAgent().run(ctx)
    assert not r.output["all_assets"]["rang"]                                                  # une seule fois par jour et par sens
    ctx.feed.quotes["EURUSD"] = Quote("EURUSD", q.mid, q.mid, q.mid, None, real_now() - dt.timedelta(seconds=900), "open", change_pct=9.0)
    al._ALL_RUNG.clear()
    r = await AlertsAgent().run(ctx)
    assert r.output["all_assets"]["checked"] == 0                                              # cotation périmée : ignorée
    settings_store.update({"alert_all_assets": False})
    assert (await AlertsAgent().run(ctx)).output["all_assets"]["enabled"] is False
    settings_store.update({"alert_all_assets": True})
