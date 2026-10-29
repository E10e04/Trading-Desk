import json

import httpx
import pytest

from app import settings_store
from app.config import SecretStore, redact
from app.llm.openrouter import LlmError, LlmUnavailable, OpenRouterClient

KEY = "sk-or-v1-TESTKEY0123456789abcdef"


def make(db, tmp_path, handler, key=KEY):
    settings_store.reset_cache()
    store = SecretStore(tmp_path)
    if key:
        store.set("openrouter_api_key", key)
    return OpenRouterClient(store, transport=httpx.MockTransport(handler), base_url="https://or.test/api/v1")


def ok_handler(req: httpx.Request):
    if req.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "minimax/minimax-m3.1-flash", "name": "MiniMax M3.1 Flash", "context_length": 200000,
                                                   "pricing": {"prompt": "0.0000001", "completion": "0.0000004"}},
                                                  {"id": "x/free:free", "pricing": {"prompt": "0", "completion": "0"}}]})
    if req.url.path.endswith("/key"):
        assert req.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json={"data": {"label": "test", "limit": 5, "usage": 0.1}})
    if req.url.path.endswith("/chat/completions"):
        body = json.loads(req.content)
        assert body["model"] in ("minimax/minimax-m3.1-flash", "other/model")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3, "cost": 0.0001}})
    return httpx.Response(404)


async def test_models_connection_and_usage(db, tmp_path):
    c = make(db, tmp_path, ok_handler)
    models = await c.list_models()
    assert [m["id"] for m in models] == ["minimax/minimax-m3.1-flash", "x/free:free"] and models[1]["free"]
    assert abs(models[0]["prompt_per_mtok"] - 0.1) < 1e-9
    r = await c.test_connection()
    assert r["ok"] and r["model"] == "minimax/minimax-m3.1-flash" and r["credit_limit"] == 5
    out = await c.chat([{"role": "user", "content": "x"}], agent_id="news", model="other/model")
    assert out["text"] == "ok" and out["cost_usd"] == 0.0001
    u = c.usage_today()
    assert u["calls"] == 2 and u["tokens"] == 30 and u["errors"] == 0
    assert c.stats()["by_model"]


async def test_key_never_leaks(db, tmp_path):
    def handler(req):
        return httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}})
    c = make(db, tmp_path, handler)
    with pytest.raises(LlmError) as e:
        await c.chat([{"role": "user", "content": "x"}])
    assert KEY not in str(e.value) and "sk-or-" not in str(e.value)
    assert KEY not in json.dumps(c.stats()) and KEY not in json.dumps(c.key_status())
    assert c.key_status()["masked"].startswith("sk-or-") and KEY not in c.key_status()["masked"]
    assert redact(f"Authorization: Bearer {KEY}", [KEY]).count(KEY) == 0


async def test_unavailable_paths(db, tmp_path):
    c = make(db, tmp_path, ok_handler, key="")
    with pytest.raises(LlmUnavailable):
        await c.chat([{"role": "user", "content": "x"}])
    assert (await c.test_connection())["ok"] is False
    c2 = make(db, tmp_path, ok_handler)
    settings_store.update({"llm_enabled": False})
    with pytest.raises(LlmUnavailable, match="désactivée"):
        await c2.chat([{"role": "user", "content": "x"}])
    settings_store.update({"llm_enabled": True, "llm_daily_token_limit": 20})
    await c2.chat([{"role": "user", "content": "x"}])  # 15 jetons
    await c2.chat([{"role": "user", "content": "x"}])  # 30 jetons au total, limite dépassée après
    with pytest.raises(LlmUnavailable, match="limite"):
        await c2.chat([{"role": "user", "content": "x"}])
    settings_store.update({"llm_daily_token_limit": 300000})


async def test_errors_timeouts_and_bad_responses_are_recorded(db, tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("t", request=req)
        if calls["n"] == 2:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
        return httpx.Response(200, text="<html>")
    c = make(db, tmp_path, handler)
    for expected in ("délai", "vide", "non JSON"):
        with pytest.raises(LlmError, match=expected):
            await c.chat([{"role": "user", "content": "x"}])
    assert c.usage_today()["errors"] == 3


def test_set_key_validation(db, tmp_path):
    c = make(db, tmp_path, ok_handler, key="")
    with pytest.raises(ValueError):
        c.set_key("pas-une-cle")
    c.set_key(KEY)
    assert c.key_status()["configured"] and c.key_status()["source"] == "interface"
    c.set_key("")
    assert not c.key_status()["configured"]
