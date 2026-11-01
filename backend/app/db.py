"""Accès base de données (SQLAlchemy 2.0, SQLite en mode WAL)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> Engine:
    """Crée (ou recrée, pour les tests) le moteur et les tables."""
    global _engine, _Session
    url = url or get_settings().db_url
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - dépend du dialecte
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    from . import models  # noqa: F401  (enregistre les tables)
    _migrate_watchlist(_engine)
    Base.metadata.create_all(_engine)
    return _engine


def _migrate_watchlist(engine: Engine) -> None:
    """Ancienne table : une seule ligne par instrument (clé = symbole). Nouvelle : une ligne par ajout (clé = id). Les données sont conservées."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "watchlist" not in insp.get_table_names() or any(c["name"] == "id" for c in insp.get_columns("watchlist")):
        return
    with engine.begin() as c:
        c.execute(text("ALTER TABLE watchlist RENAME TO watchlist_old"))
    Base.metadata.tables["watchlist"].create(engine)
    with engine.begin() as c:
        c.execute(text("INSERT INTO watchlist (symbol, added_at, source, reason, expires_at) "
                       "SELECT symbol, added_at, source, reason, expires_at FROM watchlist_old"))
        c.execute(text("DROP TABLE watchlist_old"))


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaction courte : commit à la sortie, rollback en cas d'erreur."""
    if _Session is None:
        init_engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI."""
    with session_scope() as s:
        yield s
