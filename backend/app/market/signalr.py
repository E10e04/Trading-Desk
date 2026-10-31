"""Client SignalR minimal (protocole JSON, transport WebSocket).

Implémenté directement (négociation HTTP → WebSocket → handshake → invocations) afin de rester
testable hors ligne avec un faux hub et sans dépendance exotique.

Spécification : https://github.com/dotnet/aspnetcore/blob/main/src/SignalR/docs/specs/HubProtocol.md
Types de messages : 1 Invocation, 2 StreamItem, 3 Completion, 6 Ping, 7 Close.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

log = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    """Certificats Mozilla (certifi) : l'application embarquée (PyInstaller, surtout sous macOS) n'a pas toujours les
    certificats du système, ce qui faisait échouer le WebSocket sécurisé alors que le REST (httpx + certifi) fonctionnait."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover
        return ssl.create_default_context()


RS = "\x1e"  # séparateur d'enregistrement SignalR
Handler = Callable[..., Awaitable[None] | None]


class SignalRError(RuntimeError):
    pass


class SignalRClient:
    def __init__(self, base_url: str, hub_path: str, *, headers: dict[str, str] | None = None,
                 ping_interval: float = 15.0, server_timeout: float = 45.0, handshake_timeout: float = 10.0,
                 negotiate_timeout: float = 10.0, skip_negotiation: bool = False,
                 http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.hub_path = hub_path if hub_path.startswith("/") else "/" + hub_path
        self.headers = dict(headers or {})
        self.ping_interval = ping_interval
        self.server_timeout = server_timeout
        self.handshake_timeout = handshake_timeout
        self.negotiate_timeout = negotiate_timeout
        self.skip_negotiation = skip_negotiation
        self._http_transport = http_transport
        self._handlers: dict[str, list[Handler]] = {}
        self._ws: Any = None
        self._tasks: list[asyncio.Task] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._inv = 0
        self._closed = asyncio.Event()
        self._closed.set()
        self.state = "disconnected"
        self.last_rx = 0.0  # monotonic
        self.close_reason: str | None = None
        self.negotiated: dict[str, Any] = {}

    # ------------------------------------------------------------------ handlers
    def on(self, target: str, handler: Handler) -> None:
        self._handlers.setdefault(target.lower(), []).append(handler)

    # ------------------------------------------------------------------ connexion
    async def connect(self) -> None:
        if self.state == "connected":
            return
        self.state = "connecting"
        self.close_reason = None
        try:
            token = None
            if not self.skip_negotiation:
                try:
                    token = await self._negotiate()
                except Exception as e:  # repli : connexion directe (skipNegotiation côté serveur)
                    log.warning("négociation SignalR impossible (%s) — tentative de connexion WebSocket directe", e)
            ws_url = self._ws_url(token)
            extra: dict[str, Any] = {}
            if ws_url.startswith("wss://"):
                extra["ssl"] = _ssl_context()
            hdrs = dict(self.headers)
            origin = hdrs.pop("Origin", None) or f"{'https' if ws_url.startswith('wss') else 'http'}://{urlsplit(ws_url).netloc}"
            ua = hdrs.pop("User-Agent", None)
            if ua:
                extra["user_agent_header"] = ua
            self._ws = await ws_connect(ws_url, additional_headers=hdrs or None, origin=origin, open_timeout=self.handshake_timeout,
                                        max_size=None, ping_interval=None, close_timeout=2, **extra)
            await self._handshake()
        except Exception:
            self.state = "disconnected"
            await self._teardown()
            raise
        self.state = "connected"
        self._closed.clear()
        self.last_rx = time.monotonic()
        self._tasks = [asyncio.create_task(self._reader(), name="signalr-reader"),
                       asyncio.create_task(self._pinger(), name="signalr-ping")]

    async def _negotiate(self) -> str | None:
        url = f"{self.base_url}{self.hub_path}/negotiate?negotiateVersion=1"
        async with httpx.AsyncClient(timeout=self.negotiate_timeout, transport=self._http_transport, headers=self.headers) as c:  # httpx : certifi par défaut
            r = await c.post(url)
            r.raise_for_status()
            data = r.json()
        self.negotiated = {k: v for k, v in data.items() if k not in ("accessToken",)}
        if data.get("error"):
            raise SignalRError(f"négociation refusée: {data['error']}")
        transports = [t.get("transport") for t in data.get("availableTransports", [])]
        if transports and "WebSockets" not in transports:
            raise SignalRError(f"WebSockets indisponible (transports: {transports})")
        if data.get("accessToken"):
            self.headers["Authorization"] = f"Bearer {data['accessToken']}"
        return data.get("connectionToken") or data.get("connectionId")

    def _ws_url(self, token: str | None) -> str:
        parts = urlsplit(self.base_url + self.hub_path)
        scheme = "wss" if parts.scheme == "https" else "ws"
        query = urlencode({"id": token}) if token else ""
        return urlunsplit((scheme, parts.netloc, parts.path, query, ""))

    async def _handshake(self) -> None:
        await self._ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        raw = await asyncio.wait_for(self._ws.recv(), self.handshake_timeout)
        raw = raw.decode() if isinstance(raw, bytes) else raw
        first = raw.split(RS)[0]
        resp = json.loads(first) if first else {}
        if resp.get("error"):
            raise SignalRError(f"handshake refusé: {resp['error']}")
        # Si le serveur a enchaîné d'autres messages dans la même trame, on les traite quand le lecteur démarre.
        rest = raw[len(first) + 1:]
        self._carry = rest

    # ------------------------------------------------------------------ boucle de lecture
    async def _reader(self) -> None:
        buf = getattr(self, "_carry", "")
        self._carry = ""
        try:
            while True:
                if buf:
                    buf = await self._consume(buf)
                    continue
                raw = await self._ws.recv()
                self.last_rx = time.monotonic()
                buf += raw.decode() if isinstance(raw, bytes) else raw
                buf = await self._consume(buf)
        except ConnectionClosed as e:
            self.close_reason = f"connexion fermée ({e.rcvd.code if e.rcvd else '?'})"
        except asyncio.CancelledError:
            raise
        except (WebSocketException, OSError, ValueError) as e:
            self.close_reason = self.close_reason or f"erreur de lecture: {e}"
        except Exception as e:  # ne jamais laisser mourir silencieusement
            log.exception("lecteur SignalR en échec")
            self.close_reason = f"erreur: {e}"
        finally:
            await self._on_disconnected()

    async def _consume(self, buf: str) -> str:
        while RS in buf:
            frame, buf = buf.split(RS, 1)
            if not frame:
                continue
            try:
                msg = json.loads(frame)
            except ValueError:
                log.warning("message SignalR illisible ignoré")
                continue
            await self._dispatch(msg)
        return buf

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        t = msg.get("type")
        if t == 1:  # invocation serveur -> client
            for h in list(self._handlers.get(str(msg.get("target", "")).lower(), ())):
                try:
                    r = h(*(msg.get("arguments") or []))
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    log.exception("handler SignalR en échec (%s)", msg.get("target"))
            if not self._handlers.get(str(msg.get("target", "")).lower()):
                log.debug("méthode cliente sans handler: %s", msg.get("target"))
        elif t == 3:  # completion d'une invocation
            fut = self._pending.pop(str(msg.get("invocationId")), None)
            if fut and not fut.done():
                if msg.get("error"):
                    fut.set_exception(SignalRError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
        elif t == 7:
            self.close_reason = f"fermeture demandée par le serveur: {msg.get('error') or 'sans erreur'}"
            raise ConnectionError(self.close_reason)
        # type 6 (ping) et 2 (stream item): rien à faire

    async def _pinger(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.ping_interval)
                if time.monotonic() - self.last_rx > self.server_timeout:
                    self.close_reason = f"aucune donnée du serveur depuis {self.server_timeout:.0f}s"
                    await self._abort()
                    return
                try:
                    await self._ws.send(json.dumps({"type": 6}) + RS)
                except (ConnectionClosed, WebSocketException, OSError):
                    return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ invocations
    async def invoke(self, target: str, *args: Any, timeout: float = 10.0) -> Any:
        if self.state != "connected" or self._ws is None:
            raise SignalRError("non connecté")
        self._inv += 1
        inv_id = str(self._inv)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[inv_id] = fut
        try:
            await self._ws.send(json.dumps({"type": 1, "invocationId": inv_id, "target": target, "arguments": list(args)}) + RS)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise SignalRError(f"délai dépassé pour {target}{args}") from None
        except (ConnectionClosed, WebSocketException, OSError) as e:
            raise SignalRError(f"envoi impossible: {e}") from e
        finally:
            self._pending.pop(inv_id, None)

    async def send(self, target: str, *args: Any) -> None:
        """Invocation sans attente de réponse (« fire and forget »)."""
        if self.state != "connected" or self._ws is None:
            raise SignalRError("non connecté")
        await self._ws.send(json.dumps({"type": 1, "target": target, "arguments": list(args)}) + RS)

    # ------------------------------------------------------------------ fermeture
    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def _abort(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _on_disconnected(self) -> None:
        self.state = "disconnected"
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(SignalRError("connexion perdue"))
        self._pending.clear()
        self._closed.set()

    async def _teardown(self) -> None:
        for t in self._tasks:
            if t is not asyncio.current_task():
                t.cancel()
        self._tasks = []
        await self._abort()
        self._ws = None

    async def close(self) -> None:
        await self._teardown()
        await self._on_disconnected()
