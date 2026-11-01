"""Agent Entry — skill source : trader-entry (entry.py).

`entry.evaluate` (fonction pure du skill) est utilisée telle quelle : fourchette d'ouverture (ORB) ou clôture
au-dessus d'un niveau, sur bougies de 5 minutes CLÔTURÉES, avec fenêtre d'entrée (no_entries_before / after).
Adaptation : prix en « actions virtuelles », horodatages mappés (voir adapt.py). Les sorties sont reconverties en
prix et heures réels (UTC).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from .adapt import entry_chart, to_virtual_bars
from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill


class EntryAgent(Agent):
    id = "entry"
    name = "Agent Entry"
    mission = "Détecter le signal d'entrée (cassure de la fourchette d'ouverture ou d'un niveau) sur bougies de 5 minutes clôturées."
    skill = "trader-entry"
    implementation = "full"
    limitations = [
        "Barres 5 minutes construites depuis le flux BiQuote (ou son historique) : pas de volume réel (CFD), donc le filtre « volume minimal » du skill est ignoré.",
        "Marchés continus : la fourchette d'ouverture démarre à l'ancrage UTC configuré (séance mappée 6h30) ; fenêtre d'entrée du skill appliquée à cette séance mappée.",
        "Vente à découvert (sens `sell`) : cassure à la BAISSE de la fourchette, évaluée sur des prix en miroir avec le même skill ; seulement si l'interrupteur global et celui du portefeuille sont activés.",
        "Pas de mode `watch` interactif du skill : la surveillance est assurée par l'orchestrateur (à chaque clôture de bougie 5 min).",
    ]
    dependencies = ["bougies 5 minutes (BiQuote)"]
    default_params = {"setup": "orb", "or_minutes": 15, "no_entries_before": "09:45", "no_entries_after": "15:30",
                      "session_anchor_utc": "07:00", "bars_limit": 320}
    param_help = {"setup": "orb (fourchette d'ouverture) ou level (clôture au-dessus d'un niveau)",
                  "or_minutes": "durée de la fourchette d'ouverture (multiple de 5)",
                  "no_entries_before": "pas d'entrée avant cette heure (séance mappée, HH:MM)",
                  "no_entries_after": "pas d'entrée après cette heure (séance mappée, HH:MM)"}
    inputs = ["symbol", "setup (orb|level)", "level (prix réel, si setup=level)", "side (buy|sell : sell = cassure à la baisse, vente à découvert)"]
    outputs = ["status (triggered|waiting|expired|no-data)", "trigger{price, closes_at, minutes_since, pct_over}", "opening_range", "level", "reason"]

    async def run(self, ctx: AgentContext, symbol: str = "", setup: str | None = None, level: float | None = None,
                  or_minutes: int | None = None, side: str = "buy", **_: Any) -> AgentResult:
        e = load_skill("entry")
        p = params_of(self, ctx)
        symbol = symbol.upper()
        side = "sell" if str(side).lower() in ("sell", "short") else "buy"
        setup = setup or p["setup"]
        warnings: list[str] = []
        try:
            inst, v, m = ctx.virtual(symbol, side)
        except (KeyError, ValueError) as ex:
            return AgentResult("error", f"{symbol} : {ex}", {"symbol": symbol, "status": "no-data"}, instrument=symbol)
        quality = ctx.quality(symbol)
        bars = await ctx.feed.get_bars(symbol, "5m", int(p["bars_limit"]))
        if not bars:
            return AgentResult("skipped", f"{symbol} : aucune bougie 5 minutes disponible.", {"symbol": symbol, "status": "no-data"},
                               data_quality="none", instrument=symbol)
        now = ctx.clock()
        fake_now = m.fake_utc(now)
        vb = to_virtual_bars(bars, v, m, only_closed=False)
        chart = entry_chart(symbol, vb, None)
        cfg = {"no_entries_before": p["no_entries_before"], "no_entries_after": p["no_entries_after"]}
        vlevel = v.vp(float(level)) if level is not None else None
        try:
            res = e.evaluate(chart, fake_now, setup, int(or_minutes or p["or_minutes"]), "5m", vlevel, None, cfg)
        except e.EntryError as ex:
            return AgentResult("error", f"{symbol} : {ex}", {"symbol": symbol, "status": "error"}, instrument=symbol, data_quality=quality)
        pub = e.public(res)
        out: dict[str, Any] = {"symbol": symbol, "setup": setup, "side": side, "status": res["status"], "phase": res["phase"],
                               "reason": res["reason"], "mapped_date": res["date"], "session_profile": m.profile,
                               "bars_closed": res.get("bars_closed"), "interval": "5m"}
        d = inst.digits or 5
        orr = res.get("opening_range")
        if orr:
            hi, lo = (orr["low"], orr["high"]) if v.mirror else (orr["high"], orr["low"])  # miroir : le haut virtuel est le bas réel
            out["opening_range"] = {"high": v.rp(hi), "low": v.rp(lo), "minutes": orr["minutes"], "complete": orr["complete"],
                                    "bars": orr["bars"]}
        if res.get("level") is not None:
            out["level"] = v.rp(res["level"])
        last = res.get("last")
        if last:
            out["last"] = {"bar_start_mapped": last["bar_start"], "close": v.rp(last["close"]), "vs_level_pct": last["vs_level_pct"]}
        t = res.get("trigger")
        if res["status"] == "triggered" and t:
            wall_end = dt.datetime.strptime(res["date"] + " " + t["close_time"], "%Y-%m-%d %H:%M")
            closes_real = m.wall_to_real(wall_end)
            age_min = round((now - closes_real).total_seconds() / 60.0, 1)
            out["trigger"] = {"price": round(v.rp(t["close"]), d + 1), "closes_at": closes_real.isoformat() + "Z", "bar_index": t["bar_index"],
                              "minutes_since": res.get("minutes_since"), "signal_age_min": age_min, "pct_over": t["pct_over"]}
            if side == "sell":  # en miroir, le pourcentage du skill n'a pas de sens : on le recalcule en prix réels
                ref_lvl = (out.get("opening_range") or {}).get("low") if setup == "orb" else out.get("level")
                if ref_lvl:
                    out["trigger"]["pct_over"] = round((ref_lvl - out["trigger"]["price"]) / ref_lvl * 100, 3)
                out["reason"] = "Vente à découvert (cassure à la baisse). " + out["reason"]
            out["signal_key"] = f"{symbol}|{setup}|{res['date']}" + ("|short" if side == "sell" else "")
            out["entry_doc"] = e.entry_doc({**{k: res[k] for k in ("symbol", "setup", "date", "status", "interval", "reason")},
                                            "trigger": t, "level": res.get("level"), "opening_range": orr}, fake_now)
        if quality == "stale":
            warnings.append("cotation périmée : le signal ne doit pas être exécuté sans données fraîches")
        if "volume" in pub.get("reason", "").lower():
            warnings.append("volume indisponible (CFD)")
        status = "ok" if res["status"] in ("triggered", "waiting", "expired") else "partial"
        summary = f"{symbol} : {res['status'].upper()} — {res['reason']}"
        return AgentResult(status, summary, out, warnings, quality, symbol)
