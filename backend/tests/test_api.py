import httpx
import datetime as dt
import logging

import pytest
from fastapi.testclient import TestClient

from app import settings_store
from app.config import get_settings, reset_settings_cache
from app.llm.openrouter import OpenRouterClient
from app.main import create_app
from app.runtime import Runtime
from app.config import SecretStore
from app.market.provider import Quote
from tests.helpers import make_world, real_now

TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 8, 30)
FAKE_KEY = "sk-or-v1-TESTKEY0123456789abcdefTESTKEY"


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("API_TOKEN", "tok-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    reset_settings_cache()
    settings_store.reset_cache()
    feed, prov = make_world({"EURUSD": {"type": "forex", "price": 1.10}}, today=TODAY, now_utc=NOW, orb={"EURUSD": 3})
    rt = Runtime(provider=prov, llm=OpenRouterClient(SecretStore(tmp_path)), autostart_feed=False, autostart_orchestrator=False, init_db=False)
    rt.feed.instruments.update(feed.instruments)
    rt.feed.quotes.update(feed.quotes)
    rt.feed.states.update(feed.states)
    rt.orch.clock = lambda: NOW
    with TestClient(create_app(rt)) as c:
        c.headers.update({"Authorization": "Bearer tok-test"})
        c.rt = rt
        yield c
    reset_settings_cache()


def test_auth_required_and_public_health(client):
    assert client.get("/api/health", headers={"Authorization": ""}).json()["ok"]
    assert client.get("/api/agents", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/agents", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/session").status_code == 403           # pas un client loopback


def test_markets(client):
    r = client.get("/api/instruments?q=eur").json()
    assert r["total"] == 1 and r["items"][0]["symbol"] == "EURUSD" and r["items"][0]["quote"]["freshness"] == "live"
    assert client.get("/api/instruments/NOPE").status_code == 404
    assert client.get("/api/quotes?symbols=EURUSD").json()["items"][0]["symbol"] == "EURUSD"
    b = client.get("/api/bars/EURUSD?tf=5m&limit=50").json()
    assert len(b["bars"]) == 50 and b["freshness"]["status"] == "live"
    assert client.get("/api/bars/EURUSD?tf=7x").status_code == 400
    assert client.get("/api/markets/summary").json()["classes"]["forex"]["total"] >= 1


def test_agents_catalogue_and_config(client):
    items = client.get("/api/agents").json()["items"]
    assert [a["id"] for a in items] == ["scanner", "news", "entry", "exit", "timemachine", "budget", "risk", "broker", "alerts", "analyst", "pelosi"]
    assert all(a["mission"] and a["skill"] and a["limitations"] is not None and a["skill_version"] for a in items)
    r = client.put("/api/agents/entry/config", json={"params": {"or_minutes": 30}, "model": "minimax/minimax-m3.1-flash"}).json()
    assert r["effective_params"]["or_minutes"] == 30 and r["model"]
    assert client.put("/api/agents/entry/config", json={"params": {"nope": 1}}).status_code == 400
    assert client.post("/api/agents/entry/reset").json()["effective_params"]["or_minutes"] == 15
    assert "skill_md" in client.get("/api/agents/entry/skill").json()
    assert client.get("/api/agents/zzz").status_code == 404


def test_run_agent_and_history(client):
    r = client.post("/api/agents/entry/run", json={"inputs": {"symbol": "EURUSD"}}).json()
    assert r["output"]["status"] == "triggered"
    runs = client.get("/api/runs?agent=entry").json()["items"]
    assert runs and runs[0]["skill_version"].startswith("day-trader-skill")
    d = client.get(f"/api/runs/{runs[0]['id']}").json()
    assert d["input"]["symbol"] == "EURUSD" and d["output"]["status"] == "triggered"
    assert client.post("/api/agents/broker/run", json={"inputs": {}}).status_code == 400


def test_paper_trading_flow(client):
    pid = client.post("/api/portfolios", json={"name": "api", "capital": 10000, "leverage": 30}).json()["id"]
    assert client.post("/api/portfolios", json={"name": "api", "capital": 5}).status_code == 400
    assert client.post("/api/portfolios", json={"name": "x", "capital": -1}).status_code == 422
    o = client.post("/api/orders", json={"portfolio_id": pid, "symbol": "EURUSD", "side": "buy", "qty": 10000}).json()
    assert o["status"] == "filled"
    bad = client.post("/api/orders", json={"portfolio_id": pid, "symbol": "EURUSD", "side": "buy", "qty": 7})
    assert bad.status_code in (201, 422) and (bad.status_code == 422 or bad.json()["status"] == "rejected")
    pos = client.get("/api/positions").json()["items"]
    assert len(pos) == 1 and pos[0]["qty"] == 10000
    assert client.post(f"/api/positions/{pid}/EURUSD/close").json()["status"] == "filled"
    assert client.get(f"/api/portfolios/{pid}/replay-check").json()["consistent"]
    st = client.get(f"/api/portfolios/{pid}/stats").json()
    assert st["closed_trades"] == 1
    assert len(client.get(f"/api/orders?portfolio_id={pid}").json()["items"]) >= 2
    assert client.get("/api/ledger").json()["items"]
    assert client.get("/api/portfolios/999").status_code == 404
    client.post(f"/api/portfolios/{pid}/reset")
    assert client.get(f"/api/portfolios/{pid}").json()["balance"] == 10000


def test_pipeline_simulator_proposals(client):
    pid = client.post("/api/portfolios", json={"name": "sim", "capital": 10000, "leverage": 30}).json()["id"]
    client.put("/api/agents/risk/config", json={"params": {"max_signal_age_min": 240}})
    client.put("/api/agents/timemachine/config", json={"params": {"replays": 500}})
    e = client.post("/api/agents/entry/run", json={"inputs": {"symbol": "EURUSD"}}).json()["output"]
    px = e["trigger"]["price"]
    client.rt.feed.quotes["EURUSD"] = Quote("EURUSD", px * 0.99999, px * 1.00001, px, None, real_now(), "open")
    sim = client.post("/api/simulator/plan", json={"symbol": "EURUSD", "portfolio_id": pid, "entry": px, "stop": round(px * 0.998, 5), "target": round(px * 1.004, 5)}).json()
    assert sim["executed"] is False and sim["exit"]["status"] == "ok" and sim["budget"]["qty"] > 0 and sim["risk"]["verdict"] in ("PASS", "BLOCK")
    assert not client.get("/api/positions").json()["items"]            # la simulation n'exécute rien
    out = client.post("/api/pipeline/evaluate", json={"symbol": "EURUSD"}).json()
    assert out["triggered"] and out["proposals"][0]["status"] == "executed"
    props = client.get("/api/proposals").json()["items"]
    assert props[0]["risk"]["verdict"] == "PASS"
    assert client.get("/api/positions").json()["items"]
    assert client.post(f"/api/proposals/{props[0]['id']}/decide", json={"reject": True}).status_code == 409
    assert client.get("/api/pipeline/status").json()["counters"]["executed"] == 1


def test_watchlist_strategies_journal_notifications_alerts(client):
    assert client.post("/api/watchlist", json={"symbol": "eurusd", "reason": "test"}).json()["symbol"] == "EURUSD"
    assert client.get("/api/watchlist").json()["items"][0]["source"] == "manual"
    client.delete("/api/watchlist/EURUSD")
    assert client.get("/api/watchlist").json()["items"] == []
    assert client.post("/api/strategies", json={"name": "s", "params": {"bogus": 1}}).status_code == 400
    s = client.post("/api/strategies", json={"name": "ORB 30", "params": {"or_minutes": 30}}).json()
    assert client.patch(f"/api/strategies/{s['id']}", json={"enabled": True}).json()["enabled"]
    assert client.post("/api/journal", json={"text": "  "}).status_code == 400
    n = client.post("/api/journal", json={"text": "note", "symbol": "eurusd"}).json()
    assert client.get("/api/journal").json()["items"][0]["symbol"] == "EURUSD"
    client.delete(f"/api/journal/{n['id']}")
    r = client.post("/api/alerts/rules", json={"rule": {"symbol": "EURUSD", "type": "above", "price": 2.0}}).json()
    assert client.post("/api/alerts/rules", json={"rule": {"type": "bogus"}}).status_code == 400
    client.patch(f"/api/alerts/rules/{r['id']}?active=false")
    assert client.get("/api/alerts/rules").json()["items"][0]["active"] is False
    client.delete(f"/api/alerts/rules/{r['id']}")
    client.post("/api/agents/alerts/run", json={"inputs": {}})
    assert client.get("/api/notifications").json()["unread"] >= 0
    assert client.post("/api/notifications/read").json()["ok"]


def test_settings_and_llm_key_never_leaks(client, caplog):
    assert client.put("/api/settings", json={"nope": 1}).status_code == 400
    assert client.put("/api/settings", json={"auto_execute": False}).json()["values"]["auto_execute"] is False
    assert client.put("/api/settings", json={"max_quote_age_s": "x"}).status_code == 400
    assert client.put("/api/llm/key", json={"key": "not-a-key"}).status_code == 400
    with caplog.at_level(logging.DEBUG):
        r = client.put("/api/llm/key", json={"key": FAKE_KEY})
    assert r.json()["configured"] and FAKE_KEY not in r.text
    for path in ("/api/llm/status", "/api/settings", "/api/meta", "/api/diagnostics", "/api/agents", "/api/settings/secrets"):
        assert FAKE_KEY not in client.get(path).text, path
    assert FAKE_KEY not in caplog.text
    assert client.put("/api/settings/secrets", json={"sec_user_agent": "sans arobase"}).status_code == 400
    assert client.put("/api/settings/secrets", json={"sec_user_agent": "Jean Dupont jean@example.org"}).json()["sec_user_agent_configured"]
    assert client.delete("/api/llm/key").json()["configured"] is False


def test_llm_test_without_key_and_diagnostics(client):
    r = client.post("/api/llm/test", json={}).json()
    assert r["ok"] is False and "clé" in r["error"]
    d = client.get("/api/diagnostics").json()
    names = {c["name"]: c for c in d["checks"]}
    assert names["Base de données"]["ok"] is True and names["Skills vendorisés"]["ok"] is True
    assert names["IA (OpenRouter)"]["ok"] is None
    assert names["Cohérence du journal d'exécutions"]["ok"] is True
    assert client.get("/api/meta").json()["paper_only"] is True


def test_websocket_ticks_and_events(client):
    with client.websocket_connect("/ws?token=tok-test") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"op": "subscribe", "symbols": ["eurusd"]})
        snap = ws.receive_json()
        assert snap["type"] == "ticks" and snap["snapshot"] and snap["data"][0]["symbol"] == "EURUSD"
        ws.send_json({"op": "ping"})
        assert ws.receive_json()["type"] == "pong"
        pid = client.post("/api/portfolios", json={"name": "ws", "capital": 10000, "leverage": 30}).json()["id"]
        client.post("/api/orders", json={"portfolio_id": pid, "symbol": "EURUSD", "side": "buy", "qty": 1000})
        types = set()
        for _ in range(6):
            types.add(ws.receive_json()["type"])
            if "order" in types and "fill" in types:
                break
        assert {"order", "fill"} <= types
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=bad"):
            pass


