"""Agent Broker — skill source : trader-broker (ticket.py).

Le skill d'origine affiche un ticket en clair, n'autorise un ordre que s'il a obtenu PASS de trader-risk (empreinte
identique), exige un « yes » explicite pour CET ordre, puis l'envoie au courtier. Ici :
  * le courtier est le MOTEUR PAPIER (aucun argent réel, aucune connexion externe) ;
  * `ticket.fingerprint` vérifie que l'ordre exécuté est exactement celui que Risk a validé ;
  * `ticket.parse_yes` valide la réponse de l'utilisateur quand `auto_execute` est désactivé (ordre par ordre, montants cités vérifiés) ;
  * quand `auto_execute` est activé (choix explicite de l'utilisateur de cette application), l'approbation est une DÉCISION DE POLITIQUE
    journalisée (`decision: auto`) — jamais un faux « yes » — et le contrôle Risk reste obligatoire ;
  * un ordre n'est « exécuté » que si le moteur confirme un remplissage.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from .. import settings_store
from ..db import session_scope
from ..models import Order, utcnow
from ..paper.engine import OrderRejected, OrderRequest
from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill


def real_order(sizing: dict, digits: int) -> dict[str, Any]:
    """Ordre en unités réelles, au format des champs du skill (chaînes) — pour le ticket et parse_yes."""
    o = {"symbol": sizing["symbol"], "side": "sell" if sizing.get("side") == "sell" else "buy", "qty": f"{sizing['qty']:g}", "type": sizing.get("order_type", "market"),
         "time_in_force": "day", "order_class": "bracket",
         "take_profit_limit_price": f"{sizing['target']:.{digits}f}", "stop_loss_stop_price": f"{sizing['stop']:.{digits}f}",
         "plan": {"entry": sizing["entry"], "stop": sizing["stop"], "target": sizing["target"], "max_loss": sizing["max_loss_usd"]}}
    if o["type"] == "limit":
        o["limit_price"] = f"{sizing['entry']:.{digits}f}"
    return o


def ticket_text(sizing: dict, digits: int) -> list[str]:
    typ = "au marché" if sizing.get("order_type", "market") == "market" else f"limite {sizing['entry']:.{digits}f}"
    verb = "VENTE À DÉCOUVERT" if sizing.get("side") == "sell" else "ACHAT"
    return [f"{verb} {sizing['qty']:g} {sizing['symbol']}, {typ} (≈ {sizing['notional_usd']:,.2f} USD, marge ≈ {sizing['margin_usd']:,.2f} USD)",
            f"stop-loss {sizing['stop']:.{digits}f}", f"take-profit {sizing['target']:.{digits}f}",
            f"perte max ≈ {sizing['max_loss_usd']:,.2f} USD si le stop s'exécute au prix du stop (peut être pire en marché rapide)",
            "PAPER TRADING : aucune somme réelle n'est engagée."]


def _yes(reply: str) -> str:
    """Accepte « oui » en début de réponse comme « yes » (écart documenté avec le skill, anglophone)."""
    return re.sub(r"^\s*oui\b", "yes", reply or "", flags=re.I)


class BrokerAgent(Agent):
    id = "broker"
    name = "Agent Broker"
    mission = "Transformer une proposition validée par Risk en ordre bracket papier (entrée + stop + cible) et suivre son exécution."
    skill = "trader-broker"
    implementation = "partial"
    limitations = [
        "Courtier = moteur papier interne : pas de MCP Alpaca ni de compte externe (le mode live n'existe pas dans cette version).",
        "Utilisés du skill : empreinte d'ordre (fingerprint), validation du « yes » (parse_yes) avec « oui » accepté en plus. Non repris : fichiers state/broker.json et portfolio.json (remplacés par la base SQLite).",
        "Mode auto_execute : l'approbation par l'utilisateur est remplacée par une décision de politique journalisée ; les contrôles Risk, fraîcheur et moteur restent appliqués.",
    ]
    dependencies = ["Agent Risk (PASS obligatoire)", "moteur papier"]
    default_params = {"order_type": "market"}
    param_help = {"order_type": "market (par défaut) ou limit"}
    inputs = ["proposal (sizing + risk + plan)", "reply (réponse de l'utilisateur, si approbation manuelle)", "mode: submit | flatten"]
    outputs = ["status (executed|working|rejected|pending_approval|refused)", "order", "ticket[]", "decision (auto|user)"]

    async def run(self, ctx: AgentContext, proposal: dict | None = None, reply: str | None = None, mode: str = "submit", **kw: Any) -> AgentResult:
        if mode == "flatten":
            return self.flatten(ctx, kw.get("portfolio_id"), kw.get("symbols"))
        t = load_skill("broker")
        if not proposal:
            return AgentResult("skipped", "Aucune proposition à exécuter.", {"status": "refused"})
        sizing, risk = proposal.get("sizing") or {}, proposal.get("risk") or {}
        symbol = (sizing.get("symbol") or proposal.get("symbol") or "").upper()
        pid = int(proposal.get("portfolio_id") or sizing.get("portfolio_id"))
        pid_ref = proposal.get("proposal_id")
        refuse = lambda why, **x: AgentResult("ok", f"{symbol} : non exécuté — {why}", {"status": "refused", "reason": why, **x}, [why], "none", symbol)  # noqa: E731
        # 1. Risk obligatoire, et c'est bien CET ordre qui a passé
        if risk.get("verdict") != "PASS":
            return refuse("l'Agent Risk n'a pas rendu PASS")
        checked = risk.get("checked_order")
        if not checked or not risk.get("fingerprint") or t.fingerprint(checked) != risk["fingerprint"]:
            return refuse("l'empreinte de l'ordre ne correspond pas au contrôle Risk (ordre modifié après validation)")
        if float(sizing.get("qty") or 0) <= 0 or sizing.get("refusal"):
            return refuse("quantité nulle")
        if settings_store.get("kill_switch"):
            return refuse("interrupteur d'arrêt global actif")
        try:
            inst = ctx.feed.info(symbol)
        except KeyError:
            return refuse("instrument inconnu")
        d = inst.digits or 5
        rorder = real_order(sizing, d)
        text = ticket_text(sizing, d)
        # 2. doublon (même empreinte déjà envoyée aujourd'hui sur ce portefeuille)
        fp = risk["fingerprint"] + (":sell" if sizing.get("side") == "sell" else "")  # un achat et une vente identiques ne sont pas des doublons
        today0 = ctx.clock().replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope() as s:
            for o in s.scalars(select(Order).where(Order.portfolio_id == pid, Order.created_at >= today0, Order.role.in_(("entry", "standalone")))):
                if (o.meta or {}).get("fingerprint") == fp and o.status not in ("rejected", "cancelled"):
                    return refuse("un ordre identique a déjà été envoyé aujourd'hui", order_id=o.id)
        # 3. approbation
        if settings_store.get("auto_execute") and reply is None:
            decision, why = "auto", "exécution automatique (politique de l'utilisateur : auto_execute activé)"
        elif reply is not None:
            ok, why = t.parse_yes(_yes(reply), rorder, ())
            if not ok:
                return AgentResult("ok", f"{symbol} : réponse refusée — {why}", {"status": "pending_approval", "ticket": text, "reason": why, "decision": "user"},
                                   [why], ctx.quality(symbol), symbol)
            decision = "user"
        else:
            return AgentResult("ok", f"{symbol} : en attente de votre accord (auto_execute désactivé).",
                               {"status": "pending_approval", "ticket": text, "decision": "user", "expects": "une réponse commençant par « yes » ou « oui »"},
                               [], ctx.quality(symbol), symbol)
        # 4. envoi au moteur papier
        plan = {**(proposal.get("plan") or {}), "entry": sizing["entry"], "stop": sizing["stop"], "target": sizing["target"],
                "flat_by_utc": sizing.get("flat_by_utc") or (proposal.get("plan") or {}).get("flat_by_utc"), "flat_by": sizing.get("flat_by")}
        req = OrderRequest(portfolio_id=pid, symbol=symbol, side="sell" if sizing.get("side") == "sell" else "buy", type=sizing.get("order_type", "market"), qty=float(sizing["qty"]),
                           limit_price=sizing["entry"] if sizing.get("order_type") == "limit" else None, tif="day",
                           stop_loss=sizing["stop"], take_profit=sizing["target"], source="agent:broker", proposal_id=pid_ref, plan=plan,
                           role="entry", meta={"fingerprint": fp, "max_loss_usd": sizing["max_loss_usd"], "decision": decision,
                                               "approval": why, "correlation_id": ctx.correlation_id})
        try:
            view = ctx.engine.place_order(req)
        except OrderRejected as e:
            return AgentResult("ok", f"{symbol} : refusé par le moteur — {e}", {"status": "rejected", "reason": str(e), "ticket": text, "decision": decision},
                               [str(e)], ctx.quality(symbol), symbol)
        st = {"filled": "executed", "open": "working", "triggered": "working"}.get(view["status"], view["status"])
        fill = view.get("avg_fill_price")
        msg = {"executed": f"exécuté (papier) à {fill}", "working": "ordre actif, pas encore exécuté", "rejected": f"refusé : {view.get('reject_reason')}"}.get(st, st)
        return AgentResult("ok" if st != "rejected" else "partial", f"{symbol} : {sizing['qty']:g} — {msg} [{decision}]",
                           {"status": st, "order": view, "ticket": text, "decision": decision, "approval": why, "fingerprint": fp},
                           [view["reject_reason"]] if st == "rejected" and view.get("reject_reason") else [], ctx.quality(symbol), symbol)

    def flatten(self, ctx: AgentContext, portfolio_id: int | None, symbols: list[str] | None = None) -> AgentResult:
        """Clôture au marché (papier) les positions du portefeuille — équivalent « flatten » du skill. Action explicite de l'utilisateur."""
        closed, errors = [], []
        for pos in ctx.engine.list_positions(portfolio_id):
            if symbols and pos["symbol"] not in {s.upper() for s in symbols}:
                continue
            try:
                o = ctx.engine.close_position(pos["portfolio_id"], pos["symbol"], source="agent:broker:flatten")
                (closed if o["status"] == "filled" else errors).append({"symbol": pos["symbol"], "status": o["status"], "reason": o.get("reject_reason")})
            except Exception as e:  # noqa: BLE001
                errors.append({"symbol": pos["symbol"], "error": str(e)})
        return AgentResult("partial" if errors else "ok", f"{len(closed)} position(s) clôturée(s), {len(errors)} échec(s).", {"closed": closed, "errors": errors})
