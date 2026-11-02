import pytest

from app.market import barsdb
from app.market.feed import MarketFeed
from app.market.provider import Bar
from app.bus import EventBus


def bars(start, n):
    return [Bar(ts=start + i * 300, o=1, h=1.1, l=0.9, c=1.0, v=5, closed=True) for i in range(n)]


class Prov:
    """Source qui ne garde que les 100 dernières bougies (comme BiQuote : quelques jours)."""
    name = "t"

    def __init__(self):
        self.now = 10_000_000 // 300 * 300
        self.calls = 0

    async def history(self, symbol, tf, limit=500, **kw):
        self.calls += 1
        n = min(limit, 100)
        return bars(self.now - (n - 1) * 300, n)


async def test_historique_enregistre_complete_le_passe(db):
    prov = Prov()
    f = MarketFeed(prov, EventBus(), live_s=30, hard_stale_s=300)
    f.persist_bars = True
    first = await f._history("EURUSD", "5m", 500)
    assert len(first) == 100
    # le temps passe : la source a avancé de 60 bougies, les anciennes ont disparu chez elle mais pas chez nous
    prov.now += 60 * 300
    f._hist_cache.clear()
    second = await f._history("EURUSD", "5m", 500)
    assert len(second) == 160 and [b.ts for b in second] == sorted({b.ts for b in second})
    assert second[0].ts == first[0].ts and second[-1].ts == prov.now
    assert barsdb.coverage("EURUSD")["bars"] == 160


async def test_sans_persistance_rien_n_est_garde(db):
    prov = Prov()
    f = MarketFeed(prov, EventBus(), live_s=30, hard_stale_s=300)
    await f._history("EURUSD", "5m", 500)
    assert barsdb.coverage("EURUSD")["bars"] == 0


def test_bougie_live_partielle_ne_remplace_pas_la_bougie_rest(db):
    barsdb.save_bars("EURUSD", "5m", bars(1_000_200, 1), overwrite=True)
    live = [Bar(ts=1_000_200, o=2, h=2, l=2, c=2, v=1, closed=True)]
    barsdb.save_bars("EURUSD", "5m", live, overwrite=False)
    got = barsdb.load_older("EURUSD", "5m", 2_000_000, 10)
    assert len(got) == 1 and got[0].h == 1.1


def test_seances_completes_et_resume_par_jour(db):
    day = 20_000 * 86400  # un jour UTC
    full = [Bar(ts=day + 7 * 3600 + i * 300, o=1, h=2, l=0.5, c=1.5, v=1, closed=True) for i in range(78)]
    partial = [Bar(ts=day + 86400 + 7 * 3600 + i * 300, o=1, h=1, l=1, c=1, v=1, closed=True) for i in range(50)]
    barsdb.save_bars("GBPUSD", "5m", full + partial, overwrite=True)
    assert barsdb.full_sessions("GBPUSD") == 1
    d = barsdb.daily("GBPUSD")
    assert [x["bars"] for x in d] == [50, 78] and d[1]["high"] == 2 and d[1]["low"] == 0.5
