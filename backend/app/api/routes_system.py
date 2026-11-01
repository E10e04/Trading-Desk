"""Réglages, IA (OpenRouter), diagnostic du système, session locale."""
from __future__ import annotations

import datetime as dt
import shutil
import sys
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text

from .. import settings_store
from ..agents.loader import integrity, provenance
from ..agents.registry import ORDER
from ..config import APP_NAME, APP_VERSION, SecretStore, get_settings
from ..db import session_scope
from ..llm.openrouter import LlmError
from ..models import AgentRun
from ..runtime import Runtime
from .deps import Auth, runtime

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Auth])

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@public.get("/health")
def health():
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION}


@public.get("/session")
def session(request: Request):
    """Jeton local pour l'interface servie par ce backend — uniquement depuis la machine elle-même (loopback), jamais via un proxy."""
    host = request.client.host if request.client else ""
    if host not in LOOPBACK or "x-forwarded-for" in request.headers or "forwarded" in request.headers:
        raise HTTPException(403, "jeton disponible uniquement en local : saisissez API_TOKEN dans l'interface")
    return {"token": get_settings().api_token}


@router.get("/meta")
def meta(rt: Runtime = Depends(runtime)):
    return {"app": APP_NAME, "version": APP_VERSION, "paper_only": True, "live_trading": False, "provenance": provenance(), "provider": rt.provider.name,
            "default_model": rt.llm.default_model, "python": sys.version.split()[0]}


# ---------------------------------------------------------------- mises à jour
@router.get("/update/check")
async def update_check(force: bool = False):
    """Compare la version installée à la dernière release GitHub. Une panne réseau n'est jamais bloquante : `error` est renseigné."""
    from .. import updates
    try:
        return {**await updates.check(force=force), "error": None, "repo": updates.DEFAULT_REPO}
    except Exception as e:  # noqa: BLE001
        return {"current": APP_VERSION, "latest": None, "releases": [], "update_available": False, "repo": updates.DEFAULT_REPO,
                "error": f"vérification impossible : {type(e).__name__}: {str(e)[:120]}"}


# ---------------------------------------------------------------- réglages
@router.get("/settings")
def get_settings_():
    return {"values": settings_store.get_all(), "defaults": settings_store.DEFAULTS}


@router.put("/settings")
def put_settings(values: dict[str, Any], rt: Runtime = Depends(runtime)):
    try:
        changed = "llm_model" in values and values["llm_model"] != settings_store.get("llm_model")
        out = {"values": settings_store.update(values)}
        if changed:  # le modèle choisi s'applique aussitôt à News et Analyste, sans modification manuelle
            out["agent_overrides_cleared"] = rt.orch.clear_model_overrides()
        return out
    except (KeyError, TypeError) as e:
        raise HTTPException(400, str(e)) from e


class SecretBody(BaseModel):
    sec_user_agent: str | None = None


@router.put("/settings/secrets")
def put_secrets(b: SecretBody):
    st = SecretStore()
    if b.sec_user_agent is not None:
        v = b.sec_user_agent.strip()
        if v and "@" not in v:
            raise HTTPException(400, "le User-Agent SEC doit contenir votre nom et votre e-mail")
        st.set("sec_user_agent", v)
    return {"sec_user_agent_configured": bool(st.get("sec_user_agent"))}


@router.get("/settings/secrets")
def get_secrets():
    st = SecretStore()
    ua = st.get("sec_user_agent")
    return {"sec_user_agent_configured": bool(ua), "sec_user_agent_masked": (ua[:3] + "…" + ua[-4:]) if len(ua) > 8 else ("•••" if ua else "")}


# ---------------------------------------------------------------- IA
class KeyBody(BaseModel):
    key: str


@router.get("/llm/status")
def llm_status(rt: Runtime = Depends(runtime)):
    ok, why = rt.llm.available()
    return {**rt.llm.key_status(), "available": ok, "unavailable_reason": why, "usage_today": rt.llm.usage_today(), "last_error": rt.llm.last_error}


@router.put("/llm/key")
def llm_set_key(b: KeyBody, rt: Runtime = Depends(runtime)):
    """Enregistre la clé côté serveur (fichier secrets.json 0600). Elle n'est jamais renvoyée ni journalisée."""
    try:
        rt.llm.set_key(b.key)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return rt.llm.key_status()


@router.delete("/llm/key")
def llm_delete_key(rt: Runtime = Depends(runtime)):
    rt.llm.set_key("")
    return rt.llm.key_status()


