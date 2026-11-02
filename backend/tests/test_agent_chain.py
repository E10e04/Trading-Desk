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
    assert r.output["verdict"] == "BLOCK" and any(("loss" in x.lower() or "perte" in x.lower()) for x in r.output["reasons"])
    assert sz["qty"] > 0


# ---------------------------------------------------------------- vente à découvert (miroir de prix)
@pytest.fixture
def sctx(db, tmp_path):
    settings_store.reset_cache()
    settings_store.update({"allow_short": True, "auto_execute": True})
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10, "down": True}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    c = make_ctx(feed, NOW, tmp_path, agent_params={"replays": 800, "max_signal_age_min": 240})
    c.pid = c.engine.create_portfolio("short", 10000, leverage=30)["id"]
    c.engine.update_portfolio(c.pid, risk={"allow_short": True})
    return c


async def short_chain(ctx):
    e = await EntryAgent().run(ctx, symbol="EURUSD", side="sell")
    assert e.output["status"] == "triggered", e.summary
    assert e.output["side"] == "sell" and e.output["signal_key"].endswith("|short")
    ctx.results["entry"] = e
    t = e.output["trigger"]
    from app.market.provider import Quote
    from tests.helpers import real_now
    px = t["price"]
    ctx.feed.quotes["EURUSD"] = Quote("EURUSD", px * 0.99999, px * 1.00001, px, None, real_now(), "open")
    x = await ExitAgent().run(ctx, symbol="EURUSD", entry=px, opening_range=e.output["opening_range"], entry_time=t["closes_at"], side="sell")
    assert x.status == "ok", x.summary
    ctx.results["exit"] = x
    ctx.results["timemachine"] = await TimeMachineAgent().run(ctx, plan=x.output["plan"])
    b = await BudgetAgent().run(ctx, plan=x.output["plan"], portfolio_id=ctx.pid)
    ctx.results["budget"] = b
    r = await RiskAgent().run(ctx, portfolio_id=ctx.pid)
    return e, x, b, r, {"portfolio_id": ctx.pid, "sizing": b.output, "risk": r.output, "plan": x.output["plan"]}


async def test_entry_vente_ne_se_declenche_que_sur_cassure_a_la_baisse(ctx, sctx):
    up = await EntryAgent().run(ctx, symbol="EURUSD", side="sell")        # séance haussière : pas de signal de vente
    assert up.output["status"] != "triggered"
    down = await EntryAgent().run(sctx, symbol="EURUSD", side="buy")      # séance baissière : pas de signal d'achat
    assert down.output["status"] != "triggered"
    sell = await EntryAgent().run(sctx, symbol="EURUSD", side="sell")
    assert sell.output["status"] == "triggered"
    orr, trig = sell.output["opening_range"], sell.output["trigger"]
    assert trig["price"] < orr["low"] < orr["high"] and trig["pct_over"] > 0


async def test_chaine_complete_vente_a_decouvert(sctx):
    e, x, b, r, prop = await short_chain(sctx)
    plan = x.output["plan"]
    assert plan["side"] == "sell" and plan["stop"] > plan["entry"] > plan["target"] and plan["reward_risk"] > 0
    assert b.output["side"] == "sell" and b.output["qty"] > 0 and b.output["max_loss_usd"] <= 50.0 + 1e-6
    assert r.output["verdict"] == "PASS", r.output["reasons"]
    res = await BrokerAgent().run(sctx, proposal=prop)
    assert res.output["status"] == "executed", res.summary
    pos = sctx.engine.list_positions(sctx.pid)
    assert len(pos) == 1 and pos[0]["side"] == "short" and pos[0]["qty"] == -b.output["qty"]
    legs = {o["role"]: o for o in sctx.engine.list_orders(sctx.pid) if o["role"] in ("stop_loss", "take_profit")}
    assert legs["stop_loss"]["side"] == "buy" and legs["stop_loss"]["stop_price"] == plan["stop"]
    assert legs["take_profit"]["limit_price"] == plan["target"]
    assert (await BrokerAgent().run(sctx, proposal=prop)).output["status"] == "refused"   # doublon
    assert sctx.engine.replay_from_fills(sctx.pid)["consistent"]


async def test_risk_bloque_la_vente_sans_les_interrupteurs(sctx):
    *_, b, r, prop = (await short_chain(sctx))
    settings_store.update({"allow_short": False})
    r2 = await RiskAgent().run(sctx, portfolio_id=sctx.pid)
    assert r2.output["verdict"] == "BLOCK" and any("vente à découvert" in x for x in r2.output["reasons"])
    settings_store.update({"allow_short": True})
    sctx.engine.update_portfolio(sctx.pid, risk={"allow_short": False})
    r3 = await RiskAgent().run(sctx, portfolio_id=sctx.pid)
    assert r3.output["verdict"] == "BLOCK"


def test_messages_risk_en_francais():
    from app.agents.risk import fr
    assert fr("trades: 3 entries placed today plus 0 passed earlier in this batch, your limit is 3 a day").startswith("limite de trades atteinte : 3 entrée(s)")
    assert "perte max $5 (limite $10)" in fr("max loss $5 (limit $10), reward:risk 2.00 (min 1)")
    assert "sur 3" in fr("today before this: 3 of 3 trades, $1 of $2 daily loss")
    assert fr("message inconnu") == "message inconnu"
