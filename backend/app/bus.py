"""Bus d'événements en mémoire (asyncio) — relie feed, moteur, orchestrateur et WebSocket."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)
Handler = Callable[[str, Any], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """`topic` peut être "*" pour tout recevoir. Retourne une fonction de désinscription."""
        self._subs[topic].append(handler)

        def _unsub() -> None:
            try:
                self._subs[topic].remove(handler)
            except ValueError:
                pass

        return _unsub

    async def publish(self, topic: str, payload: Any = None) -> None:
        for h in list(self._subs.get(topic, ())) + list(self._subs.get("*", ())):
            try:
                r = h(topic, payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:  # un abonné défaillant ne doit jamais bloquer les autres
                log.exception("handler d'événement en échec (topic=%s)", topic)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Boucle principale, pour publier depuis un thread (endpoints FastAPI synchrones)."""
        self._loop = loop

    _loop: asyncio.AbstractEventLoop | None = None

    def publish_nowait(self, topic: str, payload: Any = None) -> None:
        """Depuis du code synchrone (thread ou boucle) : planifie la publication."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            running.create_task(self.publish(topic, payload))
        elif self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.publish(topic, payload), self._loop)
