import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from app.agents.analyst import AnalystAgent
from app.agents.pelosi import PelosiAgent
from app.config import SecretStore
from app.models import DisclosureSeen
from app.db import session_scope
from tests.helpers import make_ctx, make_world

FIX = Path(__file__).resolve().parents[1] / "app" / "vendor" / "day_trader_skill" / "tests" / "fixtures"
TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 8, 30)


def sec_transport(calls):
    d = FIX / "trader-analyst"

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        u = str(req.url)
        if u.endswith("company_tickers.json"):
            return httpx.Response(200, content=(d / "company_tickers.json").read_bytes())
        for cik in ("0000021344", "0000320193"):
            if f"companyfacts/CIK{cik}" in u:
                return httpx.Response(200, content=(d / f"companyfacts-CIK{cik}.json").read_bytes())
            if f"submissions/CIK{cik}" in u:
                return httpx.Response(200, content=(d / f"submissions-CIK{cik}.json").read_bytes())
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp_path))
    feed, _ = make_world({"EURUSD": {"type": "forex", "price": 1.10},
                          "KO": {"type": "stock", "price": 70.0, "exchange": "NYSE", "profile": "us_equity"}}, today=TODAY, now_utc=NOW)
    return feed


async def test_analyst_equity_reads_sec_fixture(world, tmp_path, monkeypatch):
    calls = []
    store = SecretStore(tmp_path)
    store.set("sec_user_agent", "Test User test@example.org")
    monkeypatch.setattr("app.agents.analyst.SecretStore", lambda: store)
    ctx = make_ctx(world, NOW, tmp_path, http_transport=sec_transport(calls), agent_params={"use_llm": False})
    r = await AnalystAgent().run(ctx, symbol="KO")
    assert r.status == "ok" and r.output["mode"] == "sec", r.warnings
    assert r.output["rows"] and r.output["changes"] and "Coca" in r.output["entity"].title() or "COCA" in r.output["entity"]
    assert r.output["metrics"]["ticker"] == "KO"


async def test_analyst_without_sec_agent_is_partial_and_honest(world, tmp_path, monkeypatch):
    store = SecretStore(tmp_path)
    monkeypatch.setattr("app.agents.analyst.SecretStore", lambda: store)
    ctx = make_ctx(world, NOW, tmp_path, agent_params={"use_llm": False})
    r = await AnalystAgent().run(ctx, symbol="KO")
    assert r.status == "partial" and r.output["mode"] == "market_context" and any("SEC_USER_AGENT" in w for w in r.warnings)


async def test_analyst_forex_market_context(world, tmp_path):
    ctx = make_ctx(world, NOW, tmp_path, agent_params={"use_llm": False})
    r = await AnalystAgent().run(ctx, symbol="EURUSD")
    mc = r.output["market_context"]
    assert r.status == "partial" and mc["rsi_14"] is not None and mc["high_20d"] >= mc["low_20d"]
    assert any("classe d'actifs" in w for w in r.warnings)


async def test_pelosi_index_pdf_and_seen(world, tmp_path, monkeypatch):
    zbytes = (FIX / "trader-pelosi" / "2026FD.zip").read_bytes()
    txt = (FIX / "trader-pelosi" / "ptr-20035143.txt").read_text()

    def handler(req):
        if req.url.path.endswith("2026FD.zip"):
            return httpx.Response(200, content=zbytes)
        return httpx.Response(200, content=b"%PDF-fake")
    ctx = make_ctx(world, NOW, tmp_path, http_transport=httpx.MockTransport(handler), agent_params={"year": 2026, "first_run_days": 400, "notify": False})
    from app.agents.loader import load_skill
    ptr = load_skill("pelosi")
    monkeypatch.setattr(ptr, "pdf_to_text", lambda path: txt)
    r = await PelosiAgent().run(ctx)
    assert r.status == "ok" and len(r.output["filings"]) >= 2 and r.output["trades"]
    assert "ni un signal de trading" in r.output["disclaimer"]
    assert any(t["ticker"] == "BE" for t in r.output["trades"])
    # deuxième exécution : plus rien de nouveau (une alerte par dépôt)
    r2 = await PelosiAgent().run(ctx)
    assert not r2.output["filings"]
    with session_scope() as s:
        assert s.query(DisclosureSeen).count() >= 2


async def test_pelosi_index_unavailable(world, tmp_path):
    ctx = make_ctx(world, NOW, tmp_path)   # transport 503 par défaut
    r = await PelosiAgent().run(ctx)
    assert r.status == "error" and r.output["error"]
