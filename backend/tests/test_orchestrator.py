import asyncio
import datetime as dt

import pytest

from app import settings_store
from app.agents.entry import EntryAgent
from app.agents.registry import AGENTS
from app.db import session_scope
from app.market.provider import Quote
from app.models import AgentRun, AlertRule, Notification, Proposal, WatchItem
from app.orchestrator import Orchestrator
from tests.helpers import make_ctx, make_world, real_now

TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 8, 30)


@pytest.fixture
def orch(db, tmp_path):
    settings_store.reset_cache()
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    c = make_ctx(feed, NOW, tmp_path)
    o = Orchestrator(feed, c.engine, feed.bus, c.llm, c.http, clock=lambda: NOW)
    o.engine.create_portfolio("p1", 10000, leverage=30)
    # signal « frais » : cotation recalée sur la cassure ; âge max relevé car l'horloge de test est figée 70 min après la cassure
    o.set_config("risk", params={"max_signal_age_min": 240})
    o.set_config("timemachine", params={"replays": 600})
    _pin_quote(feed)
    return o


def _pin_quote(feed):
    # prix de la cassure : lu dans les bougies (clôture de la bougie 3 de la séance mappée)
    from app.agents.adapt import mapper_for
    inst = feed.info("EURUSD")
    st = dt.datetime(2026, 10, 28, 7, 0)
    ts = int(st.replace(tzinfo=dt.timezone.utc).timestamp()) + 300 * 3
    bars = [b for b in feed.provider.hist[("EURUSD", "5m")] if b.ts == ts]
    px = bars[0].c
    feed.quotes["EURUSD"] = Quote("EURUSD", px * 0.99999, px * 1.00001, px, None, real_now(), "open")


def runs(agent=None):
    with session_scope() as s:
        q = s.query(AgentRun)
        if agent:
            q = q.filter(AgentRun.agent_id == agent)
        return [(r.agent_id, r.status, r.correlation_id, r.trigger, r.skill_version, r.instrument) for r in q.order_by(AgentRun.id)]


async def test_signal_runs_whole_chain_and_audits(orch):
    out = await orch.process_symbol("EURUSD")
    assert len(out) == 1 and out[0]["status"] == "executed", out
    pos = orch.engine.list_positions()
    assert len(pos) == 1 and pos[0]["symbol"] == "EURUSD"
    rr = runs()
    assert [r[0] for r in rr if r[0] != "news"][:6] == ["entry", "exit", "timemachine", "budget", "risk", "broker"]
    assert len({r[2] for r in rr}) == 1                       # même correlation_id sur toute la chaîne
    assert all(r[4].startswith("day-trader-skill") for r in rr)   # version du skill auditée
    with session_scope() as s:
        p = s.query(Proposal).one()
        assert p.status == "executed" and p.decision == "auto" and p.order_ids and p.risk["verdict"] == "PASS"
        assert s.query(AlertRule).count() == 3                  # stop + cible + flat-by
        assert s.query(Notification).filter(Notification.source == "broker").count() == 1
    # Risk est passé AVANT Broker
    ids = [r[0] for r in rr]
    assert ids.index("risk") < ids.index("broker")


async def test_one_trigger_per_signal_even_after_restart(orch):
    await orch.process_symbol("EURUSD")
    again = await orch.process_symbol("EURUSD")
    assert again == []
    orch._processed.clear()                                   # « redémarrage » : la base fait foi
    assert await orch.process_symbol("EURUSD") == []
    with session_scope() as s:
        assert s.query(Proposal).count() == 1


async def test_concurrent_events_do_not_double_trade(orch):
    res = await asyncio.gather(*[orch.process_symbol("EURUSD") for _ in range(4)])
    assert sum(len(r) for r in res) == 1
    assert len(orch.engine.list_positions()) == 1


async def test_kill_switch_blocks_new_positions(orch):
    settings_store.update({"kill_switch": True})
    out = await orch.process_symbol("EURUSD")
    assert out[0]["status"] == "blocked" and any("kill_switch" in r for r in out[0]["risk"]["reasons"])
    assert not orch.engine.list_positions()
    settings_store.update({"kill_switch": False})


async def test_manual_approval_mode(orch):
    settings_store.update({"auto_execute": False})
    out = await orch.process_symbol("EURUSD")
    assert out[0]["status"] == "pending" and not orch.engine.list_positions()
    pid = out[0]["id"]
    bad = await orch.decide_proposal(pid, "ok go")
    assert bad["reply_accepted"] is False and not orch.engine.list_positions()
    good = await orch.decide_proposal(pid, "yes")
    assert good["status"] == "executed" and good["decision"] == "user" and len(orch.engine.list_positions()) == 1
    settings_store.update({"auto_execute": True})


async def test_manual_reject_and_expiry(orch):
    settings_store.update({"auto_execute": False})
    out = await orch.process_symbol("EURUSD")
    r = await orch.decide_proposal(out[0]["id"], reject=True)
    assert r["status"] == "rejected" and not orch.engine.list_positions()
    settings_store.update({"auto_execute": True})


async def test_disabled_agent_is_skipped_by_automation(orch):
    orch.set_config("entry", enabled=False)
    assert await orch.process_symbol("EURUSD") == []
    assert not orch.engine.list_positions()
    manual = await orch.run_agent("entry", "manual", symbol="EURUSD")
    assert manual.output["status"] == "triggered" and any("désactivé" in w for w in manual.warnings)


async def test_timeout_retry_and_error_audit(orch, monkeypatch):
    calls = {"n": 0}

    async def boom(self, ctx, **kw):
        calls["n"] += 1
        raise RuntimeError("panne simulée")
    monkeypatch.setattr(type(AGENTS["news"]), "run", boom)
    r = await orch.run_agent("news", "manual", symbols=["EURUSD"])
    assert r.status == "error" and calls["n"] == 2                      # 1 tentative + 1 reprise
    assert runs("news")[-1][1] == "error"

    async def slow(self, ctx, **kw):
        await asyncio.sleep(5)
    monkeypatch.setattr(type(AGENTS["alerts"]), "run", slow)
    orch.set_config("alerts", params={"timeout_s": 0.2})
    t = await orch.run_agent("alerts", "manual")
    assert t.status == "error" and "délai" in t.summary
    with session_scope() as s:
        assert s.query(Notification).filter(Notification.title.like("%en échec%")).count() >= 1


async def test_agent_config_validation_and_reset(orch):
    with pytest.raises(KeyError):
        orch.set_config("entry", params={"inconnu": 1})
    c = orch.set_config("entry", params={"or_minutes": 30}, model="minimax/minimax-m3.1-flash")
    assert c["effective_params"]["or_minutes"] == 30 and c["model"]
    assert orch.reset_config("entry")["effective_params"]["or_minutes"] == 15


async def test_bar_event_pipeline_and_scheduler(orch):
    with session_scope() as s:
        s.add(WatchItem(symbol="EURUSD", source="manual"))
    await orch.start()
    try:
        await orch.bus.publish("bar_closed", {"symbol": "EURUSD", "tf": "5m", "bar": {}})
        for _ in range(100):
            await asyncio.sleep(0.1)
            if orch.engine.list_positions():
                break
        assert orch.engine.list_positions()
        assert orch.status()["counters"]["executed"] == 1
    finally:
        await orch.stop()
