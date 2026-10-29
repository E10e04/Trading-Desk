"""Agent Alerts — skill source : trader-alerts (alerts.py).

Réutilise `validate`, `active`, `evaluate` (machine d'état des règles : above/below/stop/target/pct_move/opening_bell/time,
une sonnerie par règle, ré-armement optionnel) et `rules_from_plan` (alertes stop + cible + flat-by d'un plan).
Adaptations :
  * les règles sont enregistrées en PRIX RÉELS (table alert_rules) ; elles sont converties en prix virtuels pour `evaluate`,
    et les messages sont réécrits en unités réelles ;
  * heures « time » = heures de la séance mappée (voir adapt.py) ; `opening_bell` = ouverture de la séance mappée ;
  * AUCUNE alerte de prix n'est déclenchée sur une cotation périmée ou un marché fermé (anti faux positifs) ;
  * les alertes opérationnelles (flux coupé, cotations périmées, IA en erreur, interrupteur d'arrêt) sont produites par
    `operational()` — ajout propre à cette application, absent du skill ;
  * pas de récupération de prix Yahoo/Stooq : les prix viennent du flux BiQuote.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select

from .. import settings_store
from ..db import session_scope
from ..models import AlertRule, utcnow
from ..notify import notify
from .base import Agent, AgentContext, AgentResult
from .loader import load_skill


class AlertsAgent(Agent):
    id = "alerts"
    name = "Agent Alerts"
    mission = "Surveiller les niveaux (stop, cible, seuils, mouvements, heures) et les incidents du système, et notifier une seule fois par règle."
    skill = "trader-alerts"
    implementation = "full"
    limitations = [
        "Prix fournis par le flux BiQuote (pas de Yahoo/Stooq) ; aucune alerte de prix sur cotation périmée ou marché fermé.",
        "Les heures des règles « time » et l'ouverture « opening_bell » suivent la séance mappée (marchés continus : 6h30 depuis l'ancrage UTC).",
        "Notifications dans l'application (cloche + WebSocket) ; pas d'e-mail/SMS/push externe dans cette version.",
        "Alertes opérationnelles (flux, IA, interrupteur) = ajout de l'application, absentes du skill.",
    ]
    dependencies = ["flux de cotations", "table alert_rules"]
    default_interval_s = 15
    default_params = {"session_anchor_utc": "07:00", "operational": True, "stale_throttle_s": 900}
    param_help = {"operational": "produire aussi les alertes opérationnelles (flux, IA, interrupteur d'arrêt)",
                  "stale_throttle_s": "délai minimal entre deux alertes opérationnelles identiques"}
    inputs = ["(aucune : lit les règles actives)"]
    outputs = ["rings[] (règles déclenchées)", "skipped_stale[]", "errors[]", "operational[]"]

    # ------------------------------------------------------------------ règles
    @staticmethod
    def add_rule(rule: dict) -> dict:
        a = load_skill("alerts")
        a.validate({**rule, "id": rule.get("id") or "x"})
        with session_scope() as s:
            row = AlertRule(rule=rule, active=True)
            s.add(row)
            s.flush()
            if not rule.get("id"):
                row.rule = {**rule, "id": f"rule-{row.id}"}
            return {"id": row.id, "rule": row.rule, "active": True}

    @staticmethod
    def add_from_plan(plan: dict, portfolio_id: int | None = None) -> list[dict]:
        a = load_skill("alerts")
        rules = a.rules_from_plan({**plan, "date": plan.get("date")}, plan.get("flat_by"))
        out = []
        for r in rules:
            r = {**r, "position_alert": True, "portfolio_id": portfolio_id}
            r["id"] = f"{r['id']}-{int(utcnow().timestamp())}"
            out.append(AlertsAgent.add_rule(r))
        return out

    @staticmethod
    def list_rules() -> list[dict]:
        with session_scope() as s:
            return [{"id": r.id, "rule": r.rule, "active": r.active, "last_rang": r.last_rang.isoformat() + "Z" if r.last_rang else None}
                    for r in s.scalars(select(AlertRule).order_by(AlertRule.id.desc()))]

    @staticmethod
    def set_active(rule_id: int, active: bool) -> None:
        with session_scope() as s:
            row = s.get(AlertRule, rule_id)
            if not row:
                raise KeyError("règle introuvable")
            row.active = active
            if active:
                row.state = {}

    @staticmethod
    def delete_rule(rule_id: int) -> None:
        with session_scope() as s:
            row = s.get(AlertRule, rule_id)
            if row:
                s.delete(row)

    # ------------------------------------------------------------------ exécution
    async def run(self, ctx: AgentContext, **_: Any) -> AgentResult:
        a = load_skill("alerts")
        e = load_skill("entry")
        now = ctx.clock()
        rings_out, stale, errors = [], [], []
        open_syms = {(p["portfolio_id"], p["symbol"]) for p in ctx.engine.list_positions()}
        with session_scope() as s:
            rows = list(s.scalars(select(AlertRule).where(AlertRule.active.is_(True))))
            data = [(r.id, dict(r.rule), dict(r.state or {})) for r in rows]
        updates: dict[int, dict] = {}
        deactivate: list[int] = []
        for rid, rule, state in data:
            sym = (rule.get("symbol") or "").upper()
            # règles de position : plus de position ouverte => la règle n'a plus d'objet
            if rule.get("position_alert") and (rule.get("portfolio_id"), sym) not in open_syms and rule.get("portfolio_id") is not None:
                if rule.get("armed_seen"):
                    deactivate.append(rid)
                    continue
                if (rule.get("portfolio_id"), sym) in open_syms:
                    rule["armed_seen"] = True
            try:
                if sym:
                    inst, v, m = ctx.virtual(sym)
                else:
                    inst, v, m = None, None, None
                    from .adapt import mapper_for
                    from ..market.provider import Instrument  # noqa: F401
                fake_now = m.fake_utc(now) if m else now
                status = a.market_status(fake_now)
                vrule = dict(rule)
                if v is not None:
                    for k in ("price", "ref"):
                        if vrule.get(k) is not None:
                            vrule[k] = v.vp(float(vrule[k]))
                quotes = {}
                if sym:
                    fr = ctx.feed.freshness(sym)
                    q = ctx.feed.quotes.get(sym)
                    needs_quote = rule["type"] not in a.NO_QUOTE
                    if needs_quote and (not q or fr["status"] != "live"):
                        stale.append({"rule": rule.get("id"), "symbol": sym, "freshness": fr["status"]})
                        continue
                    if q:
                        quotes[sym] = {"price": v.vp(q.mid)}
                        if rule["type"] == "pct_move" and not rule.get("ref"):
                            d1 = await ctx.feed.get_bars(sym, "1d", 5)
                            done = [b for b in d1 if b.closed and dt.datetime.fromtimestamp(b.ts, dt.timezone.utc).date() < now.date()]
                            if done:
                                quotes[sym]["prev_close"] = v.vp(done[-1].c)
                rings, new_state = a.evaluate([vrule], quotes, status, state, fake_now)
            except Exception as ex:  # noqa: BLE001
                errors.append({"rule": rule.get("id"), "error": f"{type(ex).__name__}: {ex}"[:200]})
                continue
            updates[rid] = new_state
            for r in rings:
                d = (inst.digits if inst else 5) or 5
                msg = self._message(rule, ctx.feed.quotes.get(sym), d, status)
                level = "warning" if rule["type"] in ("stop", "below") else "success" if rule["type"] == "target" else "info"
                notify(ctx.bus, level, "alerts", msg[:120], msg, sym or None, dedupe_key=f"alert:{rid}:{now.date()}")
                rings_out.append({"rule_id": rid, "id": rule.get("id"), "symbol": sym, "message": msg, "type": rule["type"]})
        with session_scope() as s:
            for rid, st in updates.items():
                row = s.get(AlertRule, rid)
                if row:
                    row.state = st
                    if any(x["rule_id"] == rid for x in rings_out):
                        row.last_rang = utcnow()
            for rid in deactivate:
                row = s.get(AlertRule, rid)
                if row:
                    row.active = False
        ops = self.operational(ctx) if ctx.agent_params.get("operational", True) else []
        status = "partial" if errors else "ok"
        summary = f"{len(data)} règle(s) active(s) : {len(rings_out)} déclenchée(s), {len(stale)} ignorée(s) (cotation non fraîche), {len(ops)} alerte(s) opérationnelle(s)."
        return AgentResult(status, summary, {"rings": rings_out, "skipped_stale": stale, "errors": errors, "operational": ops}, [f"{x['rule']}: {x['error']}" for x in errors][:5], "live")

    @staticmethod
    def _message(rule: dict, q: Any, d: int, status: dict) -> str:
        sym, t = rule.get("symbol", ""), rule["type"]
        px = f"{q.mid:.{d}f}" if q else "?"
        note = f" ({rule['note']})" if rule.get("note") else ""
        if t in ("above", "target"):
            return f"{sym} à {px} : au niveau ou au-dessus de votre {'cible' if t == 'target' else 'alerte'} {rule['price']}{note}."
        if t in ("below", "stop"):
            return f"{sym} à {px} : au niveau ou en dessous de votre {'stop' if t == 'stop' else 'alerte'} {rule['price']}{note}."
        if t == "pct_move":
            return f"{sym} a bougé d'au moins {rule['pct']} % (cours {px})."
        if t == "time":
            label = rule.get("label") or "alerte horaire"
            return f"{label[:1].upper()}{label[1:]} : {rule['at']} (heure de séance mappée) atteinte" + (f" pour {sym}. Clôturez la position si elle est encore ouverte." if sym else ".")
        if t == "opening_bell":
            return "Ouverture de la séance."
        return f"Règle {rule.get('id')} déclenchée."

    # ------------------------------------------------------------------ alertes opérationnelles (ajout de l'application)
    def operational(self, ctx: AgentContext) -> list[dict]:
        thr = float(ctx.agent_params.get("stale_throttle_s", self.default_params["stale_throttle_s"]))
        out = []

        def emit(level: str, key: str, title: str, body: str) -> None:
            if notify(ctx.bus, level, "system", title, body, dedupe_key=f"ops:{key}", throttle_s=thr):
                out.append({"key": key, "level": level, "title": title})

        h = ctx.feed.health()
        st = (h.get("stream") or {}).get("state")
        if st and st not in ("connected", "synth"):
            emit("error", "stream", "Flux BiQuote indisponible", f"état du flux : {st}. Les agents n'exécutent rien sans cotation fraîche.")
        elif h["quotes"] and h["live"] == 0 and h["closed"] < h["quotes"]:
            emit("warning", "stale", "Aucune cotation fraîche", f"{h['stale']} cotation(s) périmée(s), {h['closed']} marché(s) fermé(s).")
        if settings_store.get("kill_switch"):
            emit("warning", "kill", "Interrupteur d'arrêt actif", "Aucun nouvel ordre ne sera envoyé tant qu'il est actif.")
        if ctx.llm.last_error:
            emit("warning", "llm", "Erreur du fournisseur IA", ctx.llm.last_error[:200])
        return out
