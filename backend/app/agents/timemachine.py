"""Agent TimeMachine — skill source : trader-timemachine (timemachine.py).

Réutilise `normalize_plan`, `time_machine` (simulation Monte-Carlo par rééchantillonnage des barres des N dernières
séances COMPLÈTES de 78 bougies de 5 minutes + rejeu historique de la règle ORB), `verdict`, `lean`.
Le skill attend un JSON de type Yahoo v8 : on lui en fabrique un à partir des barres BiQuote, en prix virtuels et
horodatage mappé. Les probabilités (cible avant stop...) sont invariantes à ces conversions.

Ce n'est PAS une prédiction : c'est une simulation sur l'historique propre de l'instrument.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Any

from .adapt import to_virtual_bars, yahoo_doc
from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill


def tm_symbol(symbol: str) -> str:
    """Le skill exige un ticker `[A-Z][A-Z0-9.-]{0,9}` : on nettoie sans perdre l'unicité pratique."""
    s = re.sub(r"[^A-Z0-9]", "", symbol.upper())[:10] or "X"
    return s if s[0].isalpha() else "X" + s[:9]


class TimeMachineAgent(Agent):
    id = "timemachine"
    name = "Agent TimeMachine"
    mission = "Rejouer 1 000+ futurs plausibles du trade proposé (cible avant stop ?) à partir de l'historique propre de l'instrument."
    skill = "trader-timemachine"
    implementation = "full"
    limitations = [
        "Nécessite ≥ 20 séances COMPLÈTES de 78 bougies de 5 minutes : si l'historique BiQuote est trop court ou troué, l'agent répond « données insuffisantes » (jamais de chiffre inventé).",
        "Séances mappées pour les marchés continus (fenêtre de 6h30 depuis l'ancrage UTC) : un trou de cotation dans la fenêtre invalide la séance.",
        "Simulation, pas une prédiction : actualités, annonces et gaps ne sont pas modélisés.",
        "Le rejeu historique ne couvre que le setup `orb` (comme le skill).",
    ]
    dependencies = ["Agent Exit (plan)", "historique 5 minutes (≥ 20 séances)"]
    default_params = {"replays": 5000, "seed": 19, "window": 2, "bars_limit": 3200, "session_anchor_utc": "07:00",
                      "min_edge": None}
    param_help = {"replays": "nombre de futurs simulés (1 à 1 000 000)", "seed": "graine : mêmes chiffres pour les mêmes données",
                  "window": "tolérance (en barres) du rééchantillonnage horaire",
                  "min_edge": "écart minimal (cible vs seuil de rentabilité) sous lequel le trade est bloqué ; vide = information seulement"}
    inputs = ["plan (symbol, entry, stop, target, entry_bar)"]
    outputs = ["p_target, p_stop, p_neither, avg_R", "edge (écart vs seuil de rentabilité)", "verdict", "history{triggered, target, stop}"]

    async def run(self, ctx: AgentContext, plan: dict | None = None, symbol: str | None = None, **_: Any) -> AgentResult:
        t = load_skill("timemachine")
        p = params_of(self, ctx)
        plan = plan or (ctx.results.get("exit").output.get("plan") if ctx.results.get("exit") else None)
        if not plan:
            return AgentResult("skipped", "Aucun plan à simuler (Agent Exit requis).", {})
        symbol = (symbol or plan["symbol"]).upper()
        side = "sell" if str(plan.get("side", "buy")).lower() in ("sell", "short") else "buy"
        try:
            inst, v, m = ctx.virtual(symbol, side)
        except (KeyError, ValueError) as ex:
            return AgentResult("error", f"{symbol} : {ex}", {"symbol": symbol}, instrument=symbol)
        bars = await ctx.feed.get_bars(symbol, "5m", int(p["bars_limit"]))
        now = ctx.clock()
        vb = to_virtual_bars(bars, v, m, only_closed=True)
        fake_now = m.fake_utc(now)
        day = t.to_eastern(fake_now).date()
        today_closed = [b for b in vb if t.to_eastern(t.utc_of(b["t"])).date() == day]
        if not today_closed:
            return AgentResult("skipped", f"{symbol} : aucune bougie clôturée dans la séance du jour.", {"symbol": symbol}, [], ctx.quality(symbol), symbol)
        # barre d'entrée = dernière bougie clôturée de la séance (l'ordre part « maintenant »), au plus la 77e
        last_slot = int((t.to_eastern(t.utc_of(today_closed[-1]["t"])).replace(second=0) -
                         t.to_eastern(t.utc_of(today_closed[-1]["t"])).replace(hour=9, minute=30, second=0)).total_seconds() // 300)
        k = max(0, min(last_slot, 76))
        end_min = 9 * 60 + 30 + 5 * (k + 1)
        raw = {"symbol": tm_symbol(symbol), "side": "buy", "date": day.isoformat(), "setup": plan.get("setup", "orb"),
               "entry": v.vp(plan["entry"]), "stop": v.vp(plan["stop"]), "target": v.vp(plan["target"]),
               "entry_bar": k, "entry_time": t.clock12(end_min), "opening_range": {"minutes": int((plan.get("opening_range") or {}).get("minutes") or 15)},
               "stop_rule": "auto", "interval": "5m", "flat_by": plan.get("flat_by")}
        warnings: list[str] = []
        try:
            nplan = t.normalize_plan(raw, "agent:timemachine")
            text = yahoo_doc(tm_symbol(symbol), vb, regular_market_time=vb[-1]["t"] + 300)
            doc = await asyncio.to_thread(t.time_machine, text, nplan, fake_now, int(p["replays"]), int(p["seed"]), int(p["window"]),
                                          None, "biquote")
        except t.TMError as ex:
            return AgentResult("partial", f"{symbol} : simulation impossible — {ex}", {"symbol": symbol, "error": str(ex), "insufficient_data": True},
                               [str(ex)], ctx.quality(symbol), symbol)
        mc = doc["mc"]
        lv = t.lean(mc)
        edge = round(lv[0], 4) if lv else None
        warnings += doc.get("warnings", [])
        hist = doc["history"]
        out = {"symbol": symbol, "p_target": mc["p_target"], "p_stop": mc["p_stop"], "p_neither": mc["p_neither"], "p_green": mc["p_green"],
               "avg_R": mc["avg_R"], "r_at_target": mc["r_at_target"], "replays": mc["replays"], "seed": mc["seed"], "bars_left": mc["bars_left"],
               "pool_sessions": mc["pool_sessions"], "pool_from": mc["pool_from"], "pool_to": mc["pool_to"], "edge": edge,
               "share_target_of_decided": round(lv[1], 4) if lv else None, "break_even_share": round(lv[2], 4) if lv else None,
               "verdict": doc["verdict"], "verdict_notes": doc["verdict_notes"], "note": doc["note"],
               "history": {k2: hist.get(k2) for k2 in ("sessions", "triggered", "target", "stop", "close", "green", "avg_R", "note")},
               "entry_bar": k, "min_edge": p.get("min_edge")}
        min_edge = p.get("min_edge")
        gate = "pass"
        if min_edge is not None and (edge is None or edge < float(min_edge)):
            gate = "block"
        out["gate"] = gate
        return AgentResult("ok", f"{symbol} : {doc['verdict']} ({mc['replays']} futurs simulés sur {mc['pool_sessions']} séances).",
                           out, warnings, ctx.quality(symbol), symbol)