def test_watchlist_origine_deduite_de_la_raison(client):
    reasons = {r["code"]: r for r in client.get("/api/watchlist/reasons").json()["items"]}
    assert reasons["news"]["source"] == "news" and reasons["idea"]["source"] == "manual"
    r = client.post("/api/watchlist", json={"symbol": "EURUSD", "reason_code": "news"})
    assert r.status_code == 200
    it = client.get("/api/watchlist").json()["items"][0]
    assert it["source"] == "news" and it["reason"] == reasons["news"]["label"]
    # l'origine ne peut pas être imposée séparément de la raison
    client.post("/api/watchlist", json={"symbol": "EURUSD", "reason_code": "idea", "reason": "n'importe quoi"})
    it = client.get("/api/watchlist").json()["items"][0]
    assert it["source"] == "manual" and it["reason"] == reasons["idea"]["label"]
    assert client.post("/api/watchlist", json={"symbol": "EURUSD", "reason_code": "inconnue"}).status_code == 400
    assert client.post("/api/watchlist", json={"symbol": "ZZZZZZ", "reason_code": "idea"}).status_code == 400


def test_update_endpoint_ne_plante_jamais_hors_ligne(client, monkeypatch):
    async def boom(**kw):
        raise httpx.ConnectError("pas de réseau")
    from app import updates
    monkeypatch.setattr(updates, "check", boom)
    j = client.get("/api/update/check").json()
    assert j["update_available"] is False and "vérification impossible" in j["error"]
