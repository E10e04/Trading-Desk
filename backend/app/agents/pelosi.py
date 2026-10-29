"""Agent Pelosi Disclosures — skill source : trader-pelosi (ptr.py).

Suit les déclarations publiques de transactions (Periodic Transaction Reports, loi STOCK Act) déposées à la Chambre des
représentants (index officiel du House Clerk). Fonctions du skill réutilisées : `read_index_bytes`, `parse_index`, `select`,
`new_since`, `parse_ptr_text`, `pdf_to_text`, `describe`.

INFORMATIF UNIQUEMENT : ces déclarations arrivent jusqu'à 45 jours après les transactions et ne donnent que des tranches de
montants. L'agent ne génère aucun signal, aucun ordre ; il n'alimente pas Risk. Le Sénat n'est pas couvert (accord manuel requis).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import tempfile
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import DisclosureSeen
from ..notify import notify
from .base import Agent, AgentContext, AgentResult, params_of
from .loader import load_skill

DISCLAIMER = ("Information publique différée (jusqu'à 45 jours), montants en tranches : ce n'est ni une information privilégiée ni un signal de trading. "
              "Aucun ordre n'est généré à partir de ces données.")


class PelosiAgent(Agent):
    id = "pelosi"
    name = "Agent Pelosi Disclosures"
    mission = "Informer des nouvelles déclarations de transactions boursières de membres de la Chambre (House Clerk), sans jamais trader dessus."
    skill = "trader-pelosi"
    implementation = "full"
    limitations = [
        "Chambre des représentants uniquement (le Sénat exige l'acceptation d'un accord sur efdsearch.senate.gov : non automatisé).",
        "Lecture des PDF : nécessite l'outil `pdftotext` (poppler) sur la machine ; sans lui, l'agent liste les dépôts avec le lien du PDF (statut « partiel »).",
        "Données différées et en tranches ; les titres sont des actions US : un rapprochement avec un instrument BiQuote n'est indiqué que si le ticker existe dans le catalogue.",
        "État « déjà vu » conservé en base (table disclosures_seen) au lieu de trader/state/pelosi.json.",
    ]
    dependencies = ["disclosures-clerk.house.gov (HTTPS)", "pdftotext (optionnel)"]
    default_interval_s = 6 * 3600
    default_params = {"members": ["Pelosi"], "year": None, "read_pdfs": True, "max_pdfs": 5, "first_run_days": 45, "mark_seen": True, "notify": True}
    param_help = {"members": "noms recherchés (ex. « Pelosi », « Nancy Pelosi »)", "year": "année de l'index (vide = année en cours)",
                  "read_pdfs": "télécharger et lire les nouveaux PDF (pdftotext requis)", "max_pdfs": "nombre maximal de PDF lus par exécution",
                  "mark_seen": "mémoriser les dépôts vus (une alerte par dépôt)"}
    inputs = ["members (optionnel)", "year (optionnel)"]
    outputs = ["filings[] (nouveaux dépôts)", "trades[] (lignes lues)", "disclaimer"]

    async def run(self, ctx: AgentContext, members: list[str] | None = None, year: int | None = None, offline_index: bytes | None = None,
                  **_: Any) -> AgentResult:
        ptr = load_skill("pelosi")
        p = params_of(self, ctx)
        now = ctx.clock()
        members = members or p["members"]
        year = int(year or p["year"] or now.year)
        warnings: list[str] = []
        try:
            data = offline_index if offline_index is not None else await self._download(ctx, ptr.INDEX_URL % (year, "FD"))
            rows = ptr.parse_index(ptr.read_index_bytes(data), year)
        except Exception as e:  # noqa: BLE001
            return AgentResult("error", f"Index House Clerk {year} indisponible : {str(e)[:150]}", {"disclaimer": DISCLAIMER, "error": str(e)[:200]}, [str(e)[:150]], "none")
        sel = ptr.select(rows, members, "P", None)
        with session_scope() as s:
            seen = [d.doc_id for d in s.scalars(select(DisclosureSeen))]
            first = not seen and s.scalar(select(DisclosureSeen.doc_id).limit(1)) is None
        state = {"seen": seen, "last_check": None if first else now.isoformat()}
        fresh = ptr.new_since(sel, state, now.date(), int(p["first_run_days"]))
        has_pdftotext = bool(ptr.shutil.which("pdftotext"))
        filings, trades, read, failed = [], [], 0, 0
        for r in fresh:
            item = {k: r[k] for k in ("doc_id", "name", "district", "filed", "url", "type_name")}
            item["rows"] = None
            if p["read_pdfs"] and read < int(p["max_pdfs"]):
                try:
                    text = await self._pdf_text(ctx, ptr, r["url"])
                    if text is None:
                        raise RuntimeError("pdftotext indisponible" if not has_pdftotext else "lecture du PDF impossible")
                    parsed = ptr.parse_ptr_text(text, r["filed"])
                    item["rows"] = [{**{k: row.get(k) for k in ("owner_name", "asset", "ticker", "type_name", "date", "amount", "late", "days_to_disclose")},
                                     "text": ptr.describe(row), "tradable_symbol": self._match(ctx, row.get("ticker"))} for row in parsed["rows"]]
                    trades += [{**t, "filing": r["doc_id"], "member": r["name"]} for t in item["rows"]]
                    read += 1
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    item["read_error"] = str(e)[:120]
            filings.append(item)
        if failed:
            warnings.append(f"{failed} PDF non lu(s) — liens fournis ; installez `pdftotext` (poppler) pour la lecture automatique.")
        if p["mark_seen"] and fresh:
            with session_scope() as s:
                for r in fresh:
                    if s.get(DisclosureSeen, r["doc_id"]) is None:
                        s.add(DisclosureSeen(doc_id=r["doc_id"], payload={"name": r["name"], "filed": r["filed"]}))
        if p["notify"]:
            for f in filings:
                n = len(f["rows"] or [])
                notify(ctx.bus, "info", "pelosi", f"Nouvelle déclaration : {f['name']} ({f['filed']})",
                       (f"{n} transaction(s) lue(s). " if f["rows"] is not None else "PDF non lu. ") + f["url"] + " — " + DISCLAIMER, dedupe_key=f"pelosi:{f['doc_id']}")
        status = "partial" if failed else "ok"
        summary = (f"{len(filings)} nouveau(x) dépôt(s) pour {', '.join(members)} ({len(sel)} au total en {year}), {len(trades)} transaction(s) lue(s)."
                   if filings else f"Aucun nouveau dépôt pour {', '.join(members)} ({len(sel)} au total en {year}).")
        return AgentResult(status, summary, {"filings": filings, "trades": trades, "total_filings_year": len(sel), "year": year, "disclaimer": DISCLAIMER,
                                             "pdftotext": has_pdftotext}, warnings, "historical")

    @staticmethod
    async def _download(ctx: AgentContext, url: str) -> bytes:
        r = await ctx.http.get(url, headers={"User-Agent": "trading-desk/1.0 (paper trading research)"}, timeout=60, follow_redirects=True)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.content

    async def _pdf_text(self, ctx: AgentContext, ptr: Any, url: str) -> str | None:
        data = await self._download(ctx, url)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ptr.pdf")
            with open(path, "wb") as fh:
                fh.write(data)
            return await asyncio.to_thread(ptr.pdf_to_text, path)

    @staticmethod
    def _match(ctx: AgentContext, ticker: str | None) -> str | None:
        if not ticker:
            return None
        t = ticker.upper()
        for sym in ctx.feed.instruments:
            if sym.upper() == t or sym.upper().split(".")[0] == t:
                return sym
        return None
