"""Modèles de persistance."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)  # UTC naïf, comme les skills


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    initial_capital: Mapped[float] = mapped_column(Float)
    balance: Mapped[float] = mapped_column(Float)  # capital + P&L réalisé - frais (style CFD)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    commission_bps: Mapped[float] = mapped_column(Float, default=1.0)  # par exécution
    slippage_bps: Mapped[float] = mapped_column(Float, default=0.5)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    risk: Mapped[dict] = mapped_column(JSON, default=dict)  # limites (voir risk_defaults)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    type: Mapped[str] = mapped_column(String(12))  # market | limit | stop | stop_limit
    qty: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    tif: Mapped[str] = mapped_column(String(8), default="gtc")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    # open | triggered | filled | cancelled | rejected | expired
    role: Mapped[str] = mapped_column(String(12), default="standalone")  # standalone|entry|stop_loss|take_profit
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # OCO
    source: Mapped[str] = mapped_column(String(40), default="manual")
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expire_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)  # prix d'exécution (devise de cotation)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)  # en devise de cotation, par unité
    fee: Mapped[float] = mapped_column(Float, default=0.0)  # en devise du compte
    fx_rate: Mapped[float] = mapped_column(Float, default=1.0)  # cotation -> compte
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)  # en devise du compte, net de frais
    quote_ts: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "symbol", name="uq_position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)  # entrée/stop/cible/flat-by du plan d'origine
    opened_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Ledger(Base):
    """Journal append-only : permet de reconstituer toutes les opérations."""
    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    portfolio_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    portfolio_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4), default="buy")
    setup: Mapped[str] = mapped_column(String(24), default="orb")
    kind: Mapped[str] = mapped_column(String(8), default="entry")  # entry | exit
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending | blocked | approved | executed | rejected | expired
    source_agent: Mapped[str] = mapped_column(String(24), default="entry")
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    sizing: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[dict] = mapped_column(JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # news / time machine / pelosi...
    order_ids: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(String(16), default="auto")  # auto | user
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentConfig(Base):
    __tablename__ = "agent_configs"
    agent_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = pas de planification


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(24), index=True)
    skill_version: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    instrument: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="running")  # running|ok|partial|skipped|error
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|schedule|event|pipeline
    correlation_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(16), nullable=True)  # live|stale|historical|simulated|none


Index("ix_agent_runs_agent_started", AgentRun.agent_id, AgentRun.started_at)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | success | warning | error
    source: Mapped[str] = mapped_column(String(24), default="system")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)


class AlertRule(Base):
    """Règles d'alerte (skill Alerts) : persistées, sonnent une seule fois."""
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule: Mapped[dict] = mapped_column(JSON)  # même forme que trader/alerts.json
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_rang: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    model: Mapped[str] = mapped_column(String(120))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WatchItem(Base):
    """Une ligne par AJOUT : un même instrument peut figurer plusieurs fois, avec des raisons différentes."""
    __tablename__ = "watchlist"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(24), default="manual")  # manual | scanner
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Strategy(Base):
    __tablename__ = "strategies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    portfolio_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class JournalNote(Base):
    __tablename__ = "journal_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)


class DisclosureSeen(Base):
    """Déclarations Pelosi déjà vues (une alerte par dépôt)."""
    __tablename__ = "disclosures_seen"
    doc_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class NewsSeen(Base):
    """Éléments d'actualité déjà montrés (skill News : « ne jamais répéter »)."""
    __tablename__ = "news_seen"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class EquityPoint(Base):
    __tablename__ = "equity_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    equity: Mapped[float] = mapped_column(Float)
    balance: Mapped[float] = mapped_column(Float)
    unrealized: Mapped[float] = mapped_column(Float, default=0.0)
