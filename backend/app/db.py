"""Accès base de données (SQLAlchemy 2.0, SQLite en mode WAL)."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


log = logging.getLogger(__name__)
_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None
recovery_report: dict | None = None  # renseigné si la base était corrompue au démarrage (lu par le runtime pour avertir l'utilisateur)


def _sqlite_path(url: str) -> str | None:
    if not url.startswith("sqlite:///") or ":memory:" in url or url in ("sqlite://", "sqlite:///"):
        return None
    return url[len("sqlite:///"):]


def _is_corrupt(e: Exception) -> bool:
    m = str(e).lower()
    return any(k in m for k in ("malformed", "not a database", "disk image", "corrupt"))


def _salvage(bak: str, path: str) -> dict:
    """Recopie dans une base neuve tout ce qui reste lisible de la base abîmée (table par table, ligne par ligne jusqu'à la première erreur)."""
    stats: dict = {"tables": {}, "lost": []}
    try:
        src = sqlite3.connect(f"file:{bak}?mode=ro", uri=True)
        objs = src.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
    except sqlite3.DatabaseError as e:
        stats["lost"].append(f"structure illisible : {e}")
        return stats
    dst = sqlite3.connect(path)
    try:
        for typ, name, sql in objs:
            if typ != "table":
                continue
            try:
                dst.execute(sql)
                cur = src.execute(f'SELECT * FROM "{name}"')
                n = 0
                while True:
                    try:
                        rows = cur.fetchmany(500)
                    except sqlite3.DatabaseError as e:
                        stats["lost"].append(f"{name} : lecture interrompue après {n} ligne(s) ({e})")
                        break
                    if not rows:
                        break
                    dst.executemany(f'INSERT OR IGNORE INTO "{name}" VALUES ({",".join("?" * len(rows[0]))})', rows)
                    n += len(rows)
                stats["tables"][name] = n
            except sqlite3.DatabaseError as e:
                stats["lost"].append(f"{name} : {e}")
        for typ, name, sql in objs:
            if typ == "index":
                try:
                    dst.execute(sql)
                except sqlite3.DatabaseError:
                    pass
        dst.commit()
    finally:
        dst.close()
        src.close()
    return stats


def _recover_if_corrupt(url: str) -> None:
    """Base SQLite illisible : le fichier abîmé est conservé (« .corrupt-DATE »), une base neuve est créée avec ce qui a pu être sauvé."""
    global recovery_report
    path = _sqlite_path(url)
    if not path or not os.path.exists(path):
        return
    try:
        con = sqlite3.connect(path, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            if os.path.getsize(path) < 1_500_000_000:  # vérification rapide de toutes les pages (quelques secondes au plus)
                res = con.execute("PRAGMA quick_check(1)").fetchone()
                if res and res[0] != "ok":
                    raise sqlite3.DatabaseError(f"database disk image is malformed ({res[0]})")
        finally:
            con.close()
        return
    except sqlite3.DatabaseError as e:
        if not _is_corrupt(e):
            raise
        err = str(e)
    bak = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(path + suf):
            os.replace(path + suf, bak + suf)
    stats = _salvage(bak, path)
    recovery_report = {"error": err, "backup": bak, **stats}
    log.error("base de données corrompue (%s) : sauvegarde %s, récupération %s", err, bak, stats)


def init_engine(url: str | None = None) -> Engine:
    """Crée (ou recrée, pour les tests) le moteur et les tables."""
    global _engine, _Session
    url = url or get_settings().db_url
    _recover_if_corrupt(url)
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
            cur.execute("PRAGMA synchronous=FULL")  # chaque validation est écrite sur le disque : une coupure brutale ne doit pas abîmer la base
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


def checkpoint() -> None:
    """Fusionne le journal WAL dans la base (appelé à l'arrêt propre). Sans effet sur une base en mémoire."""
    if _engine is None or not str(_engine.url).startswith("sqlite") or ":memory:" in str(_engine.url):
        return
    try:
        with _engine.connect() as c:
            c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001
        log.exception("checkpoint WAL en échec")


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
