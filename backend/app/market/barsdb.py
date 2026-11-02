"""Enregistrement local des bougies 5 min (SQLite) pour constituer l'historique que BiQuote ne garde pas."""
from __future__ import annotations

import time

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert

from ..db import session_scope
from ..models import HistBar
from .provider import Bar

KEEP_DAYS = 35
CHUNK = 100


def save_bars(symbol: str, tf: str, bars: list[Bar], *, overwrite: bool) -> int:
    """Enregistre des bougies FERMÉES. overwrite=True : l'historique REST (complet) remplace une bougie live partielle."""
    rows = [{"symbol": symbol, "tf": tf, "ts": int(b.ts), "o": b.o, "h": b.h, "l": b.l, "c": b.c, "v": b.v}
            for b in bars if b.closed]
    if not rows:
        return 0
    with session_scope() as s:
        for i in range(0, len(rows), CHUNK):
            st = insert(HistBar).values(rows[i:i + CHUNK])
            if overwrite:
                st = st.on_conflict_do_update(index_elements=["symbol", "tf", "ts"],
                                              set_={k: st.excluded[k] for k in ("o", "h", "l", "c", "v")})
            else:
                st = st.on_conflict_do_nothing()
            s.execute(st)
    return len(rows)


def load_older(symbol: str, tf: str, before_ts: float, n: int) -> list[Bar]:
    """Jusqu'à n bougies enregistrées strictement avant before_ts, en ordre chronologique."""
    if n <= 0:
        return []
    with session_scope() as s:
        rows = s.execute(select(HistBar).where(HistBar.symbol == symbol, HistBar.tf == tf, HistBar.ts < before_ts)
                         .order_by(HistBar.ts.desc()).limit(n)).scalars().all()
        out = [Bar(ts=r.ts, o=r.o, h=r.h, l=r.l, c=r.c, v=r.v, closed=True) for r in rows]
    out.reverse()
    return out


def prune(days: int = KEEP_DAYS) -> int:
    with session_scope() as s:
        return s.execute(delete(HistBar).where(HistBar.ts < int(time.time()) - days * 86400)).rowcount or 0


def coverage(symbol: str, tf: str = "5m") -> dict:
    with session_scope() as s:
        n, lo, hi = s.execute(select(func.count(), func.min(HistBar.ts), func.max(HistBar.ts))
                              .where(HistBar.symbol == symbol, HistBar.tf == tf)).one()
    return {"symbol": symbol, "tf": tf, "bars": n or 0, "from": lo, "to": hi}
