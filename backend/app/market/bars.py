"""Construction de bougies à partir des ticks (mid) et fusion avec l'historique REST."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque

from .provider import Bar, Quote

LIVE_TFS = {"1m": 60, "5m": 300}
MAXLEN = {"1m": 2000, "5m": 1200}


def bucket(ts: float, step: int) -> int:
    return int(ts // step * step)


class BarStore:
    """Bougies live par symbole et par unité de temps (1m, 5m). Volume = nombre de ticks (BiQuote n'a pas de volume)."""

    def __init__(self) -> None:
        self._closed: dict[tuple[str, str], deque[Bar]] = defaultdict(lambda: deque())
        self._open: dict[tuple[str, str], Bar] = {}

    def update(self, q: Quote) -> list[tuple[str, Bar]]:
        """Intègre un tick LIVE. Retourne les bougies qui viennent de se fermer [(tf, bar)]."""
        if q.age_at_recv_s > 60:  # cotation « dormante » (marché fermé / amorçage) : pas une bougie
            return []
        ts = q.ts_recv.replace(tzinfo=dt.timezone.utc).timestamp()
        closed_now: list[tuple[str, Bar]] = []
        for tf, step in LIVE_TFS.items():
            key = (q.symbol, tf)
            b0 = bucket(ts, step)
            cur = self._open.get(key)
            if cur is None:
                self._open[key] = Bar(ts=b0, o=q.mid, h=q.mid, l=q.mid, c=q.mid, v=1, closed=False)
            elif b0 == cur.ts:
                cur.h, cur.l, cur.c, cur.v = max(cur.h, q.mid), min(cur.l, q.mid), q.mid, cur.v + 1
            elif b0 > cur.ts:
                cur.closed = True
                dq = self._closed[key]
                dq.append(cur)
                while len(dq) > MAXLEN[tf]:
                    dq.popleft()
                closed_now.append((tf, cur))
                self._open[key] = Bar(ts=b0, o=q.mid, h=q.mid, l=q.mid, c=q.mid, v=1, closed=False)
            # b0 < cur.ts : tick en retard, ignoré
        return closed_now

    def live_bars(self, symbol: str, tf: str) -> list[Bar]:
        key = (symbol, tf)
        out = list(self._closed.get(key, ()))
        if key in self._open:
            out.append(self._open[key])
        return out

    def seed(self, symbol: str, tf: str, history: list[Bar]) -> None:
        """Ajoute l'historique REST *avant* la première bougie live (jamais par-dessus des bougies live)."""
        key = (symbol, tf)
        live = self._closed.get(key)
        first_live = live[0].ts if live else (self._open[key].ts if key in self._open else None)
        older = [b for b in history if first_live is None or b.ts < first_live]
        dq = deque(older[-MAXLEN[tf]:])
        if live:
            dq.extend(live)
        self._closed[key] = dq

    def has_seed(self, symbol: str, tf: str) -> bool:
        return (symbol, tf) in self._closed and len(self._closed[(symbol, tf)]) > 0

    def last_price(self, symbol: str) -> float | None:
        cur = self._open.get((symbol, "1m"))
        return cur.c if cur else None


def merge(history: list[Bar], live: list[Bar], limit: int) -> list[Bar]:
    """Historique REST + bougies live (les bougies live l'emportent sur un même horodatage)."""
    by_ts = {b.ts: b for b in history}
    for b in live:
        by_ts[b.ts] = b
    return [by_ts[k] for k in sorted(by_ts)][-limit:]


def aggregate(bars: list[Bar], step: int) -> list[Bar]:
    """Agrège des bougies fines en bougies plus larges (ex. 1m -> 5m)."""
    out: dict[int, Bar] = {}
    for b in bars:
        k = bucket(b.ts, step)
        a = out.get(k)
        if a is None:
            out[k] = Bar(ts=k, o=b.o, h=b.h, l=b.l, c=b.c, v=b.v, closed=b.closed)
        else:
            a.h, a.l, a.c, a.v = max(a.h, b.h), min(a.l, b.l), b.c, a.v + b.v
            a.closed = a.closed and b.closed
    return [out[k] for k in sorted(out)]
