"""WebSocket temps réel : cotations (par abonnement), bougies, ordres/exécutions, agents, propositions, notifications."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .deps import check_token

log = logging.getLogger(__name__)
router = APIRouter()
BROADCAST = {"order", "fill", "agent_run", "proposal", "notification", "feed_status"}
MAX_SUBS = 400


class Client:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.symbols: set[str] = set()
        self.ticks: dict[str, Any] = {}
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.dropped = 0

    def push(self, msg: dict) -> None:
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:  # client lent : on abandonne les événements anciens plutôt que de bloquer le système
            self.dropped += 1
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(msg)
            except Exception:  # noqa: BLE001
                pass


class Hub:
    def __init__(self, rt) -> None:
        self.rt = rt
        self.clients: set[Client] = set()
        rt.bus.subscribe("*", self._on_event)
        rt.ws_clients = 0

    def _on_event(self, topic: str, payload: Any) -> None:
        if not self.clients:
            return
        if topic == "tick":
            sym = payload.get("symbol")
            for c in self.clients:
                if sym in c.symbols:
                    c.ticks[sym] = payload
        elif topic == "bar_closed":
            sym = payload.get("symbol")
            for c in self.clients:
                if sym in c.symbols:
                    c.push({"type": topic, "data": payload})
        elif topic in BROADCAST:
            for c in self.clients:
                c.push({"type": topic, "data": payload})


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = "") -> None:
    if not check_token(token):
        await ws.close(code=4401)
        return
    rt = ws.app.state.rt
    hub: Hub = ws.app.state.hub
    await ws.accept()
    c = Client(ws)
    hub.clients.add(c)
    rt.ws_clients = len(hub.clients)
    await ws.send_json({"type": "hello", "data": {"feed": rt.feed.health(), "pipeline": rt.orch.status()}})

    async def sender() -> None:
        while True:
            await asyncio.sleep(0.25)
            if c.ticks:
                batch, c.ticks = list(c.ticks.values()), {}
                await ws.send_json({"type": "ticks", "data": batch})
            while not c.queue.empty():
                await ws.send_json(c.queue.get_nowait())

    task = asyncio.create_task(sender())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                await ws.send_json({"type": "error", "data": "message JSON invalide"})
                continue
            op = msg.get("op")
            if op == "subscribe":
                syms = {str(s).upper() for s in (msg.get("symbols") or [])}
                if len(syms) > MAX_SUBS:
                    await ws.send_json({"type": "error", "data": f"au plus {MAX_SUBS} instruments par connexion"})
                    continue
                c.symbols = syms
                snap = [rt.feed.quote_view(rt.feed.quotes[s]) for s in syms if s in rt.feed.quotes]
                await ws.send_json({"type": "ticks", "data": snap, "snapshot": True})
            elif op == "ping":
                await ws.send_json({"type": "pong"})
            else:
                await ws.send_json({"type": "error", "data": f"opération inconnue : {op}"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("ws fermé", exc_info=True)
    finally:
        task.cancel()
        hub.clients.discard(c)
        rt.ws_clients = len(hub.clients)
