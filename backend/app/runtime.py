"""Conteneur d'exécution : assemble flux de marché, moteur papier, IA et orchestrateur."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from . import settings_store
from .bus import EventBus
from .config import SecretStore, get_settings
from .db import init_engine
from .llm.openrouter import OpenRouterClient
from .market.biquote import BiquoteProvider
from .market.feed import MarketFeed
from .market.provider import MarketDataProvider
from .orchestrator import Orchestrator
from .paper.engine import PaperEngine

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, provider: MarketDataProvider | None = None, *, llm: OpenRouterClient | None = None, http: httpx.AsyncClient | None = None,
                 autostart_feed: bool = True, autostart_orchestrator: bool = True, init_db: bool = True) -> None:
        if init_db:
            init_engine()
        s = get_settings()
        self.bus = EventBus()
        self.provider = provider or BiquoteProvider()
        self.feed = MarketFeed(self.provider, self.bus, live_s=float(settings_store.get("quote_live_s")),
                               hard_stale_s=float(settings_store.get("quote_hard_stale_s")))
        self.engine = PaperEngine(self.feed, self.bus)
        self.feed.add_listener(self.engine.on_quote)
        self.llm = llm or OpenRouterClient(SecretStore())
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=True)
        self.orch = Orchestrator(self.feed, self.engine, self.bus, self.llm, self.http)
        self.autostart_feed = autostart_feed
        self.autostart_orchestrator = autostart_orchestrator
        self._equity_task: asyncio.Task | None = None
        self.settings = s

    async def start(self) -> None:
        self.bus.attach_loop(asyncio.get_running_loop())
        self.engine.load_state()
        if self.autostart_feed:
            await self.feed.start()
        if self.autostart_orchestrator:
            await self.orch.start()
        self._equity_task = asyncio.create_task(self._equity_loop(), name="equity-snapshots")

    async def _equity_loop(self) -> None:
        n = 0
        while True:
            try:
                self.engine.snapshot_equity()
                n += 1
                if n % 60 == 0:
                    self.orch.expire_pending()
                if n % 3600 == 0:
                    self.orch.purge_old_runs(30)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("instantané d'equity en échec")
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        if self._equity_task:
            self._equity_task.cancel()
        await self.orch.stop()
        if self.autostart_feed:
            await self.feed.stop()
        await self.http.aclose()
        aclose = getattr(self.provider, "aclose", None)
        if aclose:
            await aclose()
