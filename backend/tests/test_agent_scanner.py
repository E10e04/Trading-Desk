import datetime as dt

import pytest

from app import settings_store
from app.agents.scanner import ScannerAgent
from app.db import session_scope
from app.models import WatchItem
from tests.helpers import make_ctx, make_world

TODAY = dt.date(2026, 10, 21)  # mercredi
NOW = dt.datetime(2026, 10, 21, 7, 50)  # 7h50 UTC = 10h20 « New York » mappé (ancrage 07:00 UTC)


@pytest.fixture()
def world(db, tmp_path):
    settings_store.reset_cache()
    feed, prov = make_world({"EURUSD": {"type": "forex", "price": 1.10}, "XAUUSD": {"type": "metal", "price": 2400.0},
                             "GBPUSD": {"type": "forex", "price": 1.30}},
                            today=TODAY, now_utc=NOW, orb={"EURUSD": 3, "XAUUSD": 3})  # GBPUSD : séance du jour aléatoire
    return feed, prov, make_ctx(feed, NOW, tmp_path)


async def test_scanner_finds_orb_with_skill_logic_and_explains(world):
    feed, prov, ctx = world
    r = await ScannerAgent().run(ctx, symbols=["EURUSD", "XAUUSD", "GBPUSD"])
    assert r.status in ("ok", "partial")
    by = {h["symbol"]: h for h in r.output["hits"]}
    assert "EURUSD" in by and "orb" in by["EURUSD"]["setups"]
    o = by["EURUSD"]["orb"]
    assert o["price"] > o["or_high"] > o["or_low"] and o["pct_over"] > 0
    assert 1.0 < o["price"] < 1.2  # prix réels reconvertis du lot virtuel (pas de prix en milliers)
    assert any("cassure de la fourchette" in x for x in by["EURUSD"]["reasons"])
    assert "XAUUSD" in by and 2000 < by["XAUUSD"]["orb"]["price"] < 3000
    assert r.output["counts"]["orb"]["continuous"]["hits"] >= 2
    assert "recommandation" in r.summary
    # déclenché à la bougie 9:45 -> clôture 9:50 mappé = 07:20 UTC
    assert by["EURUSD"]["orb"]["closes_at"].startswith("2026-10-21T07:20")


async def test_scanner_writes_watchlist_and_replaces_old(world):
    feed, prov, ctx = world
    with session_scope() as s:
        s.add(WatchItem(symbol="OLD", source="scanner"))
        s.add(WatchItem(symbol="KEEP", source="manual"))
    await ScannerAgent().run(ctx, symbols=["EURUSD", "XAUUSD"])
    with session_scope() as s:
        syms = {w.symbol for w in s.query(WatchItem)}
    assert "OLD" not in syms and "KEEP" in syms and "EURUSD" in syms


async def test_scanner_skips_stale_wide_spread_and_reports_partial(world):
    feed, prov, ctx = world
    q = feed.quotes["GBPUSD"]
    q.age_at_recv_s = 3600  # cotation dormante
    from app.market.provider import Quote
    from tests.helpers import real_now
    feed.quotes["XAUUSD"] = Quote("XAUUSD", 2390.0, 2410.0, 2400.0, None, real_now(), "open")  # ~83 pb de spread
    prov.fail.add("EURUSD")
    r = await ScannerAgent().run(ctx, symbols=["EURUSD", "XAUUSD", "GBPUSD"])
    c = r.output["counts"]
    assert c["stale"] == 1 and c["wide_spread"] == 1
    assert r.status == "partial" and "EURUSD" in r.output["failures"]
    assert any("scan partiel" in w for w in r.warnings)


async def test_scanner_no_fresh_data_is_skipped_not_faked(world):
    feed, prov, ctx = world
    for q in feed.quotes.values():
        q.age_at_recv_s = 9999
    r = await ScannerAgent().run(ctx)
    assert r.status == "skipped" and r.output["counts"]["stale"] > 0


async def test_scanner_daily_setups_use_skill_find_setups(world):
    feed, prov, ctx = world
    from app.market.provider import Bar
    from tests.helpers import daily_from, ts_of
    # série journalière avec cassure nette du plus haut des 20 dernières clôtures
    base = [Bar(ts=ts_of(dt.datetime(2026, 9, 1) + dt.timedelta(days=i)), o=1.1, h=1.1, l=1.1, c=1.10 + 0.0001 * (i % 3), v=1) for i in range(45)]
    base.append(Bar(ts=ts_of(dt.datetime(2026, 10, 20)), o=1.1, h=1.2, l=1.1, c=1.20, v=1))
    prov.hist[("EURUSD", "1d")] = base
    r = await ScannerAgent().run(ctx, symbols=["EURUSD"])
    h = {x["symbol"]: x for x in r.output["hits"]}["EURUSD"]
    assert "breakout_20d" in h["setups"] and h["daily"]["close_date"] == "2026-10-20"


async def test_scanner_detecte_la_cassure_a_la_baisse_seulement_si_les_ventes_sont_autorisees(db, tmp_path):
    settings_store.reset_cache()
    feed, prov = make_world({"EURUSD": {"type": "forex", "price": 1.10, "down": True}, "XAUUSD": {"type": "metal", "price": 2400.0}},
                            today=TODAY, now_utc=NOW, orb={"EURUSD": 3, "XAUUSD": 3})
    ctx = make_ctx(feed, NOW, tmp_path)
    r = await ScannerAgent().run(ctx, symbols=["EURUSD", "XAUUSD"], write_watchlist=False)
    by = {h["symbol"]: h for h in r.output["hits"]}
    assert "EURUSD" not in by and "orb" in by["XAUUSD"]["setups"]          # défaut : achats seulement
    settings_store.update({"allow_short": True, "trade_direction": "both"})
    r = await ScannerAgent().run(ctx, symbols=["EURUSD", "XAUUSD"], write_watchlist=False)
    by = {h["symbol"]: h for h in r.output["hits"]}
    assert "orb_short" in by["EURUSD"]["setups"] and "orb" not in by["EURUSD"]["setups"] and "orb" in by["XAUUSD"]["setups"]
    s = by["EURUSD"]["orb_short"]
    assert s["price"] < s["or_low"] < s["or_high"] and 1.0 < s["price"] < 1.2
    assert any("à la baisse" in x for x in by["EURUSD"]["reasons"])


async def test_scanner_ignore_les_croisements_exotiques_par_defaut(db, tmp_path):
    settings_store.reset_cache()
    feed, prov = make_world({"EURUSD": {"type": "forex", "price": 1.10}, "CADMXN": {"type": "forex", "price": 14.0}},
                            today=TODAY, now_utc=NOW, orb={"EURUSD": 3, "CADMXN": 3})
    ctx = make_ctx(feed, NOW, tmp_path)
    r = await ScannerAgent().run(ctx, symbols=["EURUSD", "CADMXN"])
    assert "CADMXN" not in {h["symbol"] for h in r.output["hits"]}
    assert r.output["counts"]["exotic_skipped"] >= 1
    ctx.agent_params = {"forex_majors_only": False}
    r2 = await ScannerAgent().run(ctx, symbols=["EURUSD", "CADMXN"])
    assert r2.output["counts"]["exotic_skipped"] == 0
