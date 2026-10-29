"""Agent News — skill source : trader-news (news.py).

Fonctions du skill réutilisées telles quelles : `parse_rss` (Yahoo Finance), `parse_sec_atom` (8-K EDGAR),
`tag` (mots-clés + valeurs concernées), `dedupe`, `select` (ne montre que le nouveau), `remember`, `fingerprint`,
et ses constantes (ITEMS_8K, IMPORTANT_ITEMS, KEYWORDS, SEC_FEED). Les sources sont celles RÉELLEMENT configurées :
Yahoo/SEC pour les actions, flux d'actualités et calendrier économique de BiQuote pour le forex, les métaux, la crypto
et les indices. Une source injoignable est signalée comme telle ; rien n'est simulé.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import httpx
from sqlalchemy import delete

from ..config import SecretStore
from ..db import session_scope
from ..models import NewsSeen, utcnow
from .base import Agent, AgentContext, AgentResult, llm_summary, params_of
from .loader import load_skill

UTC = dt.timezone.utc
CCY_COUNTRY = {"USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP", "CHF": "CH", "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CNY": "CN",
               "CNH": "CN", "SEK": "SE", "NOK": "NO", "TRY": "TR", "ZAR": "ZA", "MXN": "MX", "INR": "IN", "BRL": "BR"}
MOVE_PCT = {"forex": 0.6, "metal": 1.5, "energy": 3.0, "crypto": 4.0, "index": 1.5, "equity": 3.0, "other": 3.0}
SYSTEM = ("Tu résumes des actualités de marché pour un trader, en français, en 4 phrases maximum. "
          "Sépare strictement : (1) FAITS (dépôts officiels, événements de calendrier, avec date et source), "
          "(2) PISTES (titres de presse non vérifiés), (3) INCERTITUDES. Utilise UNIQUEMENT les éléments fournis, "
          "n'invente aucun chiffre ni citation, ne prédis jamais le prix et ne recommande jamais d'acheter ou de vendre.")


def _aware(d: dt.datetime) -> dt.datetime:
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _first(d: dict, *keys: str) -> Any:
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if low.get(k.lower()) not in (None, ""):
            return low[k.lower()]
    return None


def _parse_time(v: Any) -> dt.datetime | None:
    from ..market.provider import parse_ts
    t = parse_ts(v)
    return t.replace(tzinfo=UTC) if t else None


class NewsAgent(Agent):
    id = "news"
    name = "Agent News"
    mission = "Lire les actualités, dépôts officiels et événements macro qui touchent les instruments suivis, et ne remonter que le nouveau."
    skill = "trader-news"
    implementation = "full"
    limitations = [
        "Sources réelles uniquement : Yahoo Finance + SEC EDGAR 8-K pour les actions (SEC exige un SEC_USER_AGENT), flux d'actualités et calendrier économique BiQuote pour le reste.",
        "La variation du jour (MOVING) est calculée depuis la dernière clôture journalière BiQuote, avec un seuil propre à chaque classe d'actifs (3 % pour les actions, 0,6 % pour le forex...).",
        "Un titre est une piste, pas un fait : seuls les dépôts officiels et le calendrier sont présentés comme des faits.",
    ]
    dependencies = ["Yahoo Finance RSS (actions)", "SEC EDGAR (actions, SEC_USER_AGENT)", "BiQuote /api/news et /api/calendar", "OpenRouter (résumé optionnel)"]
    uses_llm = True
    default_interval_s = 900
    default_params = {"hours": 12, "calendar_hours_ahead": 6, "use_llm": True, "mark_seen": True, "max_items": 40}
    param_help = {"hours": "fenêtre de recherche en heures", "calendar_hours_ahead": "événements macro à surveiller dans les N prochaines heures",
                  "use_llm": "résumé IA (faits / pistes / incertitudes) si une clé OpenRouter est configurée", "mark_seen": "mémoriser ce qui a été montré"}
    inputs = ["symbols"]
    outputs = ["items[]", "per_symbol[]", "risk_flags[]", "sources[] (statut de chaque source)", "summary_ai"]

    async def run(self, ctx: AgentContext, symbols: list[str] | None = None, show_all: bool = False, **_: Any) -> AgentResult:
        p = params_of(self, ctx)
        news = load_skill("news")
        now = _aware(ctx.clock())
        warnings: list[str] = []
        symbols = [s.upper() for s in (symbols or self._watch(ctx))]
        if not symbols:
            return AgentResult("skipped", "Aucun instrument à surveiller.", {}, warnings, "none")
        insts = {s: ctx.feed.info(s) for s in symbols}
        watch = {s: (insts[s].description or "") for s in symbols}
        sources: list[dict] = []
        items: list[dict] = []

        # ---- actions : Yahoo + SEC (fonctions du skill)
        eq = [s for s in symbols if insts[s].asset_class == "equity"]
        if eq:
            st = {"name": "Yahoo Finance", "ok": 0, "failed": 0, "errors": []}
            for s in eq:
                tk = s.split(".")[0]
                try:
                    data = await self._get(ctx, news.YAHOO % news.yahoo_symbol(tk), headers={"User-Agent": news.BROWSER_UA})
                    items += news.parse_rss(data, s)
                    st["ok"] += 1
                except Exception as e:
                    st["failed"] += 1
                    st["errors"].append(f"{s}: {str(e)[:100]}")
            sources.append(self._status(st))
            ua = (SecretStore().get("sec_user_agent") or "").strip()
            if ua and "@" in ua and news.PLACEHOLDER not in ua:
                sources.append(await self._sec(ctx, news, eq, ua, items))
            else:
                sources.append({"name": "SEC EDGAR", "status": "not_configured", "detail": "définissez SEC_USER_AGENT (« Prénom Nom adresse@email »)"})
        # ---- BiQuote : actualités + calendrier
        prov = ctx.feed.provider
        if hasattr(prov, "news"):
            sources.append(await self._biquote_news(ctx, prov, symbols, insts, items))
            sources.append(await self._calendar(ctx, prov, symbols, insts, items, now, int(p["calendar_hours_ahead"])))
        else:
            sources.append({"name": "BiQuote news/calendrier", "status": "not_connected", "detail": "le fournisseur actif n'expose pas ces flux"})

        # ---- fonctions du skill : tag / dedupe / select / remember
        news.tag(items, watch)
        items = news.dedupe(items)
        state = {"seen": self._load_seen()}
        selected = news.select(items, state, now, hours=int(p["hours"]), show_all=show_all)
        # `select` écarte ce qui est dans le futur : les événements de calendrier à venir sont traités ici, avec le même
        # principe « ne jamais répéter » (empreinte du skill).
        upcoming = [i for i in items if i.get("kind") == "calendar" and i.get("time") and i["time"] > now]
        for i in upcoming:
            i["new"] = news.fingerprint(i) not in state["seen"]
        selected = [i for i in selected if i.get("kind") != "calendar"] + [i for i in upcoming if i["new"] or show_all]
        if p["mark_seen"] and not show_all:
            news.remember(selected, state, now)
            self._save_seen(state["seen"])

        moves = self._moves(ctx, symbols, insts)
        per_symbol, flags = [], []
        for s in symbols:
            its = [i for i in selected if s in i.get("touches", [])]
            mv = moves.get(s)
            sym_flags = self._flags(its, mv, insts[s], now, int(p["calendar_hours_ahead"]))
            flags += [{"symbol": s, **f} for f in sym_flags]
            per_symbol.append({"symbol": s, "new_items": len(its), "move": mv,
                               "tags": sorted({t for i in its for t in i.get("tags", [])}), "flags": sym_flags,
                               "facts": [self._view(i) for i in its if i["kind"] in ("filing", "calendar")][:10],
                               "leads": [self._view(i) for i in its if i["kind"] == "headline"][:10]})
        out_items = [self._view(i) for i in selected][: int(p["max_items"])]
        unreach = [x for x in sources if x.get("status") in ("unavailable", "partial")]
        if unreach:
            warnings.append("Sources indisponibles ou partielles : " + ", ".join(x["name"] for x in unreach) + " — résultat incomplet.")
        result = {"items": out_items, "per_symbol": per_symbol, "risk_flags": flags, "sources": sources, "window_hours": int(p["hours"]),
                  "uncertainties": ["Un titre est une piste à vérifier, pas un fait.", "Aucune prévision de prix n'est produite."]}
        model = None
        if p["use_llm"] and out_items:
            facts = {"symbols": symbols, "items": [{k: i[k] for k in ("title", "source", "kind", "time", "tags", "touches") if k in i} for i in out_items[:15]],
                     "flags": flags}
            text, model = await llm_summary(ctx, self.id, SYSTEM, facts, warnings)
            result["summary_ai"] = text
        n_new = len(out_items)
        status = "partial" if unreach and not n_new else ("ok" if not unreach else "partial")
        if not any(x.get("status") == "ok" for x in sources):
            status = "partial"
            warnings.append("Aucune source d'actualité n'a répondu : absence de nouvelles ≠ absence d'événements.")
        summary = f"{n_new} élément(s) nouveau(x) sur {len(symbols)} instrument(s) ; {len(flags)} alerte(s) de risque ; {sum(1 for x in sources if x.get('status') == 'ok')}/{len(sources)} source(s) disponible(s)."
        return AgentResult(status, summary, result, warnings, ctx.quality(symbols[0]) if symbols else "none", symbols[0] if len(symbols) == 1 else None, model)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _watch(ctx: AgentContext) -> list[str]:
        from ..models import WatchItem
        with session_scope() as s:
            return [w.symbol for w in s.query(WatchItem).all()]

    @staticmethod
    def _status(st: dict) -> dict:
        status = "ok" if st["failed"] == 0 and st["ok"] else ("unavailable" if not st["ok"] else "partial")
        return {"name": st["name"], "status": status, "detail": "; ".join(st["errors"][:3]) or f"{st['ok']} flux lus"}

    @staticmethod
    async def _get(ctx: AgentContext, url: str, headers: dict | None = None, timeout: float = 20.0) -> bytes:
        r = await ctx.http.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.content

    async def _sec(self, ctx: AgentContext, news: Any, eq: list[str], ua: str, items: list[dict]) -> dict:
        st = {"name": "SEC EDGAR 8-K", "ok": 0, "failed": 0, "errors": []}
        try:
            raw = await self._get(ctx, news.TICKERS_URL, headers={"User-Agent": ua})
            table = {str(v["ticker"]).upper(): f"{int(v['cik_str']):010d}" for v in json.loads(raw).values()}
        except Exception as e:
            st["failed"] += 1
            st["errors"].append(f"liste des CIK: {str(e)[:80]}")
            return self._status(st)
        for s in eq:
            cik = table.get(s.split(".")[0])
            if not cik:
                st["errors"].append(f"{s}: pas de CIK")
                continue
            try:
                data = await self._get(ctx, news.SEC_FEED % (cik, "8-K", 10), headers={"User-Agent": ua})
                items += news.parse_sec_atom(data, s)
                st["ok"] += 1
            except Exception as e:
                st["failed"] += 1
                st["errors"].append(f"{s}: {str(e)[:80]}")
        out = self._status(st)
        if not st["failed"] and not st["ok"]:  # la source a répondu mais n'a aucun dépôt pour ces valeurs (pas une panne)
            out["status"] = "ok"
        return out

    async def _biquote_news(self, ctx: AgentContext, prov: Any, symbols: list[str], insts: dict, items: list[dict]) -> dict:
        st = {"name": "BiQuote actualités", "ok": 0, "failed": 0, "errors": []}
        kinds: dict[str, list[str]] = {}
        for s in symbols:
            if insts[s].asset_class == "equity":
                continue
            kinds.setdefault("crypto" if insts[s].asset_class == "crypto" else "market", []).append(s)
        if not kinds:
            return {"name": st["name"], "status": "ok", "detail": "non requis pour ces instruments"}
        for kind, syms in kinds.items():
            try:
                rows = await prov.news(kind)
                st["ok"] += 1
            except Exception as e:
                st["failed"] += 1
                st["errors"].append(f"{kind}: {str(e)[:80]}")
                continue
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                title = str(_first(r, "title", "headline") or "").strip()
                if not title:
                    continue
                link = str(_first(r, "url", "link") or "")
                items.append({"id": "bq:" + (link or title), "source": str(_first(r, "source", "publisher") or "BiQuote"), "kind": "headline",
                              "ticker": syms[0], "title": title, "summary": str(_first(r, "summary", "description") or ""), "link": link,
                              "time": _parse_time(_first(r, "publishedAt", "published", "time", "date", "timestamp"))})
                # ce flux est général : on rattache l'élément aux instruments dont une devise/un nom y figure
        return self._status(st)

    async def _calendar(self, ctx: AgentContext, prov: Any, symbols: list[str], insts: dict, items: list[dict], now: dt.datetime, ahead_h: int) -> dict:
        ccys = {c for s in symbols for c in (insts[s].base_ccy, insts[s].quote_ccy) if c}
        if not ccys:
            return {"name": "BiQuote calendrier économique", "status": "ok", "detail": "non requis"}
        try:
            rows = await prov.calendar_upcoming()
        except Exception as e:
            return {"name": "BiQuote calendrier économique", "status": "unavailable", "detail": str(e)[:100]}
        countries = {CCY_COUNTRY[c]: c for c in ccys if c in CCY_COUNTRY}
        n = 0
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            ctry = str(_first(r, "country", "countryCode", "currency") or "").upper()
            ccy = countries.get(ctry) or (ctry if ctry in ccys else None)
            when = _parse_time(_first(r, "time", "date", "datetime", "timestamp", "utc"))
            imp = str(_first(r, "importance", "impact", "volatility") or "").lower()
            if not ccy or not when or imp not in ("high", "3", "red"):
                continue
            title = str(_first(r, "title", "event", "name") or "").strip()
            touches = [s for s in symbols if ccy in (insts[s].base_ccy, insts[s].quote_ccy)]
            detail = ", ".join(f"{k} {v}" for k, v in (("prévu", _first(r, "forecast")), ("précédent", _first(r, "previous")), ("publié", _first(r, "actual"))) if v is not None)
            items.append({"id": f"cal:{ccy}:{title}:{when.isoformat()}", "source": "BiQuote calendrier", "kind": "calendar", "ticker": touches[0],
                          "title": f"{ccy} · {title} ({when:%H:%M} UTC, impact élevé)", "summary": detail, "link": "", "time": when, "touches_ccy": ccy})
            n += 1
        return {"name": "BiQuote calendrier économique", "status": "ok", "detail": f"{n} événement(s) à fort impact pour {sorted(ccys)}"}

    def _moves(self, ctx: AgentContext, symbols: list[str], insts: dict) -> dict[str, dict]:
        out = {}
        for s in symbols:
            q = ctx.feed.quotes.get(s)
            cached = ctx.feed._hist_cache.get((s, "1d"))
            if not q or not cached:
                continue
            closed = [b for b in cached[1] if b.closed and dt.datetime.fromtimestamp(b.ts, UTC).date() < _aware(ctx.clock()).date()]
            if not closed:
                continue
            pct = (q.mid / closed[-1].c - 1) * 100
            thr = MOVE_PCT.get(insts[s].asset_class, 3.0)
            out[s] = {"pct": round(pct, 3), "vs_close": closed[-1].c, "threshold_pct": thr, "moving": abs(pct) >= thr}
        return out

    def _flags(self, its: list[dict], move: dict | None, inst: Any, now: dt.datetime, ahead_h: int) -> list[dict]:
        flags = []
        for i in its:
            if i["kind"] == "filing" and i.get("important"):
                flags.append({"level": "high", "kind": "filing", "text": i["title"], "link": i.get("link")})
            elif i["kind"] == "calendar" and i.get("time") and now <= i["time"] <= now + dt.timedelta(hours=ahead_h):
                flags.append({"level": "high", "kind": "calendar", "text": i["title"]})
            elif set(i.get("tags", [])) & {"halt", "risk"}:
                flags.append({"level": "medium", "kind": "headline", "text": i["title"], "link": i.get("link")})
        if move and move["moving"]:
            flags.append({"level": "medium", "kind": "move", "text": f"mouvement de {move['pct']:+.2f} % depuis la dernière clôture (seuil {move['threshold_pct']} %)"})
        return flags

    @staticmethod
    def _view(i: dict) -> dict:
        t = i.get("time")
        return {"id": i["id"], "title": i["title"], "source": i["source"], "kind": i["kind"], "link": i.get("link"), "summary": (i.get("summary") or "")[:300],
                "time": t.isoformat() if t else None, "tags": i.get("tags", []), "touches": i.get("touches", []), "important": i.get("important", False),
                "new": i.get("new", True)}

    @staticmethod
    def _load_seen() -> dict[str, str]:
        with session_scope() as s:
            return {r.fingerprint: r.seen_at.isoformat() for r in s.query(NewsSeen).all()}

    @staticmethod
    def _save_seen(seen: dict[str, str]) -> None:
        with session_scope() as s:
            have = {r.fingerprint for r in s.query(NewsSeen).all()}
            for fp, ts in seen.items():
                if fp not in have:
                    s.add(NewsSeen(fingerprint=fp, seen_at=utcnow()))
            s.execute(delete(NewsSeen).where(NewsSeen.seen_at < utcnow() - dt.timedelta(days=30)))
