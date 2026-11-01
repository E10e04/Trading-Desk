"""Agent Exit — skill source : trader-exit (exit.py).

Réutilise `parse_stop`, `parse_target`, `compute` (stop/cible/risque exacts au centime), `session_bars` + `atr_of`
(stop ATR), `resolve_flat_by` (heure de sortie forcée, avancée si clôture anticipée). Deux rôles :
  * `plan` : construit le plan stop/cible/flat-by pour un signal d'entrée (prix réels en sortie) ;
  * `monitor` : ferme au marché (paper) les positions dont l'heure « flat-by » est atteinte.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .adapt import entry_chart, to_virtual_bars
from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill


def _ceil(x: float, digits: int) -> float:
    f = 10 ** digits
    return math.ceil(x * f - 1e-9) / f


def _floor(x: float, digits: int) -> float:
    f = 10 ** digits
    return math.floor(x * f + 1e-9) / f


class ExitAgent(Agent):
    id = "exit"
    name = "Agent Exit"
    mission = "Définir stop, cible et heure de sortie forcée (flat-by) d'un trade, et clôturer les positions arrivées à l'heure limite."
    skill = "trader-exit"
    implementation = "full"
    limitations = [
        "Le skill ne planifie que des achats : la vente à découvert (réglages : interrupteur global + portefeuille) est calculée sur des prix « en miroir » (prix de référence × 2 − prix), puis reconvertie ; le skill reste intact. Stop arrondi vers le haut dans ce sens.",
        "Calculs faits en prix « virtuels » (centime) puis reconvertis : stop arrondi vers le bas, cible à la précision de l'instrument.",
        "Le stop/cible sont exécutés par le moteur papier (stop = déclenchement puis marché, donc peut glisser) — pas de garantie de prix.",
    ]
    dependencies = ["Agent Entry (signal)", "bougies 5 minutes (stop ATR)"]
    default_params = {"stop": "or-mid", "target": "2R", "flat_by": "15:55", "min_reward_risk": 1.0, "session_anchor_utc": "07:00",
                      "monitor_enabled": True}
    param_help = {"stop": "or-mid, or-low, price:X, pct:X ou atr:K", "target": "2R, 1.5R ou price:X",
                  "flat_by": "heure de sortie forcée (séance mappée HH:MM)",
                  "monitor_enabled": "clôturer automatiquement à l'heure flat-by"}
    inputs = ["symbol", "entry (prix réel)", "opening_range{high,low}", "entry_time (UTC)", "stop", "target"]
    outputs = ["plan{entry, stop, target, risk_per_unit, reward_risk, flat_by, flat_by_utc}", "warnings"]

    async def run(self, ctx: AgentContext, symbol: str = "", entry: float | None = None, opening_range: dict | None = None,
                  setup: str = "orb", entry_time: dt.datetime | str | None = None, stop: str | None = None, target: str | None = None,
                  flat_by: str | None = None, mode: str = "plan", side: str = "buy", **_: Any) -> AgentResult:
        if mode == "monitor":
            return await self.monitor(ctx)
        x = load_skill("exit")
        p = params_of(self, ctx)
        symbol = symbol.upper()
        side = "sell" if str(side).lower() in ("sell", "short") else "buy"
        warnings: list[str] = []
        try:
            inst, v, m = ctx.virtual(symbol, side)
        except (KeyError, ValueError) as ex:
            return AgentResult("error", f"{symbol} : {ex}", {"symbol": symbol}, instrument=symbol)
        if entry is None:
            q = ctx.feed.quotes.get(symbol)
            if not q:
                return AgentResult("error", f"{symbol} : aucun prix d'entrée", {"symbol": symbol}, instrument=symbol)
            entry = q.ask or q.mid
        now = ctx.clock()
        d = inst.digits or 5
        stop_text = stop or p["stop"]
        if setup != "orb" and stop_text in ("or-mid", "or-low", "or_mid", "or_low"):
            stop_text = "pct:0.3"
            warnings.append("setup sans fourchette d'ouverture : stop par défaut pct:0.3")
        try:
            sr, sp = x.parse_stop(stop_text)
            tr, tp = x.parse_target(target or p["target"])
            if sr == "price":  # prix saisi en prix RÉEL -> prix virtuel
                sp = x.D(round(v.vp(float(sp)), 6))
            if tr == "price":
                tp = x.D(round(v.vp(float(tp)), 6))
            ve = v.vp(float(entry))
            orr = opening_range or {}
            # en miroir, le haut virtuel de la fourchette est le bas réel
            hk, lk = ("low", "high") if v.mirror else ("high", "low")
            oh = v.vp(orr[hk]) if orr.get(hk) is not None else None
            ol = v.vp(orr[lk]) if orr.get(lk) is not None else None
            atr = atr_n = None
            if sr == "atr":
                bars = await ctx.feed.get_bars(symbol, "5m", 320)
                vb = to_virtual_bars(bars, v, m, only_closed=False)
                fake_now = m.fake_utc(now)
                day = x.to_eastern(fake_now).date()
                sb = x.session_bars(entry_chart(symbol, vb), day, "5m", None, fake_now)
                if len(sb) < 3:
                    raise x.PlanError(f"un stop ATR demande au moins 3 bougies 5 min clôturées ; {len(sb)} disponibles")
                atr, atr_n = x.atr_of(sb)
            c = x.compute(ve, sr, sp, tr, tp, oh, ol, atr, atr_n)
            day_mapped = x.to_eastern(m.fake_utc(now)).date()
            et = None
            if entry_time:
                et_dt = dt.datetime.fromisoformat(entry_time.rstrip("Z")) if isinstance(entry_time, str) else entry_time
                et = x.to_eastern(m.fake_utc(et_dt)).time()
            fb, fb_note = x.resolve_flat_by(day_mapped, flat_by, {"flat_by": p["flat_by"]}, et)
        except x.PlanError as ex:
            return AgentResult("error", f"{symbol} : plan impossible — {ex}", {"symbol": symbol, "error": str(ex)}, warnings, ctx.quality(symbol), symbol)
        # achat : stop arrondi vers le bas (loin de l'entrée) ; vente à découvert : vers le haut
        stop_real = _ceil(v.rp(float(c["stop"])), d) if side == "sell" else _floor(v.rp(float(c["stop"])), d)
        entry_real = round(float(entry), d + 1)
        if (stop_real <= entry_real) if side == "sell" else (stop_real >= entry_real):
            return AgentResult("error", f"{symbol} : stop arrondi ({stop_real}) du mauvais côté de l'entrée ({entry_real}) après conversion de précision.",
                               {"symbol": symbol}, warnings, ctx.quality(symbol), symbol)
        target_real = round(v.rp(float(c["target"])), d)
        risk_unit = abs(entry_real - stop_real)
        rr = round(abs(target_real - entry_real) / risk_unit, 2) if risk_unit > 0 else 0.0
        fb_wall = dt.datetime.combine(day_mapped, dt.datetime.strptime(fb, "%H:%M").time())
        flat_utc = m.wall_to_real(fb_wall)
        if fb_note:
            warnings.append(f"flat-by {fb} : {fb_note}")
        mrr = p.get("min_reward_risk")
        if mrr is not None and rr < float(mrr):
            warnings.append(f"reward:risk {rr} < minimum {mrr} (le contrôle de risque tranchera)")
        plan = {"symbol": symbol, "side": side, "date": day_mapped.isoformat(), "setup": setup, "entry": entry_real, "stop": stop_real,
                "target": target_real, "risk_per_unit": round(risk_unit, d + 1), "reward_risk": rr, "stop_rule": sr,
                "stop_label": c["stop_label"], "target_label": c["target_label"], "stop_detail": c["stop_detail"],
                "flat_by": fb, "flat_by_utc": flat_utc.isoformat() + "Z", "opening_range": opening_range, "entry_time": et.strftime("%I:%M %p").lstrip("0") if et else None,
                "stop_label_fr": c["stop_label"], "created_at": now.isoformat() + "Z", "created_by": "agent:exit"}
        if atr is not None:
            plan["atr"] = round(atr, 6)
        return AgentResult("ok", f"{symbol} : entrée {entry_real}, stop {stop_real} ({c['stop_label']}), cible {target_real}, R:R {rr}, sortie forcée {flat_utc:%H:%M} UTC.",
                           {"symbol": symbol, "plan": plan}, warnings, ctx.quality(symbol), symbol)

    async def monitor(self, ctx: AgentContext) -> AgentResult:
        """Clôture au marché les positions papier dont `flat_by_utc` est atteint. Aucun effet si le paramètre est désactivé."""
        p = params_of(self, ctx)
        if not p.get("monitor_enabled", True):
            return AgentResult("skipped", "Surveillance flat-by désactivée.", {"closed": []})
        now = ctx.clock()
        closed, errors = [], []
        for pos in ctx.engine.list_positions():
            plan = pos.get("plan") or {}
            fb = plan.get("flat_by_utc")
            if not fb:
                continue
            try:
                due = dt.datetime.fromisoformat(fb.rstrip("Z")) <= now
            except ValueError:
                continue
            if not due:
                continue
            try:
                o = ctx.engine.close_position(pos["portfolio_id"], pos["symbol"], source="agent:exit")
                (closed if o.get("status") == "filled" else errors).append({"symbol": pos["symbol"], "portfolio_id": pos["portfolio_id"],
                                                                           "status": o.get("status"), "reason": o.get("reject_reason")})
            except Exception as e:  # noqa: BLE001
                errors.append({"symbol": pos["symbol"], "error": str(e)})
        status = "partial" if errors else "ok"
        return AgentResult(status, f"{len(closed)} position(s) clôturée(s) à l'heure flat-by, {len(errors)} échec(s).",
                           {"closed": closed, "errors": errors}, [f"{e}" for e in errors][:5])
