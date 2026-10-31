"""Agents : description, configuration, exécution manuelle, historique, pipeline, propositions, liste de surveillance."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from ..agents.alerts import AlertsAgent
from ..agents.loader import integrity, provenance, skill_md
from ..agents.registry import AGENTS, ORDER, get_agent
from ..db import session_scope
from ..models import AgentRun, WatchItem
from ..runtime import Runtime
from .deps import Auth, runtime

router = APIRouter(prefix="/api", dependencies=[Auth])


def run_view(r: AgentRun, full: bool = False) -> dict:
    d = {"id": r.id, "agent": r.agent_id, "skill_version": r.skill_version, "model": r.model, "instrument": r.instrument, "started_at": r.started_at.isoformat() + "Z",
         "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None, "duration_ms": r.duration_ms, "status": r.status, "trigger": r.trigger,
         "correlation_id": r.correlation_id, "summary": r.summary, "warnings": r.warnings, "error": r.error, "data_quality": r.data_quality}
    if full:
        d["input"], d["output"] = r.input, r.output
    return d


def agent_card(rt: Runtime, aid: str) -> dict:
    a = get_agent(aid)
    cfg = rt.orch.config(aid)
    with session_scope() as s:
        last = s.scalar(select(AgentRun).where(AgentRun.agent_id == aid).order_by(AgentRun.id.desc()).limit(1))
        counts = {st: s.query(AgentRun).filter(AgentRun.agent_id == aid, AgentRun.status == st).count() for st in ("ok", "partial", "skipped", "error")}
        last_view = run_view(last) if last else None
    return {**a.describe(), "config": cfg, "last_run": last_view, "run_counts": counts}


@router.get("/agents")
def agents(rt: Runtime = Depends(runtime)):
    return {"items": [agent_card(rt, a) for a in ORDER], "provenance": provenance(), "pipeline": rt.orch.status()}


@router.get("/agents/{aid}")
def agent(aid: str, rt: Runtime = Depends(runtime)):
    try:
        return agent_card(rt, aid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/agents/{aid}/skill")
def agent_skill(aid: str):
    try:
        get_agent(aid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {"agent": aid, "skill_md": skill_md(aid), "integrity": integrity(), "provenance": provenance()}


class ConfigBody(BaseModel):
    enabled: bool | None = None
    model: str | None = None
    clear_model: bool = False
    params: dict[str, Any] | None = None
    interval_s: int | None = None


@router.put("/agents/{aid}/config")
def agent_config(aid: str, body: ConfigBody, rt: Runtime = Depends(runtime)):
    try:
        return rt.orch.set_config(aid, body.enabled, body.model, body.params, body.interval_s, body.clear_model)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/agents/{aid}/reset")
def agent_reset(aid: str, rt: Runtime = Depends(runtime)):
    try:
        return rt.orch.reset_config(aid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


class RunBody(BaseModel):
    inputs: dict[str, Any] = {}


@router.post("/agents/{aid}/run")
async def agent_run(aid: str, body: RunBody, rt: Runtime = Depends(runtime)):
    try:
        get_agent(aid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    if aid == "broker":
        raise HTTPException(400, "le Broker ne s'exécute que sur une proposition validée : utilisez /api/proposals/{id}/decide ou le pipeline")
    res = await rt.orch.run_agent(aid, "manual", None, None, **body.inputs)
    return {"status": res.status, "summary": res.summary, "output": res.output, "warnings": res.warnings, "data_quality": res.data_quality, "model": res.model}


@router.get("/runs")
def runs(agent: str = "", instrument: str = "", status: str = "", correlation_id: str = "", limit: int = Query(100, le=500), offset: int = 0):
    with session_scope() as s:
        q = select(AgentRun).order_by(AgentRun.id.desc())
        if agent:
            q = q.where(AgentRun.agent_id == agent)
        if instrument:
            q = q.where(AgentRun.instrument == instrument.upper())
        if status:
            q = q.where(AgentRun.status == status)
        if correlation_id:
            q = q.where(AgentRun.correlation_id == correlation_id)
        return {"items": [run_view(r) for r in s.scalars(q.limit(limit).offset(offset))]}


@router.get("/runs/{run_id}")
def run_detail(run_id: int):
    with session_scope() as s:
        r = s.get(AgentRun, run_id)
        if not r:
            raise HTTPException(404, "exécution introuvable")
        return run_view(r, full=True)


# ---------------------------------------------------------------- pipeline
@router.get("/pipeline/status")
def pipeline_status(rt: Runtime = Depends(runtime)):
    return rt.orch.status()


class EvalBody(BaseModel):
    symbol: str
    setup: str | None = None
    level: float | None = None


@router.post("/pipeline/evaluate")
async def pipeline_evaluate(body: EvalBody, rt: Runtime = Depends(runtime)):
    """Évalue l'instrument maintenant ; si le signal est déclenché, la chaîne complète s'exécute (Risk obligatoire)."""
    out = await rt.orch.process_symbol(body.symbol, trigger="manual", setup=body.setup, level=body.level)
    return {"proposals": out, "triggered": bool(out)}


class SimBody(BaseModel):
    symbol: str
    portfolio_id: int
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    stop_rule: str | None = None
    target_rule: str | None = None


@router.post("/simulator/plan")
async def simulator_plan(body: SimBody, rt: Runtime = Depends(runtime)):
    return await rt.orch.simulate(body.symbol, body.portfolio_id, body.entry, body.stop, body.target, body.stop_rule, body.target_rule)


@router.get("/proposals")
def proposals(status: str = "", limit: int = Query(100, le=500), rt: Runtime = Depends(runtime)):
    return {"items": rt.orch.list_proposals(status or None, limit)}


@router.get("/proposals/{pid}")
def proposal(pid: int, rt: Runtime = Depends(runtime)):
    try:
        return rt.orch.proposal_view_by_id(pid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, "proposition introuvable") from e


class DecideBody(BaseModel):
    reply: str | None = None
    reject: bool = False


@router.post("/proposals/{pid}/decide")
async def decide(pid: int, body: DecideBody, rt: Runtime = Depends(runtime)):
    try:
        return await rt.orch.decide_proposal(pid, body.reply, body.reject)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


# ---------------------------------------------------------------- liste de surveillance
# Raison choisie par l'utilisateur -> libellé affiché et ORIGINE, déduite automatiquement de la raison.
WATCH_REASONS: dict[str, dict[str, str]] = {
    "idea": {"label": "Idée personnelle", "source": "manual"},
    "position": {"label": "Position à suivre", "source": "manual"},
    "price_level": {"label": "Niveau de prix à surveiller", "source": "alerts"},
    "scanner_setup": {"label": "Setup repéré (Scanner)", "source": "scanner"},
    "breakout": {"label": "Cassure attendue (Entry)", "source": "entry"},
    "news": {"label": "Actualité ou catalyseur (News)", "source": "news"},
    "fundamentals": {"label": "Analyse fondamentale (Analyst)", "source": "analyst"},
    "disclosures": {"label": "Déclarations d'élus (Pelosi)", "source": "pelosi"},
}
SOURCE_LABEL = {"manual": "Manuelle", "alerts": "Alertes", "scanner": "Scanner", "entry": "Entry", "news": "News", "analyst": "Analyst", "pelosi": "Pelosi"}


class WatchBody(BaseModel):
    symbol: str
    reason_code: str | None = None
    reason: str | None = None  # texte libre (rétrocompatibilité) ; ignoré si reason_code est fourni


@router.get("/watchlist")
def watchlist():
    with session_scope() as s:
        return {"items": [{"symbol": w.symbol, "source": w.source, "reason": w.reason, "added_at": w.added_at.isoformat() + "Z",
                           "expires_at": w.expires_at.isoformat() + "Z" if w.expires_at else None} for w in s.scalars(select(WatchItem).order_by(WatchItem.added_at.desc()))]}


@router.get("/watchlist/reasons")
def watch_reasons():
    return {"items": [{"code": k, "label": v["label"], "source": v["source"], "source_label": SOURCE_LABEL[v["source"]]} for k, v in WATCH_REASONS.items()]}


@router.post("/watchlist")
def watch_add(body: WatchBody, rt: Runtime = Depends(runtime)):
    sym = body.symbol.strip().upper()
    if not sym:
        raise HTTPException(400, "symbole requis")
    if sym not in rt.feed.instruments and sym not in rt.feed.quotes:
        raise HTTPException(400, f"instrument inconnu : {sym}")
    if body.reason_code:
        r = WATCH_REASONS.get(body.reason_code)
        if not r:
            raise HTTPException(400, f"raison inconnue : {body.reason_code}")
        source, reason = r["source"], r["label"]
    else:
        source, reason = "manual", body.reason
    with session_scope() as s:
        w = s.get(WatchItem, sym)
        if w:
            w.source, w.reason, w.expires_at = source, reason or w.reason, None
        else:
            s.add(WatchItem(symbol=sym, source=source, reason=reason))
    rt.orch.invalidate_watch()
    return {"ok": True, "symbol": sym}


@router.delete("/watchlist/{symbol}")
def watch_del(symbol: str, rt: Runtime = Depends(runtime)):
    with session_scope() as s:
        w = s.get(WatchItem, symbol.upper())
        if w:
            s.delete(w)
    rt.orch.invalidate_watch()
    return {"ok": True}


# ---------------------------------------------------------------- alertes (règles)
class RuleBody(BaseModel):
    rule: dict[str, Any]


@router.get("/alerts/rules")
def alert_rules():
    return {"items": AlertsAgent.list_rules()}


@router.post("/alerts/rules")
def alert_add(body: RuleBody):
    try:
        return AlertsAgent.add_rule(body.rule)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e


@router.patch("/alerts/rules/{rid}")
def alert_toggle(rid: int, active: bool):
    try:
        AlertsAgent.set_active(rid, active)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True}


@router.delete("/alerts/rules/{rid}")
def alert_delete(rid: int):
    AlertsAgent.delete_rule(rid)
    return {"ok": True}
