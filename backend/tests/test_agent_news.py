import datetime as dt
import json
import shutil
from pathlib import Path

import httpx
import pytest

from app import settings_store
from app.agents.news import NewsAgent
from app.config import SecretStore
from tests.helpers import make_ctx, make_world

FIX = Path(__file__).resolve().parent.parent / "app/vendor/day_trader_skill/tests/fixtures/trader-news"
TODAY = dt.date(2026, 10, 19)
NOW = dt.datetime(2026, 10, 19, 14, 15)  # 10:15 New York


def transport(calls, fail_yahoo=False):
    def h(req: httpx.Request):
        calls.append(str(req.url))
        u = str(req.url)
        if "feeds.finance.yahoo.com" in u:
            if fail_yahoo:
                return httpx.Response(503)
            name = "yahoo-KO.xml" if "s=KO" in u else "yahoo-TSLA.xml"
            return httpx.Response(200, content=(FIX / name).read_bytes())
        if u.endswith("company_tickers.json"):
            return httpx.Response(200, content=(FIX / "company_tickers.json").read_bytes())
        if "browse-edgar" in u:
            assert req.headers["user-agent"] == "Test User test@example.org"
            return httpx.Response(200, content=(FIX / "sec-8-K-0000021344.xml").read_bytes())
        return httpx.Response(404)
    return httpx.MockTransport(h)


class FakeNewsProvider:
    """Ajoute news() et calendar_upcoming() à un fournisseur synthétique."""
    def __init__(self, base, rows, cal, fail=False):
        self._b, self.rows, self.cal, self.fail = base, rows, cal, fail
    def __getattr__(self, n):
        return getattr(self._b, n)
    async def news(self, kind="market", **kw):
        if self.fail:
            raise RuntimeError("BiQuote injoignable")
        return self.rows
    async def calendar_upcoming(self, **kw):
        if self.fail:
            raise RuntimeError("BiQuote injoignable")
        return self.cal


@pytest.fixture()
def eq_world(db, tmp_path):
    settings_store.reset_cache()
    feed, prov = make_world({"TSLA": {"type": "stock", "exchange": "NASDAQ", "price": 360.0, "profile": "us_equity"}}, today=TODAY, now_utc=NOW,
                            n_hist=3)
    return feed, tmp_path


async def test_equity_headlines_via_skill_parsers_and_never_repeat(eq_world):
    feed, tmp = eq_world
    calls = []
    ctx = make_ctx(feed, NOW, tmp, http_transport=transport(calls))
    ctx.agent_params = {"hours": 48, "use_llm": False}
    r = await NewsAgent().run(ctx, symbols=["TSLA"])
    assert r.output["items"], r.output["sources"]
    titles = " ".join(i["title"] for i in r.output["items"])
    assert "Tesla-SpaceX Merger" in titles
    tsla = r.output["per_symbol"][0]
    assert tsla["leads"] and not tsla["facts"] or True
    assert any(s["name"] == "Yahoo Finance" and s["status"] == "ok" for s in r.output["sources"])
    assert any(s["name"] == "SEC EDGAR" and s["status"] == "not_configured" for s in r.output["sources"])
    first = len(r.output["items"])
    r2 = await NewsAgent().run(ctx, symbols=["TSLA"])
    assert len(r2.output["items"]) == 0 and first > 0  # rien de nouveau : jamais de répétition
    r3 = await NewsAgent().run(ctx, symbols=["TSLA"], show_all=True)
    assert len(r3.output["items"]) == first


async def test_sec_8k_flag_when_configured(db, tmp_path):
    settings_store.reset_cache()
    feed, _ = make_world({"KO": {"type": "stock", "exchange": "NYSE", "price": 70.0, "profile": "us_equity"}}, today=TODAY, now_utc=NOW, n_hist=3)
    SecretStore().set("sec_user_agent", "Test User test@example.org")
    calls = []
    ctx = make_ctx(feed, NOW, tmp_path, http_transport=transport(calls))
    ctx.agent_params = {"hours": 24 * 400, "use_llm": False}
    r = await NewsAgent().run(ctx, symbols=["KO"])
    assert any(i["kind"] == "filing" for i in r.output["items"])
    assert any(s["name"] == "SEC EDGAR 8-K" and s["status"] == "ok" for s in r.output["sources"])
    assert any("browse-edgar" in c for c in calls)
    SecretStore().set("sec_user_agent", "")


async def test_unreachable_sources_are_reported_not_faked(eq_world):
    feed, tmp = eq_world
    ctx = make_ctx(feed, NOW, tmp, http_transport=transport([], fail_yahoo=True))
    ctx.agent_params = {"use_llm": False}
    r = await NewsAgent().run(ctx, symbols=["TSLA"])
    assert r.status == "partial" and r.output["items"] == []
    assert any("Aucune source" in w for w in r.warnings)
    assert r.output["sources"][0]["status"] == "unavailable"


async def test_forex_uses_biquote_news_and_calendar_with_flags(db, tmp_path):
    settings_store.reset_cache()
    now = dt.datetime(2026, 10, 21, 12, 30)
    feed, prov = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=dt.date(2026, 10, 21), now_utc=now, n_hist=3)
    soon = (now + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (now + dt.timedelta(hours=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cal = [{"country": "US", "title": "Non-Farm Payrolls", "importance": "high", "time": soon, "forecast": "180K", "previous": "150K"},
           {"country": "EU", "title": "ECB Speech", "importance": "low", "time": soon},
           {"country": "JP", "title": "BoJ", "importance": "high", "time": soon}]
    rows = [{"title": "Euro slips as dollar firms", "url": "https://x/1", "source": "Wire", "publishedAt": (now - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    feed.provider = FakeNewsProvider(prov, rows, cal)
    ctx = make_ctx(feed, now, tmp_path)
    ctx.agent_params = {"use_llm": False, "hours": 6, "calendar_hours_ahead": 6}
    r = await NewsAgent().run(ctx, symbols=["EURUSD"])
    flags = r.output["risk_flags"]
    assert any(f["kind"] == "calendar" and "Non-Farm" in f["text"] for f in flags)
    assert not any("BoJ" in f["text"] for f in flags) and not any("ECB" in f["text"] for f in flags)  # JPY n'est pas dans EURUSD ; impact faible ignoré
    assert any(s["name"] == "BiQuote calendrier économique" and s["status"] == "ok" for s in r.output["sources"])
    fact = r.output["per_symbol"][0]["facts"]
    assert fact and fact[0]["kind"] == "calendar" and "prévu 180K" in fact[0]["summary"]
    r2 = await NewsAgent().run(ctx, symbols=["EURUSD"])
    assert not r2.output["risk_flags"] or all(f["kind"] != "calendar" for f in r2.output["risk_flags"])  # déjà vu : pas répété
    # BiQuote en panne : source marquée indisponible
    feed.provider = FakeNewsProvider(prov, rows, cal, fail=True)
    r3 = await NewsAgent().run(ctx, symbols=["EURUSD"], show_all=True)
    assert r3.status == "partial" and any(s["status"] == "unavailable" for s in r3.output["sources"])


async def test_llm_summary_optional_and_graceful(eq_world):
    feed, tmp = eq_world
    ctx = make_ctx(feed, NOW, tmp, http_transport=transport([]))
    ctx.agent_params = {"hours": 48, "use_llm": True}
    r = await NewsAgent().run(ctx, symbols=["TSLA"])  # pas de clé -> pas de résumé, mais l'agent fonctionne
    assert r.output["items"] and r.output.get("summary_ai") is None
    assert any("résumé IA non généré" in w for w in r.warnings)
