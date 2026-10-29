"""Fixtures communes. Les tests « simulés » n'utilisent JAMAIS le réseau (faux hub local, MockTransport)."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

# Environnement isolé AVANT tout import de l'application
_TMP = tempfile.mkdtemp(prefix="tradingdesk-test-")
os.environ["TRADING_DATA_DIR"] = _TMP
os.environ["TRADING_DB_URL"] = f"sqlite:///{_TMP}/test.sqlite"
os.environ["API_TOKEN"] = "test-token"
os.environ.pop("OPENROUTER_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route, WebSocketRoute  # noqa: E402
from starlette.websockets import WebSocket, WebSocketDisconnect  # noqa: E402

RS = "\x1e"


class FakeHub:
    """Faux hub SignalR (protocole JSON) pour tester le client et le fournisseur sans réseau."""

    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.connections = 0
        self.negotiate_calls = 0
        self.fail_negotiate = False
        self.drop_next = False
        self.unknown = {"BAD"}
        self.tick_interval = 0.05
        self.sockets: list[WebSocket] = []
        self.send_close_message = False

    def make_app(self) -> Starlette:
        async def negotiate(request):
            self.negotiate_calls += 1
            if self.fail_negotiate:
                return JSONResponse({"error": "nope"}, status_code=404)
            return JSONResponse({"connectionId": "cid", "connectionToken": "ctok", "negotiateVersion": 1,
                                 "availableTransports": [{"transport": "WebSockets", "transferFormats": ["Text", "Binary"]}]})

        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            self.connections += 1
            self.sockets.append(ws)
            first = (await ws.receive_text()).split(RS)[0]
            assert json.loads(first)["protocol"] == "json"
            await ws.send_text("{}" + RS)
            tick_task = None
            subs: set[str] = set()

            async def ticker():
                n = 0
                while True:
                    await asyncio.sleep(self.tick_interval)
                    for s in list(subs):
                        n += 1
                        tick = {"symbol": s, "bid": 1.1000 + n * 1e-5, "ask": 1.1002 + n * 1e-5, "mid": 1.1001 + n * 1e-5,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"}
                        await ws.send_text(json.dumps({"type": 1, "target": "ReceiveTick", "arguments": [tick]}) + RS)

            try:
                while True:
                    text = await ws.receive_text()
                    for frame in filter(None, text.split(RS)):
                        m = json.loads(frame)
                        if m.get("type") == 1 and m.get("target") == "Subscribe":
                            sym = m["arguments"][0]
                            if sym in self.unknown:
                                await ws.send_text(json.dumps({"type": 3, "invocationId": m["invocationId"], "error": "unknown symbol"}) + RS)
                                continue
                            self.subscribed.append(sym)
                            subs.add(sym)
                            await ws.send_text(json.dumps({"type": 3, "invocationId": m["invocationId"], "result": None}) + RS)
                            await ws.send_text(json.dumps({"type": 1, "target": "ReceiveSubscriptionState", "arguments": [sym, "open"]}) + RS)
                            if tick_task is None:
                                tick_task = asyncio.create_task(ticker())
                        if self.drop_next:
                            self.drop_next = False
                            await ws.close(code=1011)
                            return
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                if tick_task:
                    tick_task.cancel()

        return Starlette(routes=[Route("/hubs/tick/negotiate", negotiate, methods=["POST"]),
                                 WebSocketRoute("/hubs/tick", ws_endpoint)])


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def fake_hub():
    hub = FakeHub()
    port = _free_port()
    config = uvicorn.Config(hub.make_app(), host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    hub.url = f"http://127.0.0.1:{port}"
    yield hub
    server.should_exit = True
    th.join(timeout=5)


@pytest.fixture()
def db():
    from app import db as dbmod
    dbmod.init_engine(f"sqlite://")  # base en mémoire neuve
    from app.db import Base, get_engine
    yield dbmod
    Base.metadata.drop_all(get_engine())
