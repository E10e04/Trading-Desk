"""Moteur de paper trading (SEUL mode d'exécution de cette version).

Comptabilité de type CFD :
    solde (balance) = capital initial + P&L réalisé - frais
    equity          = solde + P&L latent (positions longues valorisées au bid, à découvert à l'ask)
    marge utilisée  = somme(|qté| x prix x taux USD / levier)
    marge libre     = equity - marge utilisée

Règles clés (déterministes, aucune IA) :
  * un ordre n'est « exécuté » que lorsqu'une cotation fraîche permet un remplissage (jamais « parce qu'il a été envoyé ») ;
  * achat au ask, vente au bid (spread estimé si bid/ask absents) + slippage + commission ;
  * stop : se déclenche puis s'exécute au marché => peut s'exécuter PIRE que le prix du stop (écart modélisé) ;
  * limite : exécutée au prix limite ou mieux ;
  * positions longues, et à découvert (quantité négative) si l'interrupteur global ET celui du portefeuille sont activés ;
    pas de levier au-delà de celui du portefeuille.
Chaque étape est écrite dans le journal append-only (`ledger`) : `replay_from_fills` reconstitue l'état.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from .. import settings_store
from ..bus import EventBus
from ..db import session_scope
from ..market.feed import MarketFeed
from ..market.instruments import spec_for
from ..market.provider import Quote
from ..models import EquityPoint, Fill, Ledger, Order, Portfolio, Position, Proposal, utcnow

log = logging.getLogger(__name__)
ORDER_TYPES = ("market", "limit", "stop", "stop_limit")
TIFS = ("day", "gtc")
EPS = 1e-9

RISK_DEFAULTS: dict[str, Any] = {
    # Limites passées au skill Risk (check.py) — noms identiques à `config.example.json` du dépôt source.
    "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_usd": 4000, "min_order_usd": 5,
    "max_loss_per_trade_usd": 50, "max_daily_loss_usd": 150, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True, "blocked": [], "pdt_guard": False,
    "gate_manual": False,  # appliquer aussi le skill Risk aux ordres saisis à la main
    "allow_short": False,  # vente à découvert : interrupteur du portefeuille (il faut aussi le réglage global « allow_short »)
    "max_exposure_pct": 300,  # exposition brute max / equity (règle backend supplémentaire)
}


def default_risk(capital: float, leverage: float) -> dict[str, Any]:
    """Limites par défaut proportionnelles au capital (modifiables par portefeuille)."""
    r = dict(RISK_DEFAULTS)
    r.update({"max_loss_per_trade_usd": round(capital * 0.005, 2), "max_daily_loss_usd": round(capital * 0.015, 2),
              "max_order_usd": round(capital * leverage * 0.75, 2), "max_trades_per_day": 3, "max_open_positions": 5,
              "max_spread_bps": 25.0, "auto_trade": True})
    return r


class OrderRejected(Exception):
    def __init__(self, reasons: list[str], order: dict | None = None):
        super().__init__("; ".join(reasons))
        self.reasons, self.order = reasons, order


@dataclass
class OrderRequest:
    portfolio_id: int
    symbol: str
    side: str
    type: str = "market"
    qty: float = 0.0
    limit_price: float | None = None
    stop_price: float | None = None
    tif: str = "gtc"
    stop_loss: float | None = None  # bracket
    take_profit: float | None = None
    source: str = "manual"
    proposal_id: int | None = None
    plan: dict | None = None
    expire_at: dt.datetime | None = None
    role: str = "standalone"
    meta: dict = field(default_factory=dict)


def _fin(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class PaperEngine:
    def __init__(self, feed: MarketFeed, bus: EventBus) -> None:
        self.feed, self.bus = feed, bus
        self._lock = threading.RLock()
        self._open: dict[str, set[int]] = {}
        self._pos_symbols: dict[str, set[int]] = {}
        self._last_mtm: dict[int, float] = {}
        self._last_equity_ts: dict[int, float] = {}
        self._warned: set[tuple[int, str]] = set()
        self.last_event_at: dt.datetime | None = None

    # ------------------------------------------------------------------ démarrage
    def load_state(self) -> None:
        with self._lock, session_scope() as s:
            self._open.clear()
            self._pos_symbols.clear()
            for o in s.scalars(select(Order).where(Order.status.in_(("open", "triggered")))):
                self._open.setdefault(o.symbol, set()).add(o.id)
            for p in s.scalars(select(Position).where(func.abs(Position.qty) > EPS)):
                self._pos_symbols.setdefault(p.symbol, set()).add(p.portfolio_id)

    # ------------------------------------------------------------------ portefeuilles
    def create_portfolio(self, name: str, capital: float, *, leverage: float = 1.0, commission_bps: float = 1.0,
                         slippage_bps: float = 0.5, risk: dict | None = None) -> dict:
        if not name.strip():
            raise ValueError("nom de portefeuille requis")
        if not (capital and capital > 0 and math.isfinite(capital)):
            raise ValueError("le capital initial doit être positif")
        if not 1 <= leverage <= 500:
            raise ValueError("levier entre 1 et 500")
        if commission_bps < 0 or slippage_bps < 0:
            raise ValueError("frais et slippage doivent être >= 0")
        r = default_risk(capital, leverage)
        r.update(risk or {})
        with self._lock, session_scope() as s:
            if s.scalar(select(Portfolio).where(Portfolio.name == name.strip())):
                raise ValueError(f"un portefeuille « {name} » existe déjà")
            p = Portfolio(name=name.strip(), initial_capital=capital, balance=capital, leverage=leverage,
                          commission_bps=commission_bps, slippage_bps=slippage_bps, risk=r)
            s.add(p)
            s.flush()
            self._ledger(s, p.id, "portfolio_created", p.id, {"capital": capital, "leverage": leverage})
            pid = p.id
        return self.portfolio_summary(pid)

    def update_portfolio(self, pid: int, **fields: Any) -> dict:
        allowed = {"name", "leverage", "commission_bps", "slippage_bps", "risk", "active"}
        with self._lock, session_scope() as s:
            p = s.get(Portfolio, pid)
            if not p:
                raise KeyError("portefeuille introuvable")
            for k, v in fields.items():
                if k not in allowed or v is None:
                    continue
                if k == "risk":
                    r = dict(RISK_DEFAULTS)
                    r.update(p.risk or {})
                    r.update(v)
                    v = r
                if k == "leverage" and not 1 <= float(v) <= 500:
                    raise ValueError("levier entre 1 et 500")
                setattr(p, k, v)
            self._ledger(s, pid, "portfolio_updated", pid, {k: v for k, v in fields.items() if k != "risk"})
        return self.portfolio_summary(pid)

    def reset_portfolio(self, pid: int) -> dict:
        with self._lock, session_scope() as s:
            p = s.get(Portfolio, pid)
            if not p:
                raise KeyError("portefeuille introuvable")
            for model in (Fill, Order, Position, EquityPoint):
                for row in s.scalars(select(model).where(model.portfolio_id == pid)):
                    s.delete(row)
            p.balance, p.realized_pnl, p.fees_paid = p.initial_capital, 0.0, 0.0
            self._ledger(s, pid, "portfolio_reset", pid, {"capital": p.initial_capital})
        self.load_state()
        return self.portfolio_summary(pid)

    def delete_portfolio(self, pid: int) -> None:
        """Supprime un portefeuille et tout ce qui lui est rattaché. Le dernier portefeuille ne peut pas être supprimé.
        Le journal (ledger) est conservé : c'est la trace d'audit."""
        with self._lock, session_scope() as s:
            p = s.get(Portfolio, pid)
            if not p:
                raise KeyError("portefeuille introuvable")
            if len(list(s.scalars(select(Portfolio.id)))) <= 1:
                raise ValueError("impossible de supprimer le dernier portefeuille")
            for model in (Fill, Order, Position, EquityPoint, Proposal):
                for row in s.scalars(select(model).where(model.portfolio_id == pid)):
                    s.delete(row)
            s.flush()
            name = p.name
            s.delete(p)
            self._ledger(s, pid, "portfolio_deleted", pid, {"name": name})
        self.load_state()

    # ------------------------------------------------------------------ valorisation
    def _est_bid_ask(self, q: Quote) -> tuple[float, float, bool]:
        """(bid, ask, estimé?) — spread estimé selon la classe d'actifs si le fournisseur n'en donne pas."""
        if q.bid is not None and q.ask is not None:
            return q.bid, q.ask, False
        half = q.mid * spec_for(self.feed.info(q.symbol).asset_class).default_spread_bps / 2e4
        return q.mid - half, q.mid + half, True

    def _fx(self, symbol: str) -> float | None:
        return self.feed.fx_to_usd(self.feed.info(symbol).quote_ccy)

    def portfolio_summary(self, pid: int) -> dict:
        with self._lock, session_scope() as s:
            p = s.get(Portfolio, pid)
            if not p:
                raise KeyError("portefeuille introuvable")
            positions = list(s.scalars(select(Position).where(Position.portfolio_id == pid, func.abs(Position.qty) > EPS)))
            n_open = len(list(s.scalars(select(Order.id).where(Order.portfolio_id == pid, Order.status.in_(("open", "triggered"))))))
            pos_views, unreal, margin, gross, incomplete = [], 0.0, 0.0, 0.0, False
            for pos in positions:
                v = self._position_view(pos, p.leverage)
                pos_views.append(v)
                if v["unrealized_pnl"] is None:
                    incomplete = True
                else:
                    unreal += v["unrealized_pnl"]
                if v["notional_usd"] is not None:
                    gross += v["notional_usd"]
                    margin += v["margin_usd"]
            equity = p.balance + unreal
            return {
                "id": p.id, "name": p.name, "currency": p.currency, "active": p.active,
                "initial_capital": p.initial_capital, "balance": round(p.balance, 2), "equity": round(equity, 2),
                "unrealized_pnl": round(unreal, 2), "realized_pnl": round(p.realized_pnl, 2), "fees_paid": round(p.fees_paid, 2),
                "margin_used": round(margin, 2), "free_margin": round(equity - margin, 2), "leverage": p.leverage,
                "exposure_usd": round(gross, 2), "exposure_pct": round(gross / equity * 100, 2) if equity > 0 else None,
                "commission_bps": p.commission_bps, "slippage_bps": p.slippage_bps, "risk": p.risk,
                "return_pct": round((equity / p.initial_capital - 1) * 100, 3),
                "positions": pos_views, "open_orders": n_open, "valuation_incomplete": incomplete,
                "created_at": p.created_at.isoformat() + "Z",
            }

    def _position_view(self, pos: Position, leverage: float) -> dict:
        q = self.feed.quotes.get(pos.symbol)
        fx = self._fx(pos.symbol)
        mark = None
        fresh = self.feed.freshness(pos.symbol)
        if q:
            bid, ask, _ = self._est_bid_ask(q)
            mark = ask if pos.qty < 0 else bid  # une position à découvert se rachète à l'ask
        unreal = (mark - pos.avg_price) * pos.qty * fx if (mark is not None and fx is not None) else None
        notional = abs(pos.qty) * mark * fx if (mark is not None and fx is not None) else None
        return {
            "id": pos.id, "portfolio_id": pos.portfolio_id, "symbol": pos.symbol, "qty": pos.qty, "avg_price": pos.avg_price,
            "mark": mark, "fx_to_usd": fx, "unrealized_pnl": round(unreal, 2) if unreal is not None else None,
            "unrealized_pct": round((mark / pos.avg_price - 1) * 100 * (-1 if pos.qty < 0 else 1), 3) if mark is not None and pos.avg_price else None,
            "side": "short" if pos.qty < 0 else "long",
            "notional_usd": round(notional, 2) if notional is not None else None,
            "margin_usd": round(notional / leverage, 2) if notional is not None else None,
            "realized_pnl": round(pos.realized_pnl, 2), "fees_paid": round(pos.fees_paid, 2), "plan": pos.plan or {},
            "opened_at": pos.opened_at.isoformat() + "Z" if pos.opened_at else None,
            "data_freshness": fresh["status"], "data_age_s": fresh["age_s"],
        }

    # ------------------------------------------------------------------ validation déterministe
    def _validate(self, s, p: Portfolio, req: OrderRequest, q: Quote | None) -> list[str]:
        why: list[str] = []
        sym, side, typ = req.symbol.upper(), req.side.lower(), req.type.lower()
        inst = self.feed.info(sym)
        if not p.active:
            why.append("portefeuille inactif")
        if side not in ("buy", "sell"):
            why.append(f"sens invalide « {req.side} » (buy ou sell)")
        if typ not in ORDER_TYPES:
            why.append(f"type d'ordre inconnu « {req.type} »")
        if req.tif not in TIFS:
            why.append(f"durée de validité inconnue « {req.tif} » (day ou gtc)")
        if sym not in self.feed.instruments and sym not in self.feed.quotes:
            why.append(f"instrument inconnu: {sym}")
        qty = _fin(req.qty)
        if qty is None or qty <= 0:
            why.append("la quantité doit être positive")
        else:
            step = spec_for(inst.asset_class).qty_step
            if qty + EPS < step or abs(qty / step - round(qty / step)) > 1e-6:
                why.append(f"quantité {qty} invalide : multiple de {step} requis pour {inst.asset_class}")
        for name, val, needed in (("limit_price", req.limit_price, typ in ("limit", "stop_limit")),
                                  ("stop_price", req.stop_price, typ in ("stop", "stop_limit"))):
            if needed and (_fin(val) is None or _fin(val) <= 0):
                why.append(f"{name} requis et positif pour un ordre {typ}")
            elif not needed and val is not None:
                why.append(f"{name} non applicable à un ordre {typ}")
        if why:
            return why
        pos = s.scalar(select(Position).where(Position.portfolio_id == p.id, Position.symbol == sym))
        held = pos.qty if pos else 0.0
        protective = req.role in ("stop_loss", "take_profit")
        reduces = (side == "sell" and held > EPS) or (side == "buy" and held < -EPS) or (protective and abs(held) > EPS)
        if protective and not ((side == "sell" and held > EPS) or (side == "buy" and held < -EPS)):
            why.append("protection sans position à protéger dans ce sens")
            return why
        if reduces:
            # on ne fait que réduire la position existante : jamais de retournement d'un coup
            if protective:
                if qty > abs(held) + EPS:
                    why.append(f"ordre protecteur de {qty} > position détenue {abs(held)}")
            else:
                pending = sum(o.qty - o.filled_qty for o in s.scalars(select(Order).where(
                    Order.portfolio_id == p.id, Order.symbol == sym, Order.side == side, Order.status.in_(("open", "triggered")),
                    Order.role == "standalone")))
                if qty > abs(held) - pending + EPS:
                    why.append(f"vente à découvert interdite : position {held}, déjà en ordres de vente {pending}, demandé {qty}" if side == "sell"
                               else f"rachat de {qty} > position à découvert {abs(held)} (déjà en ordres : {pending})")
            return why
        if side == "sell" and not self._short_allowed(p):
            why.append("vente à découvert interdite : activez-la dans Réglages (interrupteur global et portefeuille)")
            return why
        # ouverture ou renforcement (achat, ou vente à découvert autorisée) : contrôle de marge
        if req.role == "standalone" or req.role == "entry":
            if q is None:
                why.append(f"aucune cotation pour {sym}")
                return why
            fx = self._fx(sym)
            if fx is None:
                why.append(f"conversion {inst.quote_ccy or 'devise inconnue'} → USD indisponible : ordre refusé plutôt que de supposer un taux")
                return why
            ref = _fin(req.limit_price) or _fin(req.stop_price) or q.mid
            summary_free = self._free_margin(s, p)
            reserved = 0.0
            for o in s.scalars(select(Order).where(Order.portfolio_id == p.id, Order.status.in_(("open", "triggered")),
                                                   Order.role.in_(("standalone", "entry")))):
                op = s.scalar(select(Position).where(Position.portfolio_id == p.id, Position.symbol == o.symbol))
                oh = op.qty if op else 0.0
                if (o.side == "buy" and oh >= -EPS) or (o.side == "sell" and oh <= EPS):  # ordre qui ouvre ou renforce
                    reserved += self._margin_of(o.qty - o.filled_qty, o.limit_price or o.stop_price or ref, self._fx(o.symbol) or fx, p.leverage)
            need = self._margin_of(qty, ref, fx, p.leverage) + qty * ref * fx * p.commission_bps / 1e4
            if need > summary_free - reserved + 1e-6:
                why.append(f"marge insuffisante : requis {need:.2f} USD, disponible {max(summary_free - reserved, 0):.2f} USD")
            exp_cap = (p.risk or {}).get("max_exposure_pct")
            if exp_cap:
                gross = self._gross_exposure(s, p) + qty * ref * fx
                eq = p.balance + self._unrealized(s, p)
                if eq > 0 and gross / eq * 100 > float(exp_cap) + 1e-9:
                    why.append(f"exposition brute {gross / eq * 100:.0f}% > limite {exp_cap}% de l'equity")
        return why

    @staticmethod
    def _short_allowed(p: Portfolio) -> bool:
        """Vente à découvert : il faut l'interrupteur global (Réglages) ET celui du portefeuille."""
        return bool(settings_store.get("allow_short")) and bool((p.risk or {}).get("allow_short"))

    @staticmethod
    def _margin_of(qty: float, price: float, fx: float, leverage: float) -> float:
        return abs(qty) * price * fx / leverage

    def _unrealized(self, s, p: Portfolio) -> float:
        tot = 0.0
        for pos in s.scalars(select(Position).where(Position.portfolio_id == p.id, func.abs(Position.qty) > EPS)):
            v = self._position_view(pos, p.leverage)
            tot += v["unrealized_pnl"] or 0.0
        return tot

    def _gross_exposure(self, s, p: Portfolio) -> float:
        tot = 0.0
        for pos in s.scalars(select(Position).where(Position.portfolio_id == p.id, func.abs(Position.qty) > EPS)):
            v = self._position_view(pos, p.leverage)
            tot += v["notional_usd"] or (abs(pos.qty) * pos.avg_price * (self._fx(pos.symbol) or 1.0))
        return tot

    def _free_margin(self, s, p: Portfolio) -> float:
        used = 0.0
        for pos in s.scalars(select(Position).where(Position.portfolio_id == p.id, func.abs(Position.qty) > EPS)):
            v = self._position_view(pos, p.leverage)
            used += v["margin_usd"] if v["margin_usd"] is not None else abs(pos.qty) * pos.avg_price * (self._fx(pos.symbol) or 1.0) / p.leverage
        return p.balance + self._unrealized(s, p) - used

    # ------------------------------------------------------------------ passage d'ordres
    def place_order(self, req: OrderRequest) -> dict:
        """Valide, enregistre et — si possible — exécute. Un ordre refusé est conservé (audit) avec sa raison."""
        events: list[tuple[str, Any]] = []
        with self._lock, session_scope() as s:
            p = s.get(Portfolio, req.portfolio_id)
            if not p:
                raise OrderRejected(["portefeuille introuvable"])
            sym = req.symbol.upper()
            q = self.feed.quotes.get(sym)
            reasons = self._validate(s, p, req, q)
            digits = self.feed.info(sym).digits or 5
            expire = req.expire_at
            if req.tif == "day" and expire is None:
                now = utcnow()
                expire = dt.datetime(now.year, now.month, now.day) + dt.timedelta(days=1)
            o = Order(portfolio_id=p.id, symbol=sym, side=req.side.lower(), type=req.type.lower(), qty=_fin(req.qty) or 0.0,
                      limit_price=round(req.limit_price, digits) if _fin(req.limit_price) else None,
                      stop_price=round(req.stop_price, digits) if _fin(req.stop_price) else None, tif=req.tif, role=req.role,
                      source=req.source, proposal_id=req.proposal_id, expire_at=expire, status="open",
                      meta={**req.meta, "plan": req.plan or {}})
            if req.stop_loss or req.take_profit:
                o.meta = {**o.meta, "bracket": {"stop_loss": req.stop_loss, "take_profit": req.take_profit}}
            s.add(o)
            s.flush()
            if reasons:
                o.status, o.reject_reason = "rejected", "; ".join(reasons)
                self._ledger(s, p.id, "order_rejected", o.id, {"symbol": sym, "reasons": reasons, "source": req.source})
                events.append(("order", self._order_view(o)))
                view = self._order_view(o)
            else:
                self._ledger(s, p.id, "order_placed", o.id, {"symbol": sym, "side": o.side, "type": o.type, "qty": o.qty,
                                                              "limit": o.limit_price, "stop": o.stop_price, "source": req.source})
                if o.type == "market":
                    fresh = self.feed.freshness(sym)
                    max_age = float(settings_store.get("max_quote_age_s"))
                    state = self.feed.states.get(sym, q.market_state if q else "unknown")
                    if q is None or fresh["age_s"] is None or fresh["age_s"] > max_age or state == "closed":
                        why = "marché fermé" if state == "closed" else f"cotation périmée ({fresh['age_s']}s > {max_age:.0f}s)" if q else "aucune cotation"
                        o.status, o.reject_reason = "rejected", f"exécution impossible : {why}"
                        self._ledger(s, p.id, "order_rejected", o.id, {"symbol": sym, "reasons": [o.reject_reason]})
                    else:
                        self._fill_order(s, p, o, q, events)
                else:
                    self._open.setdefault(sym, set()).add(o.id)
                events.append(("order", self._order_view(o)))
                view = self._order_view(o)
        self._publish(events)
        return view

    def cancel_order(self, order_id: int, reason: str = "annulé par l'utilisateur") -> dict:
        events: list[tuple[str, Any]] = []
        with self._lock, session_scope() as s:
            o = s.get(Order, order_id)
            if not o:
                raise KeyError("ordre introuvable")
            if o.status not in ("open", "triggered"):
                raise ValueError(f"ordre déjà {o.status}")
            self._cancel(s, o, reason, events)
            view = self._order_view(o)
        self._publish(events)
        return view

    def close_position(self, pid: int, symbol: str, source: str = "manual") -> dict:
        symbol = symbol.upper()
        with self._lock, session_scope() as s:
            pos = s.scalar(select(Position).where(Position.portfolio_id == pid, Position.symbol == symbol, func.abs(Position.qty) > EPS))
            if not pos:
                raise KeyError(f"aucune position ouverte sur {symbol}")
            qty = abs(pos.qty)
            closing_side = "sell" if pos.qty > 0 else "buy"  # une position à découvert se clôture par un rachat
            # on libère les ordres de sortie en attente avant de clôturer
            events: list[tuple[str, Any]] = []
            for o in s.scalars(select(Order).where(Order.portfolio_id == pid, Order.symbol == symbol, Order.side == closing_side,
                                                   Order.status.in_(("open", "triggered")))):
                self._cancel(s, o, "position clôturée", events)
        self._publish(events)
        return self.place_order(OrderRequest(portfolio_id=pid, symbol=symbol, side=closing_side, type="market", qty=qty, source=source))

    # ------------------------------------------------------------------ cotations -> déclenchements
    async def on_quote(self, q: Quote) -> None:
        sym = q.symbol
        ids = self._open.get(sym)
        if ids:
            self._process_triggers(q, sorted(ids))
        pf = self._pos_symbols.get(sym)
        if pf:
            now = time.monotonic()
            for pid in list(pf):
                if now - self._last_mtm.get(pid, 0) >= 1.0:
                    self._last_mtm[pid] = now
                    self.bus.publish_nowait("portfolio", self.portfolio_summary(pid))

    def _process_triggers(self, q: Quote, ids: list[int]) -> None:
        events: list[tuple[str, Any]] = []
        with self._lock, session_scope() as s:
            bid, ask, _ = self._est_bid_ask(q)
            now = utcnow()
            for oid in ids:
                o = s.get(Order, oid)
                if o is None or o.status not in ("open", "triggered"):
                    self._open.get(q.symbol, set()).discard(oid)
                    continue
                if o.expire_at and now >= o.expire_at:
                    o.status = "expired"
                    self._open.get(q.symbol, set()).discard(oid)
                    self._ledger(s, o.portfolio_id, "order_expired", o.id, {})
                    events.append(("order", self._order_view(o)))
                    continue
                p = s.get(Portfolio, o.portfolio_id)
                if self.feed.freshness(q.symbol)["status"] == "closed":
                    continue  # pas de remplissage sur un marché fermé
                buy = o.side == "buy"
                kind = o.type
                trigger = False
                if kind == "limit" or (kind == "stop_limit" and o.status == "triggered"):
                    trigger = (ask <= (o.limit_price or 0)) if buy else (bid >= (o.limit_price or 1e18))
                elif kind == "stop":
                    trigger = (ask >= (o.stop_price or 1e18)) if buy else (bid <= (o.stop_price or 0))
                elif kind == "stop_limit":
                    hit = (ask >= (o.stop_price or 1e18)) if buy else (bid <= (o.stop_price or 0))
                    if hit:
                        o.status = "triggered"
                        self._ledger(s, p.id, "order_triggered", o.id, {"price": ask if buy else bid})
                        trigger = (ask <= (o.limit_price or 0)) if buy else (bid >= (o.limit_price or 1e18))
                if trigger:
                    self._fill_order(s, p, o, q, events)
                else:
                    events.append(("order", self._order_view(o))) if o.status == "triggered" else None
        self._publish(events)

    # ------------------------------------------------------------------ exécution
    def _fill_order(self, s, p: Portfolio, o: Order, q: Quote, events: list) -> None:
        bid, ask, est = self._est_bid_ask(q)
        buy = o.side == "buy"
        fx = self._fx(o.symbol)
        if fx is None:
            if (o.id, "fx") not in self._warned:
                self._warned.add((o.id, "fx"))
                log.warning("ordre %s: conversion devise indisponible, exécution différée", o.id)
            if o.type == "market":
                o.status, o.reject_reason = "rejected", "conversion de devise indisponible"
            return
        base = ask if buy else bid
        slip = base * p.slippage_bps / 1e4
        price = base + slip if buy else base - slip
        if o.type == "limit" or (o.type == "stop_limit"):
            price = min(price, o.limit_price) if buy else max(price, o.limit_price)  # limite : prix limite ou mieux
        digits = self.feed.info(o.symbol).digits or 5
        price = round(price, digits + 1)
        qty = o.qty - o.filled_qty
        fee = qty * price * fx * p.commission_bps / 1e4
        pos = s.scalar(select(Position).where(Position.portfolio_id == p.id, Position.symbol == o.symbol))
        realized = 0.0
        if pos is None:
            pos = Position(portfolio_id=p.id, symbol=o.symbol, qty=0.0, avg_price=0.0)
            s.add(pos)
            s.flush()
        old = pos.qty
        was_flat = abs(old) <= EPS
        opening = was_flat or (old > 0) == buy  # ouvre ou renforce (long si achat, à découvert si vente)
        if opening:
            new_qty = old + (qty if buy else -qty)
            pos.avg_price = (pos.avg_price * abs(old) + price * qty) / abs(new_qty)
            if was_flat:
                pos.opened_at = utcnow()
                pos.plan = o.meta.get("plan") or pos.plan or {}
            pos.qty = new_qty
        else:
            closed = min(qty, abs(old))
            realized = (price - pos.avg_price) * closed * fx * (1 if old > 0 else -1)
            pos.qty = old - closed if old > 0 else old + closed
            if abs(pos.qty) <= EPS:
                pos.qty, pos.avg_price, pos.opened_at, pos.plan = 0.0, 0.0, None, {}
        net = realized - fee
        pos.realized_pnl += net
        pos.fees_paid += fee
        p.balance += net
        p.realized_pnl += realized
        p.fees_paid += fee
        f = Fill(order_id=o.id, portfolio_id=p.id, symbol=o.symbol, side=o.side, qty=qty, price=price, bid=q.bid, ask=q.ask,
                 slippage=slip, fee=fee, fx_rate=fx, realized_pnl=net, quote_ts=q.ts_source or q.ts_recv)
        s.add(f)
        o.status, o.filled_qty, o.avg_fill_price = "filled", o.qty, price
        self._open.get(o.symbol, set()).discard(o.id)
        s.flush()
        self._ledger(s, p.id, "order_filled", o.id, {"symbol": o.symbol, "side": o.side, "qty": qty, "price": price, "fee": round(fee, 6),
                                                      "fx": fx, "bid": q.bid, "ask": q.ask, "spread_estimated": est,
                                                      "realized_net": round(net, 6)})
        flat = abs(pos.qty) <= EPS
        kind = "position_closed" if flat else ("position_opened" if was_flat else "position_changed")
        self._ledger(s, p.id, kind, pos.id, {"symbol": o.symbol, "qty": pos.qty, "avg_price": pos.avg_price})
        if not flat:
            self._pos_symbols.setdefault(o.symbol, set()).add(p.id)
        else:
            self._pos_symbols.get(o.symbol, set()).discard(p.id)
        events.append(("fill", self._fill_view(f)))
        events.append(("order", self._order_view(o)))
        # OCO : un côté exécuté annule l'autre ; position soldée => plus de protections orphelines
        if o.group_id:
            for sib in s.scalars(select(Order).where(Order.group_id == o.group_id, Order.id != o.id, Order.status.in_(("open", "triggered")))):
                self._cancel(s, sib, "OCO : l'autre jambe a été exécutée", events)
        if flat:
            for orphan in s.scalars(select(Order).where(Order.portfolio_id == p.id, Order.symbol == o.symbol,
                                                        Order.role.in_(("stop_loss", "take_profit")), Order.status.in_(("open", "triggered")))):
                self._cancel(s, orphan, "position soldée", events)
        # bracket : l'entrée exécutée crée ses jambes de protection
        br = (o.meta or {}).get("bracket")
        if opening and br and o.role in ("standalone", "entry"):
            gid = uuid.uuid4().hex[:16]
            for role, typ, key in (("stop_loss", "stop", "stop_loss"), ("take_profit", "limit", "take_profit")):
                px = br.get(key)
                if not px:
                    continue
                leg = Order(portfolio_id=p.id, symbol=o.symbol, side="sell" if buy else "buy", type=typ, qty=qty,
                            stop_price=round(px, digits) if typ == "stop" else None,
                            limit_price=round(px, digits) if typ == "limit" else None, tif="gtc", role=role,
                            parent_id=o.id, group_id=gid, source=o.source, proposal_id=o.proposal_id, status="open",
                            meta={"plan": o.meta.get("plan") or {}})
                s.add(leg)
                s.flush()
                self._open.setdefault(o.symbol, set()).add(leg.id)
                self._ledger(s, p.id, "order_placed", leg.id, {"symbol": o.symbol, "side": leg.side, "type": typ, "qty": qty, "role": role, "parent": o.id})
                events.append(("order", self._order_view(leg)))

    def _cancel(self, s, o: Order, reason: str, events: list) -> None:
        o.status, o.reject_reason = "cancelled", reason
        self._open.get(o.symbol, set()).discard(o.id)
        self._ledger(s, o.portfolio_id, "order_cancelled", o.id, {"reason": reason})
        events.append(("order", self._order_view(o)))

    # ------------------------------------------------------------------ journal / vues
    def _ledger(self, s, pid: int | None, kind: str, ref: int | None, payload: dict) -> None:
        s.add(Ledger(portfolio_id=pid, kind=kind, ref_id=ref, payload=payload))
        self.last_event_at = utcnow()

    def _publish(self, events: list[tuple[str, Any]]) -> None:
        for topic, payload in events:
            self.bus.publish_nowait(topic, payload)

    @staticmethod
    def _order_view(o: Order) -> dict:
        return {"id": o.id, "portfolio_id": o.portfolio_id, "symbol": o.symbol, "side": o.side, "type": o.type, "qty": o.qty,
                "limit_price": o.limit_price, "stop_price": o.stop_price, "tif": o.tif, "status": o.status, "role": o.role,
                "parent_id": o.parent_id, "group_id": o.group_id, "source": o.source, "proposal_id": o.proposal_id,
                "filled_qty": o.filled_qty, "avg_fill_price": o.avg_fill_price, "reject_reason": o.reject_reason,
                "created_at": o.created_at.isoformat() + "Z" if o.created_at else None,
                "updated_at": o.updated_at.isoformat() + "Z" if o.updated_at else None,
                "expire_at": o.expire_at.isoformat() + "Z" if o.expire_at else None}

    @staticmethod
    def _fill_view(f: Fill) -> dict:
        return {"id": f.id, "order_id": f.order_id, "portfolio_id": f.portfolio_id, "symbol": f.symbol, "side": f.side, "qty": f.qty,
                "price": f.price, "bid": f.bid, "ask": f.ask, "slippage": f.slippage, "fee": round(f.fee, 6), "fx_rate": f.fx_rate,
                "realized_pnl": round(f.realized_pnl, 4), "ts": f.ts.isoformat() + "Z"}

    # ------------------------------------------------------------------ lectures
    def list_orders(self, pid: int | None = None, status: str | None = None, limit: int = 200, symbol: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(Order).order_by(Order.id.desc()).limit(limit)
            if pid:
                stmt = stmt.where(Order.portfolio_id == pid)
            if symbol:
                stmt = stmt.where(Order.symbol == symbol.upper())
            if status == "active":
                stmt = stmt.where(Order.status.in_(("open", "triggered")))
            elif status:
                stmt = stmt.where(Order.status == status)
            return [self._order_view(o) for o in s.scalars(stmt)]

    def list_fills(self, pid: int | None = None, limit: int = 200, symbol: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(Fill).order_by(Fill.id.desc()).limit(limit)
            if symbol:
                stmt = stmt.where(Fill.symbol == symbol.upper())
            if pid:
                stmt = stmt.where(Fill.portfolio_id == pid)
            return [self._fill_view(f) for f in s.scalars(stmt)]

    def list_positions(self, pid: int | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(Position).where(func.abs(Position.qty) > EPS)
            if pid:
                stmt = stmt.where(Position.portfolio_id == pid)
            out = []
            for pos in s.scalars(stmt):
                p = s.get(Portfolio, pos.portfolio_id)
                out.append({**self._position_view(pos, p.leverage), "portfolio": p.name})
            return out

    def list_portfolios(self) -> list[dict]:
        with session_scope() as s:
            ids = list(s.scalars(select(Portfolio.id).order_by(Portfolio.id)))
        return [self.portfolio_summary(i) for i in ids]

    def ledger(self, pid: int | None = None, limit: int = 300) -> list[dict]:
        with session_scope() as s:
            stmt = select(Ledger).order_by(Ledger.id.desc()).limit(limit)
            if pid:
                stmt = stmt.where(Ledger.portfolio_id == pid)
            return [{"id": r.id, "ts": r.ts.isoformat() + "Z", "portfolio_id": r.portfolio_id, "kind": r.kind, "ref_id": r.ref_id,
                     "payload": r.payload} for r in s.scalars(stmt)]

    # ------------------------------------------------------------------ courbe d'equity
    def snapshot_equity(self, force: bool = False) -> None:
        every = float(settings_store.get("equity_snapshot_s"))
        now = time.monotonic()
        for summ in self.list_portfolios():
            pid = summ["id"]
            if not force and now - self._last_equity_ts.get(pid, 0) < every:
                continue
            self._last_equity_ts[pid] = now
            with session_scope() as s:
                s.add(EquityPoint(portfolio_id=pid, equity=summ["equity"], balance=summ["balance"], unrealized=summ["unrealized_pnl"]))

    def equity_curve(self, pid: int, limit: int = 1000) -> list[dict]:
        with session_scope() as s:
            rows = list(s.scalars(select(EquityPoint).where(EquityPoint.portfolio_id == pid).order_by(EquityPoint.id.desc()).limit(limit)))
        return [{"ts": r.ts.isoformat() + "Z", "equity": r.equity, "balance": r.balance, "unrealized": r.unrealized} for r in reversed(rows)]

    # ------------------------------------------------------------------ reconstitution
    def replay_from_fills(self, pid: int) -> dict:
        """Reconstitue solde et positions UNIQUEMENT à partir des exécutions et compare à l'état stocké."""
        with session_scope() as s:
            p = s.get(Portfolio, pid)
            if not p:
                raise KeyError("portefeuille introuvable")
            bal, qty, avg, fees, realized = p.initial_capital, {}, {}, 0.0, 0.0
            for f in s.scalars(select(Fill).where(Fill.portfolio_id == pid).order_by(Fill.id)):
                q0, a0 = qty.get(f.symbol, 0.0), avg.get(f.symbol, 0.0)
                is_buy = f.side == "buy"
                if abs(q0) <= EPS or (q0 > 0) == is_buy:
                    nq = q0 + (f.qty if is_buy else -f.qty)
                    avg[f.symbol] = (a0 * abs(q0) + f.price * f.qty) / abs(nq)
                    qty[f.symbol] = nq
                    gross = 0.0
                else:
                    closed = min(f.qty, abs(q0))
                    gross = (f.price - a0) * closed * f.fx_rate * (1 if q0 > 0 else -1)
                    qty[f.symbol] = q0 - closed if q0 > 0 else q0 + closed
                    if abs(qty[f.symbol]) <= EPS:
                        qty[f.symbol], avg[f.symbol] = 0.0, 0.0
                realized += gross
                fees += f.fee
                bal += gross - f.fee
            stored = {pos.symbol: pos.qty for pos in s.scalars(select(Position).where(Position.portfolio_id == pid)) if abs(pos.qty) > EPS}
            rebuilt = {k: v for k, v in qty.items() if abs(v) > EPS}
            ok = (abs(bal - p.balance) < 1e-6 and abs(fees - p.fees_paid) < 1e-6 and abs(realized - p.realized_pnl) < 1e-6
                  and stored.keys() == rebuilt.keys() and all(abs(stored[k] - rebuilt[k]) < 1e-9 for k in stored))
            return {"consistent": ok, "balance_rebuilt": round(bal, 6), "balance_stored": round(p.balance, 6),
                    "fees_rebuilt": round(fees, 6), "fees_stored": round(p.fees_paid, 6),
                    "positions_rebuilt": rebuilt, "positions_stored": stored}
