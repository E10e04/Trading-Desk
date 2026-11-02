"""Paper trading : portefeuilles, ordres, positions, exécutions, journal ; stratégies, journal personnel, notifications."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db import session_scope
from ..models import AgentRun, JournalNote, Notification, Portfolio, Strategy
from ..paper.engine import OrderRejected, OrderRequest
from ..runtime import Runtime
from .deps import Auth, runtime

router = APIRouter(prefix="/api", dependencies=[Auth])


class PortfolioBody(BaseModel):
    name: str
    capital: float = Field(gt=0)
    leverage: float = 1.0
    commission_bps: float = 1.0
    slippage_bps: float = 0.5
    risk: dict[str, Any] | None = None


@router.get("/portfolios")
def portfolios(rt: Runtime = Depends(runtime)):
    return {"items": rt.engine.list_portfolios()}


@router.post("/portfolios", status_code=201)
def portfolio_create(b: PortfolioBody, rt: Runtime = Depends(runtime)):
    try:
        return rt.engine.create_portfolio(b.name, b.capital, leverage=b.leverage, commission_bps=b.commission_bps, slippage_bps=b.slippage_bps, risk=b.risk)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/portfolios/{pid}")
def portfolio(pid: int, rt: Runtime = Depends(runtime)):
    try:
        return rt.engine.portfolio_summary(pid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.patch("/portfolios/{pid}")
def portfolio_update(pid: int, fields: dict[str, Any], rt: Runtime = Depends(runtime)):
    try:
        return rt.engine.update_portfolio(pid, **fields)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/portfolios/{pid}", status_code=204)
def portfolio_delete(pid: int, rt: Runtime = Depends(runtime)):
    try:
        rt.engine.delete_portfolio(pid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/portfolios/{pid}/reset")
def portfolio_reset(pid: int, rt: Runtime = Depends(runtime)):
    try:
        return rt.engine.reset_portfolio(pid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/portfolios/{pid}/equity")
def equity(pid: int, limit: int = Query(1000, le=10000), rt: Runtime = Depends(runtime)):
    return {"items": rt.engine.equity_curve(pid, limit)}


@router.get("/portfolios/{pid}/replay-check")
def replay(pid: int, rt: Runtime = Depends(runtime)):
    try:
        return rt.engine.replay_from_fills(pid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


class OrderBody(BaseModel):
    portfolio_id: int
    symbol: str
    side: str
    type: str = "market"
    qty: float
    limit_price: float | None = None
    stop_price: float | None = None
    tif: str = "gtc"
    stop_loss: float | None = None
    take_profit: float | None = None


@router.post("/orders", status_code=201)
def order_create(b: OrderBody, rt: Runtime = Depends(runtime)):
    """Ordre MANUEL de l'utilisateur (papier). Validé par le moteur : quantité, marge, exposition, vente à découvert interdite.
    Les ordres des agents passent, eux, par Risk puis Broker."""
    try:
        return rt.engine.place_order(OrderRequest(portfolio_id=b.portfolio_id, symbol=b.symbol, side=b.side, type=b.type, qty=b.qty, limit_price=b.limit_price,
                                                   stop_price=b.stop_price, tif=b.tif, stop_loss=b.stop_loss, take_profit=b.take_profit, source="manual"))
    except OrderRejected as e:
        raise HTTPException(422, {"reasons": e.reasons}) from e


@router.get("/orders")
def orders(portfolio_id: int | None = None, status: str | None = None, symbol: str | None = None, limit: int = Query(200, le=1000), rt: Runtime = Depends(runtime)):
    return {"items": rt.engine.list_orders(portfolio_id, status, limit, symbol)}


@router.delete("/orders/{oid}")
def order_cancel(oid: int, rt: Runtime = Depends(runtime)):
    try:
        return rt.engine.cancel_order(oid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/positions")
def positions(portfolio_id: int | None = None, rt: Runtime = Depends(runtime)):
    return {"items": rt.engine.list_positions(portfolio_id)}


@router.post("/positions/{pid}/{symbol}/close")
def position_close(pid: int, symbol: str, rt: Runtime = Depends(runtime)):
    try:
        return rt.engine.close_position(pid, symbol, source="manual")
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/fills")
def fills(portfolio_id: int | None = None, symbol: str | None = None, limit: int = Query(200, le=1000), rt: Runtime = Depends(runtime)):
    return {"items": rt.engine.list_fills(portfolio_id, limit, symbol)}


@router.get("/ledger")
def ledger(portfolio_id: int | None = None, limit: int = Query(300, le=2000), rt: Runtime = Depends(runtime)):
    return {"items": rt.engine.ledger(portfolio_id, limit)}


# ---------------------------------------------------------------- statistiques de performance (réelles, issues des exécutions)
@router.get("/portfolios/{pid}/stats")
def stats(pid: int, rt: Runtime = Depends(runtime)):
    fl = [f for f in rt.engine.list_fills(pid, 5000) if f["side"] == "sell"]
    wins = [f["realized_pnl"] for f in fl if f["realized_pnl"] > 0]
    losses = [f["realized_pnl"] for f in fl if f["realized_pnl"] < 0]
    n = len(fl)
    return {"closed_trades": n, "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / n, 4) if n else None,
            "gross_profit": round(sum(wins), 2), "gross_loss": round(sum(losses), 2),
            "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "note": "Calculé uniquement à partir des exécutions papier enregistrées. Aucune donnée fictive ; vide tant qu'aucun trade n'est clôturé."}


# ---------------------------------------------------------------- stratégies
class StrategyBody(BaseModel):
    name: str
    description: str = ""
    enabled: bool = False
    portfolio_id: int | None = None
    params: dict[str, Any] = {}


def _strat(s: Strategy) -> dict:
    return {"id": s.id, "name": s.name, "description": s.description, "enabled": s.enabled, "portfolio_id": s.portfolio_id, "params": s.params,
            "created_at": s.created_at.isoformat() + "Z"}


ALLOWED_STRATEGY_PARAMS = {"setup", "or_minutes", "stop", "target", "flat_by", "no_entries_before", "no_entries_after"}


@router.get("/strategies")
def strategies():
    with session_scope() as s:
        return {"items": [_strat(x) for x in s.scalars(select(Strategy).order_by(Strategy.id))], "allowed_params": sorted(ALLOWED_STRATEGY_PARAMS)}


@router.post("/strategies", status_code=201)
def strategy_create(b: StrategyBody):
    bad = set(b.params) - ALLOWED_STRATEGY_PARAMS
    if bad:
        raise HTTPException(400, f"paramètre(s) non supporté(s) : {', '.join(sorted(bad))}")
    with session_scope() as s:
        if b.portfolio_id is not None and not s.get(Portfolio, b.portfolio_id):
            raise HTTPException(400, "portefeuille introuvable")
        if s.scalar(select(Strategy).where(Strategy.name == b.name)):
            raise HTTPException(409, "une stratégie porte déjà ce nom")
        st = Strategy(name=b.name, description=b.description, enabled=b.enabled, portfolio_id=b.portfolio_id, params=b.params)
        s.add(st)
        s.flush()
        return _strat(st)


@router.patch("/strategies/{sid}")
def strategy_update(sid: int, fields: dict[str, Any]):
    with session_scope() as s:
        st = s.get(Strategy, sid)
        if not st:
            raise HTTPException(404, "stratégie introuvable")
        if "params" in fields:
            bad = set(fields["params"]) - ALLOWED_STRATEGY_PARAMS
            if bad:
                raise HTTPException(400, f"paramètre(s) non supporté(s) : {', '.join(sorted(bad))}")
        for k in ("name", "description", "enabled", "portfolio_id", "params"):
            if k in fields:
                setattr(st, k, fields[k])
        return _strat(st)


@router.delete("/strategies/{sid}")
def strategy_delete(sid: int):
    with session_scope() as s:
        st = s.get(Strategy, sid)
        if st:
            s.delete(st)
    return {"ok": True}


# ---------------------------------------------------------------- analyses & journal
class NoteBody(BaseModel):
    text: str
    symbol: str | None = None
    tags: list[str] = []


@router.get("/journal")
def journal(limit: int = Query(200, le=1000)):
    with session_scope() as s:
        return {"items": [{"id": n.id, "ts": n.ts.isoformat() + "Z", "text": n.text, "symbol": n.symbol, "tags": n.tags}
                          for n in s.scalars(select(JournalNote).order_by(JournalNote.id.desc()).limit(limit))]}


@router.post("/journal", status_code=201)
def journal_add(b: NoteBody):
    if not b.text.strip():
        raise HTTPException(400, "texte requis")
    with session_scope() as s:
        n = JournalNote(text=b.text.strip(), symbol=b.symbol.upper() if b.symbol else None, tags=b.tags)
        s.add(n)
        s.flush()
        return {"id": n.id}


@router.delete("/journal/{nid}")
def journal_delete(nid: int):
    with session_scope() as s:
        n = s.get(JournalNote, nid)
        if n:
            s.delete(n)
    return {"ok": True}


@router.get("/analyses")
def analyses(symbol: str = "", limit: int = Query(50, le=300)):
    """Résultats des agents d'analyse (Analyst, News, Pelosi, TimeMachine) — lecture de l'historique d'exécution."""
    with session_scope() as s:
        q = select(AgentRun).where(AgentRun.agent_id.in_(("analyst", "news", "pelosi", "timemachine")), AgentRun.status.in_(("ok", "partial"))).order_by(AgentRun.id.desc())
        if symbol:
            q = q.where(AgentRun.instrument == symbol.upper())
        return {"items": [{"id": r.id, "agent": r.agent_id, "instrument": r.instrument, "at": r.started_at.isoformat() + "Z", "status": r.status,
                           "summary": r.summary, "output": r.output, "warnings": r.warnings, "model": r.model, "data_quality": r.data_quality}
                          for r in s.scalars(q.limit(limit))]}


# ---------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications(unread: bool = False, level: str = "", limit: int = Query(100, le=500), offset: int = Query(0, ge=0)):
    """Pagination : `limit` par page (50 dans l'interface) et `offset` ; `total` = nombre de notifications correspondant aux filtres."""
    with session_scope() as s:
        q = select(Notification)
        if unread:
            q = q.where(Notification.read.is_(False))
        if level:
            q = q.where(Notification.level == level)
        total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = list(s.scalars(q.order_by(Notification.id.desc()).limit(limit).offset(offset)))
        unread_n = s.query(Notification).filter(Notification.read.is_(False)).count()
        return {"unread": unread_n, "total": total, "limit": limit, "offset": offset, "items": [{"id": n.id, "ts": n.ts.isoformat() + "Z", "level": n.level, "source": n.source, "title": n.title,
                                               "body": n.body, "symbol": n.symbol, "read": n.read} for n in rows]}


@router.post("/notifications/read")
def notifications_read(ids: list[int] | None = None):
    with session_scope() as s:
        q = select(Notification).where(Notification.read.is_(False))
        if ids:
            q = q.where(Notification.id.in_(ids))
        for n in s.scalars(q):
            n.read = True
    return {"ok": True}
