"""Serveur de TEST pour les tests e2e de l'interface (Playwright).

⚠ Données SYNTHÉTIQUES uniquement (aléatoires, déterministes) : jamais utilisé en exploitation, jamais présenté comme du marché réel.
Aucun accès réseau. Base SQLite temporaire. Orchestrateur automatique désactivé (les tests lancent les agents à la demande).

    python -m tests.e2e_server --port 8799
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import random
import tempfile
from pathlib import Path

from contextlib import asynccontextmanager

import uvicorn

from tests.helpers import SynthProvider, make_world, real_now

PORT_DEFAULT = 8799
TOKEN = "e2e-token"
SYMBOLS = {
    "EURUSD": {"type": "forex", "price": 1.10},
    "GBPUSD": {"type": "forex", "price": 1.30},
    "USDJPY": {"type": "forex", "price": 150.0},
    "XAUUSD": {"type": "metal", "price": 2300.0},
    "BTCUSD": {"type": "crypto", "price": 64000.0},
}


async def _ticker(rt) -> None:
    from app.market.provider import Quote
    rng = random.Random(3)
    while True:
        await asyncio.sleep(0.8)
        for sym, q in list(rt.feed.quotes.items()):
            if sym not in SYMBOLS:
                continue
            mid = q.mid * (1 + rng.gauss(0, 0.00005))
            half = mid * 0.00001
            await rt.feed._on_tick(Quote(sym, mid - half, mid + half, mid, None, real_now(), "open"))


def build(tmp: str):
    os.environ["TRADING_DATA_DIR"] = tmp
    os.environ["API_TOKEN"] = TOKEN
    os.environ.pop("OPENROUTER_API_KEY", None)
    from app.config import reset_settings_cache
    reset_settings_cache()
    from app.llm.openrouter import OpenRouterClient
    from app.config import SecretStore
    from app.main import create_app
    from app.runtime import Runtime

    now = real_now()
    feed, prov = make_world(SYMBOLS, today=now.date(), now_utc=now, n_hist=25)
    rt = Runtime(provider=prov, llm=OpenRouterClient(SecretStore(Path(tmp))), autostart_feed=False, autostart_orchestrator=False)
    rt.feed.instruments.update(feed.instruments)
    rt.feed.quotes.update(feed.quotes)
    rt.feed.states.update(feed.states)
    app = create_app(rt)

    orig = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        task = asyncio.get_running_loop().create_task(_ticker(rt))
        try:
            async with orig(a):
                yield
        finally:
            task.cancel()

    app.router.lifespan_context = lifespan
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="trading-e2e-")
    uvicorn.run(build(tmp), host="127.0.0.1", port=a.port, log_level="warning")
