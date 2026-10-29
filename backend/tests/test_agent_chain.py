"""Chaîne complète Entry → Exit → TimeMachine → Budget → Risk → Broker sur monde synthétique (aucun réseau)."""
import datetime as dt

import pytest

from app import settings_store
from app.agents.broker import BrokerAgent
from app.agents.budget import BudgetAgent
from app.agents.entry import EntryAgent
from app.agents.exit import ExitAgent
from app.agents.risk import RiskAgent
from app.agents.timemachine import TimeMachineAgent
from tests.helpers import make_ctx, make_world

TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 8, 30)


@pytest.fixture
def ctx(db, tmp_path):
    settings_store.reset_cache()
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    c = make_ctx(feed, NOW, tmp_path, agent_params={"replays": 800, "max_signal_age_min": 240})
    c.pid = c.engine.create_portfolio("chain", 10000, leverage=30)["id"]
    return c


async def chain(ctx, reply=None):
    e = await EntryAgent().run(ctx, symbol="EURUSD")
    assert e.output["status"] == "triggered", e.summary
    ctx.results["entry"] = e
    t = e.output["trigger"]
    # le marché synthétique a continué de monter depuis la cassure : on recale la cotation sur le prix du signal (signal « frais »)
    from app.market.provider import Quote
    from tests.helpers import real_now
    px = t["price"]
    ctx.feed.quotes["EURUSD"] = Quote("EURUSD", px * 0.99999, px * 1.00001, px, None, real_now(), "open")
    x = await ExitAgent().run(ctx, symbol="EURUSD", entry=t["price"], opening_range=e.output["opening_range"], entry_time=t["closes_at"])
    ctx.results["exit"] = x
    ctx.results["timemachine"] = await TimeMachineAgent().run(ctx, plan=x.output["plan"])
    b = await BudgetAgent().run(ctx, plan=x.output["plan"], portfolio_id=ctx.pid)
    ctx.results["budget"] = b
    r = await RiskAgent().run(ctx, portfolio_id=ctx.pid)
    ctx.results["risk"] = r
    prop = {"portfolio_id": ctx.pid, "sizing": b.output, "risk": r.output, "plan": x.output["plan"]}
    return e, x, b, r, prop


async def test_full_chain_auto_executes_paper(ctx):
    settings_store.update({"auto_execute": True})
    e, x, b, r, prop = await chain(ctx)
    bo = b.output
    assert bo["qty"] > 0 and bo["max_loss_usd"] <= 50.0 + 1e-6      # 0,5 % de 10 000
    assert bo["qty"] % 1000 == 0
    assert r.output["verdict"] == "PASS", r.output["reasons"]
    res = await BrokerAgent().run(ctx, proposal=prop)
    assert res.output["status"] == "executed", res.summary
    assert res.output["decision"] == "auto"
    pos = ctx.engine.list_positions(ctx.pid)
    assert len(pos) == 1 and pos[0]["qty"] == bo["qty"]
    assert pos[0]["plan"]["flat_by_utc"] == x.output["plan"]["flat_by_utc"]
    legs = [o for o in ctx.engine.list_orders(ctx.pid) if o["role"] in ("stop_loss", "take_profit")]
    assert {o["role"] for o in legs} == {"stop_loss", "take_profit"}
    # un second envoi identique est refusé (doublon)
    again = await BrokerAgent().run(ctx, proposal=prop)
    assert again.output["status"] == "refused"
    assert ctx.engine.replay_from_fills(ctx.pid)["consistent"]


async def test_manual_approval_flow(ctx):
    settings_store.update({"auto_execute": False})
    *_, prop = await chain(ctx)
    pend = await BrokerAgent().run(ctx, proposal=prop)
    assert pend.output["status"] == "pending_approval" and not ctx.engine.list_positions(ctx.pid)
    bad = await BrokerAgent().run(ctx, proposal=prop, reply="ok go")
    assert bad.output["status"] == "pending_approval" and not ctx.engine.list_positions(ctx.pid)
    wrong = await BrokerAgent().run(ctx, proposal=prop, reply="yes 12345")
    assert wrong.output["status"] == "pending_approval"
    good = await BrokerAgent().run(ctx, proposal=prop, reply="oui")
    assert good.output["status"] == "executed" and good.output["decision"] == "user"


async def test_broker_refuses_without_pass_or_tampered(ctx):
    settings_store.update({"auto_execute": True})
    *_, prop = await chain(ctx)
    blocked = {**prop, "risk": {**prop["risk"], "verdict": "BLOCK"}}
    assert (await BrokerAgent().run(ctx, proposal=blocked)).output["status"] == "refused"
    tampered = {**prop, "risk": {**prop["risk"], "checked_order": {**prop["risk"]["checked_order"], "qty": "999"}}}
    r = await BrokerAgent().run(ctx, proposal=tampered)
    assert r.output["status"] == "refused" and "empreinte" in r.output["reason"]
    assert not ctx.engine.list_positions(ctx.pid)


async def test_risk_blocks(ctx):
    *_, prop = await chain(ctx)
    sz = ctx.results["budget"].output
    # kill switch
    settings_store.update({"kill_switch": True})
    r = await RiskAgent().run(ctx, portfolio_id=ctx.pid)
    assert r.output["verdict"] == "BLOCK" and any("kill_switch" in x for x in r.output["reasons"])
    settings_store.update({"kill_switch": False})
    # signal trop vieux
    ctx.agent_params["max_signal_age_min"] = 5
    r = await RiskAgent().run(ctx, portfolio_id=ctx.pid)
    assert r.output["verdict"] == "BLOCK" and any("age_du_signal" in x for x in r.output["reasons"])
    ctx.agent_params["max_signal_age_min"] = 240
    # perte max trop grande (limite du portefeuille resserrée)
    ctx.engine.update_portfolio(ctx.pid, risk={**ctx.engine.portfolio_summary(ctx.pid)["risk"], "max_loss_per_trade_usd": 1.0})
    r = await RiskAgent().run(ctx, portfolio_id=ctx.pid)
    assert r.output["verdict"] == "BLOCK" and any("loss" in x.lower() for x in r.output["reasons"])
    assert sz["qty"] > 0
