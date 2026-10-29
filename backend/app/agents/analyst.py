"""Agent Analyst — skill source : trader-analyst (facts.py).

Pour les ACTIONS (CFD actions) : lit les résultats déclarés à la SEC (XBRL « company facts ») avec `analyse`, `changes`, `latest_filings`,
`render`, `metrics_row` (chiffres uniquement — le skill ne dit jamais « achetez »). Pour les autres classes d'actifs (forex, métaux, énergie,
crypto, indices) il n'existe pas de résultats trimestriels : l'agent est alors PARTIEL et fournit seulement un contexte de marché calculé
sur les bougies journalières (variations, plus haut/bas 20 jours, RSI 14 du skill Scanner), clairement étiqueté comme tel.
Résumé en langage clair par IA : optionnel, à partir des seuls chiffres calculés.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import SecretStore
from .base import Agent, AgentContext, AgentResult, llm_summary, params_of
from .loader import load_skill

SYSTEM = ("Tu es un analyste prudent. À partir des SEULS faits fournis (JSON), rédige en français 4 à 6 phrases claires sur ce qui a changé. "
          "N'invente aucun chiffre, ne donne aucun conseil d'achat ou de vente, ne prédis pas le cours.")


class AnalystAgent(Agent):
    id = "analyst"
    name = "Agent Analyst"
    mission = "Expliquer les résultats déclarés d'une société (actions) ou, à défaut, le contexte de marché récent d'un instrument — sans recommandation."
    skill = "trader-analyst"
    implementation = "partial"
    uses_llm = True
    limitations = [
        "Résultats SEC (XBRL) disponibles uniquement pour les actions américaines ayant un CIK ; configuration SEC_USER_AGENT (nom + e-mail) obligatoire.",
        "Forex, métaux, énergie, crypto, indices : pas de bilans — l'agent ne fournit qu'un contexte de marché calculé sur les bougies journalières (statut « partiel »).",
        "L'IA (optionnelle) ne fait que reformuler des chiffres déjà calculés ; aucune recommandation.",
        "Les pièces jointes des communiqués (EX-99) ne sont pas téléchargées : liens uniquement.",
    ]
    dependencies = ["SEC EDGAR (HTTPS, actions)", "bougies journalières BiQuote", "OpenRouter (optionnel)"]
    default_params = {"quarters": 6, "use_llm": True}
    param_help = {"quarters": "nombre de trimestres affichés", "use_llm": "résumé en langage clair par l'IA (si disponible)"}
    inputs = ["symbol"]
    outputs = ["mode (sec|market_context)", "rows[]", "changes[]", "filings", "market_context", "summary_ai"]

    async def run(self, ctx: AgentContext, symbol: str = "", **_: Any) -> AgentResult:
        p = params_of(self, ctx)
        symbol = symbol.upper()
        try:
            inst = ctx.feed.info(symbol)
        except KeyError:
            return AgentResult("error", f"{symbol} : instrument inconnu.", {"symbol": symbol}, instrument=symbol)
        warnings: list[str] = []
        out: dict[str, Any] = {"symbol": symbol, "asset_class": inst.asset_class}
        status = "partial"
        if inst.asset_class == "equity":
            sec = await self._sec(ctx, symbol, int(p["quarters"]), warnings)
            if sec:
                out.update(sec)
                out["mode"] = "sec"
                status = "ok"
        if status != "ok":
            out["mode"] = "market_context"
            out["market_context"] = await self._context(ctx, symbol, warnings)
            if inst.asset_class != "equity":
                warnings.append("pas de résultats d'entreprise pour cette classe d'actifs : contexte de marché uniquement (agent partiel).")
        model = None
        if p["use_llm"]:
            facts = {k: out.get(k) for k in ("changes", "market_context", "rows", "ttm") if out.get(k)}
            text, model = await llm_summary(ctx, self.id, SYSTEM, facts, warnings)
            if text:
                out["summary_ai"] = text
        summary = (f"{symbol} : résultats SEC lus ({len(out.get('rows', []))} trimestres)." if out.get("mode") == "sec"
                   else f"{symbol} : contexte de marché (pas de résultats d'entreprise disponibles).")
        return AgentResult(status, summary, out, warnings, ctx.quality(symbol), symbol, model)

    # ------------------------------------------------------------------ SEC
    async def _get_json(self, ctx: AgentContext, url: str, ua: str) -> Any:
        r = await ctx.http.get(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"}, timeout=25, follow_redirects=True)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()

    async def _sec(self, ctx: AgentContext, symbol: str, quarters: int, warnings: list[str]) -> dict | None:
        f = load_skill("analyst")
        ua = (SecretStore().get("sec_user_agent") or "").strip()
        if not ua or "@" not in ua or f.PLACEHOLDER in ua:
            warnings.append("SEC_USER_AGENT non configuré (« Prénom Nom adresse@email ») : résultats SEC non lus.")
            return None
        tk = symbol.split(".")[0].replace("-", ".")
        try:
            tickers = await self._get_json(ctx, f.TICKERS_URL, ua)
            table = {v["ticker"].upper().replace("-", "."): (str(v["cik_str"]).zfill(10), f.clean_title(v.get("title"))) for v in tickers.values()}
            if tk not in table:
                warnings.append(f"{tk} : pas de CIK à la SEC (société non américaine ?).")
                return None
            cik, name = table[tk]
            facts = await self._get_json(ctx, f.FACTS_URL % cik, ua)
            res = f.analyse(facts, quarters, name)
        except f.FactsError as e:
            warnings.append(f"{tk} : {e}")
            return None
        except Exception as e:  # noqa: BLE001
            warnings.append(f"SEC indisponible : {str(e)[:100]}")
            return None
        filings = None
        try:
            subs = await self._get_json(ctx, f.SUBMISSIONS_URL % cik, ua)
            filings = f.latest_filings(subs, cik, None)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"liste des dépôts indisponible : {str(e)[:80]}")
        note = f.stale_note(res, filings) if filings else None
        if note:
            warnings.append(note)
        return {"entity": res["entity"], "cik": res["cik"], "rows": res["rows"], "ttm": res["ttm"], "changes": f.changes(res), "filings": filings,
                "metrics": f.metrics_row(tk, res), "report_text": f.render(tk, res, filings), "source": "SEC XBRL company facts"}

    # ------------------------------------------------------------------ contexte de marché (agent partiel)
    async def _context(self, ctx: AgentContext, symbol: str, warnings: list[str]) -> dict | None:
        scan = load_skill("scanner")
        try:
            bars = [b for b in await ctx.feed.get_bars(symbol, "1d", 60) if b.closed]
        except Exception as e:  # noqa: BLE001
            warnings.append(f"bougies journalières indisponibles : {str(e)[:80]}")
            return None
        if len(bars) < 6:
            warnings.append("historique journalier insuffisant (< 6 jours).")
            return None
        c = [b.c for b in bars]
        last = c[-1]

        def chg(n: int) -> float | None:
            return round((last / c[-1 - n] - 1) * 100, 2) if len(c) > n else None

        w = bars[-20:]
        rsi = scan.rsi(c, 14) if len(c) >= 15 else None
        rngs = [(b.h - b.l) / b.c * 100 for b in bars[-14:]]
        q = ctx.feed.quotes.get(symbol)
        return {"last_close": last, "change_1d_pct": chg(1), "change_5d_pct": chg(5), "change_20d_pct": chg(20),
                "high_20d": max(b.h for b in w), "low_20d": min(b.l for b in w),
                "rsi_14": round(rsi, 1) if rsi is not None else None, "avg_daily_range_14d_pct": round(sum(rngs) / len(rngs), 3),
                "bars_used": len(bars), "live_price": q.mid if q else None,
                "note": "Calculs déterministes sur bougies journalières BiQuote ; pas de bilans pour cet instrument."}