@router.get("/llm/models")
async def llm_models(refresh: bool = False, rt: Runtime = Depends(runtime)):
    try:
        return {"default": rt.llm.default_model, "items": await rt.llm.list_models(refresh)}
    except LlmError as e:
        raise HTTPException(502, str(e)) from e


class TestBody(BaseModel):
    model: str | None = None


@router.post("/llm/test")
async def llm_test(b: TestBody, rt: Runtime = Depends(runtime)):
    return await rt.llm.test_connection(b.model)


@router.get("/llm/calls")
def llm_calls(limit: int = 50, offset: int = 0, rt: Runtime = Depends(runtime)):
    """Appels IA récents, paginés (50 par page dans l'interface)."""
    return rt.llm.calls(max(1, min(limit, 200)), max(0, offset))


@router.get("/llm/stats")
def llm_stats(rt: Runtime = Depends(runtime)):
    return rt.llm.stats()


# ---------------------------------------------------------------- diagnostic
@router.get("/diagnostics")
def diagnostics(rt: Runtime = Depends(runtime)):
    checks: list[dict] = []

    def add(name: str, ok: bool | None, detail: str, level: str | None = None) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "level": level or ("ok" if ok else "error" if ok is False else "warning")})

    try:
        with session_scope() as s:
            s.execute(text("select 1"))
            n_runs = s.scalar(select(func.count(AgentRun.id)))
        add("Base de données", True, f"SQLite opérationnelle, {n_runs} exécutions d'agents enregistrées")
    except Exception as e:  # noqa: BLE001
        add("Base de données", False, str(e)[:150])
    h = rt.feed.health()
    st = (h.get("stream") or {})
    add("Flux BiQuote", st.get("state") == "connected", f"état {st.get('state')}, {h['live']} cotation(s) fraîche(s) / {h['quotes']}, {h['stale']} périmée(s), {h['closed']} fermée(s)")
    add("Catalogue d'instruments", bool(h["instruments"]), f"{h['instruments']} instruments" + (f" — erreur : {h['catalogue_error']}" if h["catalogue_error"] else ""))
    add("Conversion USD (EUR)", rt.feed.fx_to_usd("EUR") is not None, "taux EURUSD disponible" if rt.feed.fx_to_usd("EUR") else "EURUSD indisponible : instruments cotés en EUR non tradables")
    ok, why = rt.llm.available()
    add("IA (OpenRouter)", ok if rt.llm.key_status()["configured"] else None, "disponible" if ok else why, None if ok else "warning")
    ua = SecretStore().get("sec_user_agent")
    add("SEC EDGAR (Analyst/News)", bool(ua) or None, "User-Agent configuré" if ua else "SEC_USER_AGENT non configuré : résultats SEC indisponibles", None if ua else "warning")
    add("pdftotext (Pelosi)", bool(shutil.which("pdftotext")) or None, "installé" if shutil.which("pdftotext") else "absent : les PDF de déclarations ne seront pas lus", None if shutil.which("pdftotext") else "warning")
    integ = integrity()
    add("Skills vendorisés", all(integ.values()), f"{sum(integ.values())}/{len(integ)} chargés — {provenance()['repo']} @ {provenance()['commit'][:7]}")
    pf = rt.engine.list_portfolios()
    add("Portefeuilles papier", bool(pf) or None, f"{len(pf)} portefeuille(s)" if pf else "aucun portefeuille : créez-en un pour que le pipeline puisse trader", None if pf else "warning")
    if settings_store.get("kill_switch"):
        add("Interrupteur d'arrêt", None, "ACTIF : aucun nouvel ordre automatique", "warning")
    cons = []
    for p in pf:
        try:
            r = rt.engine.replay_from_fills(p["id"])
            if not r.get("consistent"):
                cons.append(p["name"])
        except Exception:  # noqa: BLE001
            cons.append(p["name"])
    add("Cohérence du journal d'exécutions", not cons, "reconstitution = état stocké pour tous les portefeuilles" if not cons else f"écart sur : {', '.join(cons)}")
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=24)
    with session_scope() as s:
        errs = [{"agent": r.agent_id, "at": r.started_at.isoformat() + "Z", "error": r.error or r.summary} for r in
                s.scalars(select(AgentRun).where(AgentRun.status == "error", AgentRun.started_at >= cutoff).order_by(AgentRun.id.desc()).limit(15))]
    return {"checks": checks, "feed": h, "pipeline": rt.orch.status(), "recent_errors": errs, "llm": rt.llm.stats(), "provenance": provenance(),
            "agents": ORDER, "server_time": dt.datetime.utcnow().isoformat() + "Z", "ws_clients": getattr(rt, "ws_clients", 0)}
