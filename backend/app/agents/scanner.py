"""Agent Scanner — skill source : trader-scanner (scan.py).

Fonctions du skill réutilisées telles quelles : `find_setups` (breakout_20d, bull_flag, reclaim_ma20, oversold,
oversold_bounce, calculés sur clôtures journalières), `scan_daily`, `orb_window`, `scan_orb` (opening-range breakout
sur bougies de 5 minutes, en deux étapes), `rsi`. Seuls l'univers et le chargement des données changent : au lieu de
Yahoo + liste Nasdaq Trader (actions US), l'univers est le catalogue BiQuote.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from sqlalchemy import delete

from .. import settings_store
from ..db import session_scope
from ..market.provider import Bar
from ..models import WatchItem, utcnow
from .adapt import SessionMapper, Virtual, scan_series, to_virtual_bars
from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill

LABELS_FR = {
    "orb_short": "cassure à la baisse de la fourchette d'ouverture (vente à découvert)",
    "breakout_20d": "clôture au-dessus du plus haut des 20 dernières clôtures",
    "bull_flag": "drapeau haussier (hausse > 10 % puis 5 clôtures dans une fourchette < 5 %)",
    "reclaim_ma20": "reprise de la moyenne mobile 20 jours",
    "oversold": "survendu (RSI 14 < 30)",
    "oversold_bounce": "rebond après survente (RSI 14 était < 30 hier, clôture en hausse)",
    "orb": "cassure de la fourchette d'ouverture (opening-range breakout)",
}


class ScannerAgent(Agent):
    id = "scanner"
    name = "Agent Scanner"
    mission = "Repérer les instruments qui répondent à une règle de setup (cassure, drapeau, survente...) et alimenter la liste de surveillance."
    skill = "trader-scanner"
    implementation = "full"
    limitations = [
        "Univers = instruments BiQuote cotant à cet instant (forex, métaux, énergie, crypto, indices, CFD actions) au lieu des ~5 900 actions US du skill.",
        "BiQuote ne fournit aucun volume réel (CFD) : le filtre de liquidité en volume du skill est remplacé par un filtre de spread et de fraîcheur.",
        "Marchés continus : la « séance » d'ouverture est une fenêtre de 6h30 démarrant à l'ancrage UTC configuré ; le calendrier US des skills s'y applique (pas de séances le week-end).",
    ]
    dependencies = ["BiQuote REST (bougies 5m et 1d)", "flux de cotations"]
    default_interval_s = 300
    default_params = {"modes": ["daily", "orb"], "or_minutes": 15, "max_spread_bps": 25.0, "max_instruments": 300,
                      "concurrency": 8, "asset_classes": [], "no_entries_after": "15:30", "session_anchor_utc": "07:00",
                      "watchlist_ttl_h": 12}
    param_help = {"modes": "daily (clôtures), orb (fourchette d'ouverture)", "or_minutes": "durée de la fourchette d'ouverture (multiple de 5)",
                  "max_spread_bps": "ignore les instruments dont le spread dépasse ce nombre de points de base",
                  "asset_classes": "restreindre à forex, metal, energy, crypto, index, equity (vide = toutes)"}
    inputs = ["symbols (optionnel)"]
    outputs = ["hits[] (symbol, setups, raisons, prix)", "counts", "watchlist"]

    async def run(self, ctx: AgentContext, symbols: list[str] | None = None, write_watchlist: bool = True, **_: Any) -> AgentResult:
        from .. import settings_store
        p = params_of(self, ctx)
        scan = load_skill("scanner")
        now = ctx.clock()
        warnings: list[str] = []
        universe, skipped = self._universe(ctx, p, symbols)
        if not universe:
            return AgentResult("skipped", "Aucun instrument à scanner (aucune cotation fraîche).", {"counts": skipped}, warnings, "none")
        sem = asyncio.Semaphore(int(p["concurrency"]))
        counts = {"requested": len(universe), "fetched_daily": 0, "fetched_5m": 0, "failed": 0, "wide_spread": skipped.get("wide_spread", 0),
                  "stale": skipped.get("stale", 0), "no_fx": skipped.get("no_fx", 0)}
        failures: dict[str, str] = {}
        daily_data: dict[str, dict] = {}
        intraday: dict[str, tuple[dict, Virtual, SessionMapper, list[Bar]]] = {}
        real_daily: dict[str, list[tuple[dt.date, float]]] = {}
        want_daily, want_orb = "daily" in p["modes"], "orb" in p["modes"]

        async def fetch(sym: str) -> None:
            async with sem:
                try:
                    inst, v, m = ctx.virtual(sym)
                except ValueError as e:
                    counts["no_fx"] += 1
                    failures[sym] = str(e)
                    return
                try:
                    if want_daily:
                        d = await ctx.feed.get_bars(sym, "1d", 60)
                        done = [b for b in d if b.closed and dt.datetime.fromtimestamp(b.ts, dt.timezone.utc).date() < now.date()]
                        # horodatage à midi UTC : le skill lit la DATE (heure de New York) de chaque bougie
                        series = {"t": [int(dt.datetime.combine(dt.datetime.fromtimestamp(b.ts, dt.timezone.utc).date(), dt.time(12), dt.timezone.utc).timestamp()) for b in done],
                                  "c": [v.vp(b.c) for b in done], "meta": {}}
                        daily_data[sym] = series
                        real_daily[sym] = [(dt.datetime.fromtimestamp(b.ts, dt.timezone.utc).date(), b.c) for b in done]
                        counts["fetched_daily"] += 1
                    if want_orb:
                        b5 = await ctx.feed.get_bars(sym, "5m", 320)
                        vb = to_virtual_bars(b5, v, m, only_closed=False)
                        intraday[sym] = (scan_series(vb), v, m, b5)
                        counts["fetched_5m"] += 1
                except Exception as e:
                    counts["failed"] += 1
                    failures[sym] = f"{type(e).__name__}: {str(e)[:120]}"

        await asyncio.gather(*(fetch(s) for s in universe))

        hits: dict[str, dict] = {}

        def hit(sym: str) -> dict:
            if sym not in hits:
                inst = ctx.feed.info(sym)
                q = ctx.feed.quotes.get(sym)
                hits[sym] = {"symbol": sym, "asset_class": inst.asset_class, "description": inst.description, "setups": [], "reasons": [],
                             "price": q.mid if q else None, "freshness": ctx.feed.freshness(sym)["status"],
                             "spread_bps": self._spread_bps(ctx, sym)}
            return hits[sym]

        # -- étape 1 : scan journalier (fonctions du skill)
        if want_daily and daily_data:
            d_hits, d_counts, last_date = scan.scan_daily(daily_data, list(daily_data), now, 0.0, 0)
            counts["daily"] = {**d_counts, "last_close_date": last_date.isoformat() if last_date else None}
            for h in d_hits:
                row = hit(h["symbol"])
                row["setups"] += h["setups"]
                row["reasons"] += [LABELS_FR.get(s, s) for s in h["setups"]]
                row["daily"] = {"rsi": h["rsi"], "close_date": h["close_date"]}

        # -- étape 2 : opening-range breakout, par profil de session (achat ; et vente à découvert si les interrupteurs le permettent)
        if want_orb and intraday:
            counts["orb"] = {}
            until = scan.parse_hhmm(p["no_entries_after"], "no_entries_after")
            passes = [("buy", intraday)]
            if "sell" in settings_store.trade_directions():
                short_series: dict[str, tuple[dict, Virtual, SessionMapper, list[Bar]]] = {}
                for sym, (_, _, m, b5) in intraday.items():
                    try:
                        _, vs, _ = ctx.virtual(sym, "sell")  # prix en miroir : une cassure à la baisse devient une cassure à la hausse
                    except ValueError:
                        continue
                    short_series[sym] = (scan_series(to_virtual_bars(b5, vs, m, only_closed=False)), vs, m, b5)
                passes.append(("sell", short_series))
            for side, series in passes:
                groups: dict[str, list[str]] = {"us_equity": [], "continuous": []}
                for sym, (_, _, m, _) in series.items():
                    groups[m.profile].append(sym)
                for profile, syms in groups.items():
                    if not syms:
                        continue
                    m0 = series[syms[0]][2]
                    key = profile if side == "buy" else f"{profile}_vente"
                    try:
                        day, cutoff = scan.orb_window(m0.fake_utc(now), None, int(p["or_minutes"]), until)
                    except scan.ScanError as e:
                        counts["orb"][key] = {"scanned": 0, "note": str(e)}
                        continue
                    stage1 = {s_: series[s_][0] for s_ in syms}
                    o_hits, o_counts, o_failed = scan.scan_orb(stage1, syms, day, cutoff, int(p["or_minutes"]), 0.0, 0)
                    counts["orb"][key] = o_counts
                    for s_, why in o_failed.items():
                        failures[s_] = why
                    for h in o_hits:
                        _, v, m, _ = series[h["symbol"]]
                        row = hit(h["symbol"])
                        start_wall = dt.datetime.combine(day, dt.datetime.strptime(h["bar_start"], "%H:%M").time())
                        closes_real = m.wall_to_real(start_wall + dt.timedelta(minutes=5))
                        if side == "buy":
                            row["setups"].append("orb")
                            row["reasons"].append(f"{LABELS_FR['orb']} : clôture {v.rp(h['price']):.{v.digits}f} au-dessus de {v.rp(h['or_high']):.{v.digits}f} "
                                                  f"(+{h['pct_over']:.2f} %)")
                            row["orb"] = {"price": v.rp(h["price"]), "or_high": v.rp(h["or_high"]), "or_low": v.rp(h["or_low"]), "pct_over": h["pct_over"],
                                          "closes_at": closes_real.isoformat() + "Z", "bar_index": h["bar_index"]}
                        else:
                            row["setups"].append("orb_short")
                            row["reasons"].append(f"{LABELS_FR['orb_short']} : clôture {v.rp(h['price']):.{v.digits}f} sous {v.rp(h['or_high']):.{v.digits}f} "
                                                  f"(-{h['pct_over']:.2f} %)")
                            row["orb_short"] = {"price": v.rp(h["price"]), "or_low": v.rp(h["or_high"]), "or_high": v.rp(h["or_low"]), "pct_over": h["pct_over"],
                                                "closes_at": closes_real.isoformat() + "Z", "bar_index": h["bar_index"]}

        out_hits = sorted(hits.values(), key=lambda h: (-("orb" in h["setups"] or "orb_short" in h["setups"]), -len(h["setups"]), h["symbol"]))
        counts["hits"] = len(out_hits)
        if failures:
            warnings.append(f"{len(failures)} instrument(s) non scannés (données manquantes ou erreur) — scan partiel, jamais « tout le marché ».")
        if skipped.get("stale"):
            warnings.append(f"{skipped['stale']} instrument(s) ignorés : cotation périmée ou marché fermé.")
        if write_watchlist:
            self._write_watchlist(out_hits, int(p["watchlist_ttl_h"]))
        quality = "live" if universe and any(ctx.feed.freshness(s)["status"] == "live" for s in universe[:20]) else "stale"
        scanned = max(counts["fetched_daily"], counts["fetched_5m"])
        status = "partial" if failures else "ok"
        summary = (f"{len(out_hits)} instrument(s) correspondent à une règle sur {scanned} scannés "
                   f"({counts['requested']} demandés, {len(failures)} en échec). Une correspondance n'est pas une recommandation d'achat.")
        return AgentResult(status, summary, {"hits": out_hits, "counts": counts, "failures": dict(list(failures.items())[:30])}, warnings, quality)

    # ------------------------------------------------------------------ univers
    def _universe(self, ctx: AgentContext, p: dict, symbols: list[str] | None) -> tuple[list[str], dict]:
        from .. import settings_store
        sk = {"stale": 0, "wide_spread": 0}
        if symbols:
            base = [s.upper() for s in symbols]
        else:
            mode = settings_store.get("scanner_universe")
            if mode == "watchlist":
                from ..models import WatchItem
                with session_scope() as s:
                    base = sorted({w.symbol for w in s.query(WatchItem).all()})
            elif mode == "all":
                base = sorted(ctx.feed.instruments)
            else:
                base = sorted(ctx.feed.quotes)
        classes = set(p.get("asset_classes") or [])
        out = []
        for sym in base:
            inst = ctx.feed.info(sym)
            if classes and inst.asset_class not in classes:
                continue
            if ctx.feed.freshness(sym)["status"] != "live":
                sk["stale"] += 1
                continue
            sb = self._spread_bps(ctx, sym)
            if sb is not None and sb > float(p["max_spread_bps"]):
                sk["wide_spread"] += 1
                continue
            out.append(sym)
        cap = min(int(p["max_instruments"]), int(settings_store.get("scanner_max_instruments")))
        return out[:cap], sk

    @staticmethod
    def _spread_bps(ctx: AgentContext, sym: str) -> float | None:
        q = ctx.feed.quotes.get(sym)
        if not q or q.spread is None or q.mid <= 0:
            return None
        return round(q.spread / q.mid * 1e4, 2)

    @staticmethod
    def _write_watchlist(hits: list[dict], ttl_h: int) -> None:
        now = utcnow()
        with session_scope() as s:
            s.execute(delete(WatchItem).where(WatchItem.source == "scanner"))
            for h in hits:  # une ligne propre au Scanner, même si l'instrument est déjà suivi pour une autre raison
                s.add(WatchItem(symbol=h["symbol"], source="scanner", reason="; ".join(h["reasons"])[:500],
                                expires_at=now + dt.timedelta(hours=ttl_h)))
