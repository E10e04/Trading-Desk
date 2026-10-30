"""Tests EN DIRECT contre les vrais fournisseurs. Exclus par défaut : `pytest -m live`.
Ils échouent (et ne sont jamais présentés comme réussis) si le réseau ou la clé manque ; ils ne passent jamais par les doubles de test."""
import asyncio
import os

import pytest

from app.config import SecretStore
from app.llm.openrouter import OpenRouterClient
from app.market.biquote import BiquoteProvider

pytestmark = pytest.mark.live


async def test_biquote_catalogue_et_cotations():
    p = BiquoteProvider()
    try:
        inst = await p.list_instruments()
        assert len(inst) > 10, "le catalogue BiQuote doit contenir des instruments"
        got = await p.latest([i.symbol for i in inst[:5]])
        assert isinstance(got, dict)
    finally:
        await p.aclose()


async def test_biquote_flux_signalr_recoit_des_ticks():
    p = BiquoteProvider()
    ticks = []

    async def on_tick(q):
        ticks.append(q)

    try:
        inst = await p.list_instruments()
        await p.start_stream(on_tick)
        await p.subscribe([i.symbol for i in inst[:20]])
        for _ in range(60):
            if ticks:
                break
            await asyncio.sleep(1)
        assert p.stream_status()["state"] == "connected"
        assert ticks, "aucun tick reçu en 60 s (marchés fermés ou flux indisponible)"
    finally:
        await p.stop_stream()
        await p.aclose()


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY non défini")
async def test_openrouter_connexion_modele_par_defaut():
    c = OpenRouterClient(SecretStore())
    r = await c.test_connection()
    assert r["ok"], r.get("error")
    assert r["model"] == "minimax/minimax-m3.1-flash"
