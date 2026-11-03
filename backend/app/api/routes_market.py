"""Marchés : catalogue, cotations, bougies, état du flux."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..market.provider import ProviderError
from ..runtime import Runtime
from .deps import Auth, runtime

router = APIRouter(prefix="/api", dependencies=[Auth])


@router.get("/history/coverage/{symbol}")
async def history_coverage(symbol: str, rt: Runtime = Depends(runtime)):
    """Historique 5 min enregistré localement pour un instrument, et avancement vers les 20 séances que demande le TimeMachine."""
    from ..market import barsdb
    sym = symbol.upper()
    if rt.feed.persist_bars:  # choisir l'instrument suffit à démarrer son enregistrement (historique REST sauvegardé au passage)
        try:
            await rt.feed.get_bars(sym, "5m", 1200)  # > 1000 : déclenche aussi le complément Yahoo (si activé)
        except Exception:  # noqa: BLE001  source indisponible : on montre ce qui est déjà enregistré
            pass
    cov = barsdb.coverage(sym)
    return {**cov, "full_sessions": barsdb.full_sessions(sym), "needed_sessions": 20, "persisting": rt.feed.persist_bars, "days": barsdb.daily(sym)}


@router.get("/instruments")
def instruments(q: str = "", asset_class: str = "", freshness: str = "", limit: int = Query(200, le=2000), offset: int = 0, rt: Runtime = Depends(runtime)):
    qq = q.strip().lower()
    rows = []
    for sym in sorted(set(rt.feed.instruments) | set(rt.feed.quotes)):
        inst = rt.feed.info(sym)
        if asset_class and inst.asset_class != asset_class:
            continue
        if qq and qq not in sym.lower() and qq not in (inst.description or "").lower():
            continue
        v = rt.feed.instrument_view(sym) or {**inst.to_dict(), "quote": rt.feed.quote_view(rt.feed.quotes[sym]) if sym in rt.feed.quotes else None}
        fr = (v.get("quote") or {}).get("freshness", "unknown")
        if freshness and fr != freshness:
            continue
        rows.append(v)
    return {"total": len(rows), "items": rows[offset:offset + limit]}


@router.get("/instruments/{symbol}")
def instrument(symbol: str, rt: Runtime = Depends(runtime)):
    symbol = symbol.upper()
    v = rt.feed.instrument_view(symbol)
    if v is None:
        if symbol not in rt.feed.quotes:
            raise HTTPException(404, f"instrument inconnu : {symbol}")
        v = {**rt.feed.info(symbol).to_dict(), "quote": rt.feed.quote_view(rt.feed.quotes[symbol])}
    v["positions"] = [p for p in rt.engine.list_positions() if p["symbol"] == symbol]
    v["freshness"] = rt.feed.freshness(symbol)
    return v


@router.get("/markets/summary")
def summary(rt: Runtime = Depends(runtime)):
    by_class: dict[str, dict] = {}
    for sym in set(rt.feed.instruments) | set(rt.feed.quotes):
        c = rt.feed.info(sym).asset_class
        d = by_class.setdefault(c, {"total": 0, "live": 0, "stale": 0, "closed": 0, "unknown": 0})
        d["total"] += 1
        d[rt.feed.freshness(sym)["status"]] += 1
    return {"classes": by_class, "health": rt.feed.health()}


@router.get("/quotes")
def quotes(symbols: str = "", rt: Runtime = Depends(runtime)):
    want = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    qs = [rt.feed.quotes[s] for s in (want or list(rt.feed.quotes)) if s in rt.feed.quotes]
    return {"items": [rt.feed.quote_view(q) for q in qs[:3000]]}


@router.get("/bars/{symbol}")
async def bars(symbol: str, tf: str = "5m", limit: int = Query(300, le=5000), rt: Runtime = Depends(runtime)):
    try:
        data = await rt.feed.get_bars(symbol.upper(), tf, limit)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"historique indisponible : {type(e).__name__}: {str(e)[:120]}") from e
    return {"symbol": symbol.upper(), "tf": tf, "freshness": rt.feed.freshness(symbol.upper()), "bars": [b.to_dict() for b in data]}


@router.get("/feed/status")
def feed_status(rt: Runtime = Depends(runtime)):
    return rt.feed.health()


@router.post("/feed/diagnose")
async def feed_diagnose(rt: Runtime = Depends(runtime)):
    """Teste le fournisseur étape par étape (REST, négociation, WebSocket, abonnement, réception)."""
    fn = getattr(rt.provider, "diagnose", None)
    if fn is None:
        return {"steps": [], "note": "ce fournisseur n'a pas de diagnostic"}
    return {"steps": await fn(), "status": rt.feed.health()["stream"]}
