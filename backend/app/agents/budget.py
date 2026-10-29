"""Agent Budget — skill source : trader-budget (size.py).

`size_trade` (fonction pure du skill) dimensionne le trade : quantité = minimum de quatre plafonds (perte max par
trade, % de position, ordre max, cash après réserve). `bracket_order` fabrique l'ordre. Adaptation :
  * calcul en « actions virtuelles » (voir adapt.py), reconverti en quantité réelle (multiple du pas de l'instrument) ;
  * « budget » = pouvoir d'achat du portefeuille papier (equity x levier), « cash » = marge libre x levier ;
  * la perte maximale est RECALCULÉE en prix réels (qty x (entrée - stop) x taux USD) : si elle dépasse la limite
    à cause d'arrondis de précision, la quantité est réduite d'un pas. Aucun calcul n'est fait par l'IA.
"""
from __future__ import annotations

from typing import Any

from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill


class BudgetAgent(Agent):
    id = "budget"
    name = "Agent Budget"
    mission = "Dimensionner la position : quantité maximale respectant perte max par trade, taille de position, ordre max et marge disponible."
    skill = "trader-budget"
    implementation = "full"
    limitations = [
        "Mode « un plan = un ordre » (run_plan) du skill ; le mode d'allocation de poids (`--weights`) n'est pas utilisé.",
        "Pouvoir d'achat = equity x levier du portefeuille papier (le skill raisonne en cash d'un compte actions sans levier).",
        "Prix et quantités convertis via le lot virtuel ; la perte max est revérifiée en prix réels et la quantité réduite d'un pas si besoin.",
        "Fractions : la quantité réelle est un multiple du pas de l'instrument (forex 1 000, métaux 0,1, crypto 0,001...).",
    ]
    dependencies = ["Agent Exit (plan)", "portefeuille papier (equity, marge libre)", "cotation fraîche"]
    default_params = {"order_type": "market", "session_anchor_utc": "07:00"}
    param_help = {"order_type": "market ou limit (limit : au prix d'entrée du plan)"}
    inputs = ["plan (Agent Exit)", "portfolio_id"]
    outputs = ["qty (réelle)", "entry/stop/target", "max_loss_usd", "notional_usd", "margin_usd", "bound_by", "limits[]", "refusal"]

    async def run(self, ctx: AgentContext, plan: dict | None = None, portfolio_id: int | None = None, **_: Any) -> AgentResult:
        z = load_skill("budget")
        p = params_of(self, ctx)
        plan = plan or (ctx.results.get("exit").output.get("plan") if ctx.results.get("exit") else None)
        if not plan or portfolio_id is None:
            return AgentResult("skipped", "Plan et portefeuille requis.", {})
        symbol = plan["symbol"].upper()
        try:
            inst, v, m = ctx.virtual(symbol)
            summ = ctx.engine.portfolio_summary(int(portfolio_id))
        except (KeyError, ValueError) as ex:
            return AgentResult("error", f"{symbol} : {ex}", {"symbol": symbol}, instrument=symbol)
        d = inst.digits or 5
        q = ctx.feed.quotes.get(symbol)
        risk_cfg = dict(summ["risk"] or {})
        lev = float(summ["leverage"] or 1)
        equity = float(summ["equity"])
        if equity <= 0:
            return AgentResult("ok", f"{symbol} : equity ≤ 0, aucun ordre.", {"symbol": symbol, "qty": 0, "refusal": "equity épuisée"}, [], ctx.quality(symbol), symbol)
        buying_power = equity * lev
        cash = max(float(summ["free_margin"]), 0.0) * lev
        cfg = {k: risk_cfg[k] for k in ("cash_buffer_pct", "max_position_pct", "max_order_usd", "min_order_usd", "max_loss_per_trade_usd",
                                        "max_daily_loss_usd", "max_trades_per_day", "max_orders_per_day", "min_reward_risk", "require_stop",
                                        "blocked") if k in risk_cfg}
        vplan = {"symbol": symbol, "side": "buy", "date": plan["date"], "setup": plan.get("setup", ""), "entry": v.vp(plan["entry"]),
                 "stop": v.vp(plan["stop"]), "target": v.vp(plan["target"]), "entry_time": plan.get("entry_time") or "",
                 "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""), "flat_by": plan.get("flat_by")}
        now = ctx.clock()
        today = load_skill("entry").to_eastern(m.fake_utc(now)).date().isoformat()
        live_ask = (q.ask or q.mid) if q else None
        limit = v.vp(plan["entry"]) if p["order_type"] == "limit" else None
        warnings: list[str] = []
        try:
            s = z.size_trade(vplan, cfg, budget=buying_power, limit=limit, price=v.vp(live_ask) if live_ask else None,
                             cash=cash, today=today)
        except z.SizeError as ex:
            return AgentResult("ok", f"{symbol} : pas d'ordre — {ex}", {"symbol": symbol, "qty": 0, "refusal": str(ex)}, [], ctx.quality(symbol), symbol)
        qv = int(s["qty"])
        entry_ref = v.rp(s["entry"])
        stop, target = plan["stop"], plan["target"]
        risk_unit = max(entry_ref - stop, 0.0)
        loss_limit = cfg.get("max_loss_per_trade_usd")
        # revérification en prix réels : perte max exacte
        while qv > 0 and loss_limit and qv * v.lot * risk_unit * v.fx > float(loss_limit) + 1e-9:
            qv -= 1
        # plafond d'exposition brute du portefeuille (règle backend, identique à celle de l'Agent Risk)
        bound_by = s["bound_by"]
        exp_cap = risk_cfg.get("max_exposure_pct")
        extra_limits = []
        if exp_cap and entry_ref > 0:
            room_usd = max(float(exp_cap) / 100.0 * equity - float(summ["exposure_usd"]), 0.0)
            cap_v = int(room_usd // (entry_ref * v.lot * v.fx))
            extra_limits.append({"name": "exposition brute", "usd": round(room_usd, 2), "qty": v.rq(cap_v), "binds": cap_v < qv,
                                 "how": f"{exp_cap} % de l'equity {equity:,.2f} − exposition actuelle {summ['exposure_usd']:,.2f} = {room_usd:,.2f} USD"})
            if cap_v < qv:
                qv = cap_v
                bound_by = "exposition brute"
        qty = v.rq(qv)
        refusal = s["refusal"]
        if qv == 0 and not refusal:
            refusal = "la quantité tombe à zéro après revérification de la perte maximale en prix réels"
        max_loss = round(qty * risk_unit * v.fx, 2)
        notional = round(qty * entry_ref * v.fx, 2)
        margin = round(notional / lev, 2)
        rr = round((target - entry_ref) / risk_unit, 2) if risk_unit > 0 else 0.0
        limits = [{"name": l["name"], "usd": l["usd"], "qty": v.rq(l["shares"]), "binds": l.get("binds", False), "how": l["how"]} for l in s["limits"]] + extra_limits
        out = {"symbol": symbol, "portfolio_id": int(portfolio_id), "qty": qty, "qty_virtual": qv, "lot": v.lot, "fx_to_usd": v.fx,
               "entry": round(entry_ref, d + 1), "entry_from": s["entry_from"], "stop": stop, "target": target,
               "risk_per_unit": round(risk_unit, d + 1), "reward_risk": rr, "max_loss_usd": max_loss, "notional_usd": notional,
               "margin_usd": margin, "bound_by": bound_by, "limits": limits, "refusal": refusal,
               "order_type": s["order_type"], "buying_power_usd": round(buying_power, 2), "cash_available_usd": round(cash, 2),
               "flat_by": plan.get("flat_by"), "flat_by_utc": plan.get("flat_by_utc"), "setup": plan.get("setup")}
        if qv > 0 and not refusal:
            # le skill ne connaît que ses 4 plafonds : on lui présente le plafond « cash » si c'est l'exposition brute qui borne
            vorder = z.bracket_order({**s, "qty": qv, "refusal": None, "bound_by": s["bound_by"], "max_loss": round(qv * (s["entry"] - s["stop"]), 2),
                                      "cost": round(qv * s["entry"], 2)})
            if bound_by == "exposition brute":
                vorder["why"] = vorder["why"].replace("cash after the buffer", "gross exposure cap")
            out["virtual_order"] = vorder
        if refusal:
            return AgentResult("ok", f"{symbol} : aucun ordre — {refusal}", out, warnings, ctx.quality(symbol), symbol)
        return AgentResult("ok", f"{symbol} : {qty:g} unités (≈ {notional:,.0f} USD, marge {margin:,.0f} USD), perte max {max_loss:,.2f} USD, "
                                 f"plafond atteint : {bound_by}.", out, warnings, ctx.quality(symbol), symbol)
