"""Agent Risk — skill source : trader-risk (check.py).

Porte de contrôle OBLIGATOIRE avant toute exécution. `check` (fonction pure du skill, inchangée) valide format,
fenêtre d'entrée, perte max par trade, reward:risk, perte journalière, nombre de trades, doublons, taille de position,
marge/cash. Il raisonne en actions US : l'ordre lui est présenté en « actions virtuelles » avec un code d'action stable.

Règles supplémentaires DÉTERMINISTES du backend (jamais l'IA), car le skill ne connaît pas les CFD :
interrupteur d'arrêt global, fraîcheur de la cotation et marché fermé, spread maximal, nombre max de positions ouvertes,
pas de renforcement d'une position existante, perte journalière RÉALISÉE, exposition brute max, âge du signal,
verrou TimeMachine (si configuré), alertes d'actualité de niveau « high » (si configuré), quantité nulle / refus du Budget.
Verdict : PASS ou BLOCK. Un BLOCK est définitif pour cette proposition ; aucun agent ne peut le contourner.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select

from .. import settings_store
from ..db import session_scope
from ..models import Fill, Order
from .adapt import risk_code
from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill

_CODES: dict[str, str] = {}


def code_of(symbol: str) -> str:
    return risk_code(symbol, _CODES)


class RiskAgent(Agent):
    id = "risk"
    name = "Agent Risk"
    mission = "Valider (PASS) ou refuser (BLOCK) chaque ordre proposé selon les limites de risque du portefeuille, avant toute exécution."
    skill = "trader-risk"
    implementation = "full"
    limitations = [
        "Le skill ne connaît que les tickers d'actions US : l'ordre est présenté sous un code d'action stable et en « actions virtuelles » (voir docs/AGENTS.md). Les règles propres aux CFD sont des règles backend ajoutées.",
        "« Cash » du skill = pouvoir d'achat restant (marge libre x levier) ; « compte » = equity x levier.",
        "Le journal d'ordres du jour est reconstruit depuis le moteur papier (jour = date UTC réelle) au lieu de trader/state/broker.json.",
        "Les règles ajoutées par le backend ne figurent pas dans le skill d'origine : elles sont listées séparément (`backend_rules`) dans chaque verdict.",
    ]
    dependencies = ["Agent Budget (ordre dimensionné)", "moteur papier (portefeuille, journal)", "cotation fraîche"]
    default_params = {"max_signal_age_min": 20, "max_spread_bps": None, "max_open_positions": None, "allow_add_to_position": False,
                      "session_anchor_utc": "07:00", "block_on_high_news": True}
    param_help = {"max_signal_age_min": "âge maximal du signal d'entrée (minutes) ; au-delà : BLOCK",
                  "max_spread_bps": "spread maximal (points de base) ; vide = valeur du portefeuille",
                  "max_open_positions": "nombre max de positions ouvertes ; vide = valeur du portefeuille",
                  "block_on_high_news": "BLOCK si l'agent News a levé une alerte de niveau high sur l'instrument (réglage global news_block_on_halt)"}
    inputs = ["sizing (Agent Budget)", "signal (Agent Entry)", "timemachine (optionnel)", "news (optionnel)", "portfolio_id"]
    outputs = ["verdict PASS|BLOCK", "reasons[]", "warnings[]", "max_loss", "reward_risk", "fingerprint", "backend_rules[]", "skill_result"]

    async def run(self, ctx: AgentContext, sizing: dict | None = None, signal: dict | None = None, timemachine: dict | None = None,
                  news: dict | None = None, portfolio_id: int | None = None, **_: Any) -> AgentResult:
        k = load_skill("risk")
        p = params_of(self, ctx)
        sizing = sizing or (ctx.results.get("budget").output if ctx.results.get("budget") else None)
        signal = signal if signal is not None else (ctx.results.get("entry").output if ctx.results.get("entry") else None)
        timemachine = timemachine if timemachine is not None else (ctx.results.get("timemachine").output if ctx.results.get("timemachine") else None)
        news = news if news is not None else (ctx.results.get("news").output if ctx.results.get("news") else None)
        if not sizing:
            return AgentResult("skipped", "Aucun dimensionnement à contrôler (Agent Budget requis).", {"verdict": "BLOCK", "reasons": ["aucune proposition dimensionnée"]})
        pid = int(portfolio_id if portfolio_id is not None else sizing.get("portfolio_id"))
        symbol = sizing["symbol"].upper()
        now = ctx.clock()
        backend: list[dict] = []

        def rule(name: str, ok: bool, detail: str, severity: str = "block") -> None:
            backend.append({"rule": name, "ok": ok, "detail": detail, "severity": severity})

        try:
            inst, v, m = ctx.virtual(symbol)
            summ = ctx.engine.portfolio_summary(pid)
        except (KeyError, ValueError) as ex:
            return AgentResult("ok", f"{symbol} : BLOCK — {ex}", {"verdict": "BLOCK", "symbol": symbol, "reasons": [str(ex)], "backend_rules": []},
                               [], "none", symbol)
        rc = summ.get("risk") or {}
        fresh = ctx.feed.freshness(symbol)
        q = ctx.feed.quotes.get(symbol)

        # ---------------- règles backend déterministes
        rule("kill_switch", not settings_store.get("kill_switch"), "interrupteur d'arrêt global actif" if settings_store.get("kill_switch") else "inactif")
        rule("portefeuille_actif", bool(summ["active"]), "portefeuille désactivé" if not summ["active"] else "actif")
        max_age = float(settings_store.get("max_quote_age_s"))
        state = ctx.feed.states.get(symbol, q.market_state if q else "unknown")
        fresh_ok = bool(q) and fresh["status"] == "live" and fresh["age_s"] is not None and fresh["age_s"] <= max_age and state != "closed"
        rule("donnees_fraiches", fresh_ok, f"cotation {fresh['status']}, âge {fresh['age_s']} s (max {max_age:.0f} s), marché {state}")
        spread_bps = round(q.spread / q.mid * 1e4, 2) if q and q.spread is not None and q.mid > 0 else None
        cap_spread = p.get("max_spread_bps") or rc.get("max_spread_bps")
        if cap_spread and spread_bps is not None:
            rule("spread", spread_bps <= float(cap_spread), f"spread {spread_bps} bps (max {cap_spread})")
        elif cap_spread:
            rule("spread", False, "spread inconnu (bid/ask absents) : exécution refusée par prudence")
        open_pos = [x for x in summ["positions"]]
        cap_pos = p.get("max_open_positions") or rc.get("max_open_positions")
        if cap_pos:
            rule("positions_ouvertes", len(open_pos) < int(cap_pos), f"{len(open_pos)} position(s) ouverte(s), max {cap_pos}")
        if not p.get("allow_add_to_position"):
            held = [x for x in open_pos if x["symbol"] == symbol]
            rule("pas_de_renforcement", not held, f"position déjà ouverte sur {symbol}" if held else "aucune position ouverte sur l'instrument")
        today0 = dt.datetime(now.year, now.month, now.day)
        with session_scope() as s:
            pnl_today = float(sum((f.realized_pnl or 0.0) for f in s.scalars(select(Fill).where(Fill.portfolio_id == pid, Fill.ts >= today0))))
        dl = rc.get("max_daily_loss_usd")
        if dl:
            rule("perte_journaliere_realisee", pnl_today > -float(dl), f"P&L réalisé aujourd'hui {pnl_today:.2f} USD (limite -{float(dl):.2f})")
        exp_cap = rc.get("max_exposure_pct")
        if exp_cap and summ["equity"] > 0 and sizing.get("notional_usd") is not None:
            tot = (summ["exposure_usd"] + sizing["notional_usd"]) / summ["equity"] * 100
            rule("exposition_brute", tot <= float(exp_cap) + 1e-9, f"exposition après ordre {tot:.0f} % de l'equity (max {exp_cap} %)")
        if signal and signal.get("trigger"):
            age = signal["trigger"].get("signal_age_min")
            if age is not None:
                rule("age_du_signal", age <= float(p["max_signal_age_min"]), f"signal vieux de {age} min (max {p['max_signal_age_min']})")
        if timemachine and timemachine.get("gate") == "block":
            rule("timemachine", False, f"écart {timemachine.get('edge')} sous le minimum configuré {timemachine.get('min_edge')}")
        elif timemachine and timemachine.get("insufficient_data"):
            rule("timemachine", True, "simulation indisponible (historique insuffisant) : information seulement", "info")
        if news and settings_store.get("news_block_on_halt") and p.get("block_on_high_news"):
            highs = [f for f in (news.get("risk_flags") or []) if f.get("symbol") == symbol and f.get("level") == "high"]
            rule("actualites", not highs, ("alerte news high : " + "; ".join(f["text"][:80] for f in highs[:2])) if highs else "aucune alerte high")
        rule("quantite", float(sizing.get("qty") or 0) > 0 and not sizing.get("refusal"), sizing.get("refusal") or f"quantité {sizing.get('qty')}")

        # ---------------- skill Risk (fonction pure)
        skill_res, order, reasons, warnings, info = None, sizing.get("virtual_order"), [], [], []
        max_loss = sizing.get("max_loss_usd")
        rr = sizing.get("reward_risk")
        fingerprint = None
        if order:
            code = code_of(symbol)
            order = {**order, "symbol": code}
            positions, prices, times = [], {}, {}
            fake_now = m.fake_utc(now)
            if q:
                prices[code] = v.vp(q.ask or q.mid)
                times[code] = fake_now - dt.timedelta(seconds=float(fresh["age_s"] or 0))
            for hp in open_pos:
                try:
                    _, hv, _ = ctx.virtual(hp["symbol"])
                except (KeyError, ValueError):
                    warnings.append(f"position {hp['symbol']} non valorisable (taux USD manquant)")
                    continue
                hc = code_of(hp["symbol"])
                positions.append({"ticker": hc, "shares": hv.vq(hp["qty"])})
                if hp.get("mark") is not None:
                    prices[hc] = hv.vp(hp["mark"])
            cash = max(float(summ["free_margin"]), 0.0) * float(summ["leverage"])
            vcfg = {key: rc[key] for key in ("cash_buffer_pct", "max_position_pct", "max_order_usd", "min_order_usd", "max_loss_per_trade_usd",
                                             "max_daily_loss_usd", "max_trades_per_day", "max_orders_per_day", "min_reward_risk", "require_stop",
                                             "pdt_guard") if key in rc}
            vcfg["blocked"] = [code_of(b) for b in (rc.get("blocked") or [])]
            vcfg["paper"] = True
            vcfg["no_entries_before"] = (ctx.agent_params.get("no_entries_before") or "09:45")
            vcfg["no_entries_after"] = (ctx.agent_params.get("no_entries_after") or "15:30")
            state_doc = {"orders": self._state(pid, now, v_for=ctx)}
            today = now.date().isoformat()
            try:
                skill_res = k.check([order], vcfg, {"cash": cash, "positions": positions}, prices, state_doc, today, m.fake_utc(now), times)
            except k.RiskError as ex:
                reasons.append(f"skill risk : {ex}")
            else:
                r = skill_res["results"][0]
                reasons += r["reasons"]
                warnings += r["warnings"] + skill_res.get("warnings", [])
                info += r["info"]
                fingerprint = r["fingerprint"]
                rr = r["reward_risk"] if r["reward_risk"] is not None else rr
                max_loss = r["max_loss"] if r["max_loss"] is not None else max_loss
        else:
            reasons.append("aucun ordre dimensionné à contrôler")
        backend_blocks = [b for b in backend if not b["ok"] and b["severity"] == "block"]
        reasons += [f"backend/{b['rule']} : {b['detail']}" for b in backend_blocks]
        verdict = "BLOCK" if reasons else "PASS"
        out = {"verdict": verdict, "symbol": symbol, "portfolio_id": pid, "reasons": reasons, "warnings": warnings, "info": info,
               "max_loss": max_loss, "reward_risk": rr, "fingerprint": fingerprint, "backend_rules": backend, "skill_result": skill_res,
               "checked_order": order, "spread_bps": spread_bps}
        why = "; ".join(reasons[:2])
        return AgentResult("ok", f"{symbol} : {verdict}" + (f" — {why}" if reasons else f" (perte max {max_loss} USD, R:R {rr})"), out, warnings,
                           ctx.quality(symbol), symbol)

    @staticmethod
    def _state(pid: int, now: dt.datetime, v_for: AgentContext) -> list[dict]:
        """Ordres d'entrée « placés » aujourd'hui (jour = date UTC réelle) pour les limites journalières du skill."""
        today0 = dt.datetime(now.year, now.month, now.day)
        out = []
        with session_scope() as s:
            for o in s.scalars(select(Order).where(Order.portfolio_id == pid, Order.created_at >= today0, Order.side == "buy",
                                                    Order.role.in_(("entry", "standalone")))):
                if o.status in ("rejected", "cancelled"):
                    continue
                out.append({"status": "placed", "date": today0.date().isoformat(), "symbol": code_of(o.symbol), "side": "buy", "kind": "entry",
                            "max_loss": (o.meta or {}).get("max_loss_usd")})
        return out
