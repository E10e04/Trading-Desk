"""Notifications persistantes (table `notifications`) + diffusion temps réel (bus -> WebSocket)."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select

from .bus import EventBus
from .db import session_scope
from .models import Notification, utcnow

LEVELS = ("info", "success", "warning", "error")


def notify(bus: EventBus | None, level: str, source: str, title: str, body: str = "", symbol: str | None = None,
           dedupe_key: str | None = None, throttle_s: float = 0) -> dict[str, Any] | None:
    """Crée une notification. Avec `dedupe_key` + `throttle_s`, une notification identique récente est ignorée (retourne None)."""
    level = level if level in LEVELS else "info"
    with session_scope() as s:
        if dedupe_key and throttle_s > 0:
            since = utcnow() - dt.timedelta(seconds=throttle_s)
            if s.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key, Notification.ts >= since).limit(1)):
                return None
        n = Notification(level=level, source=source, title=title[:200], body=body, symbol=symbol, dedupe_key=dedupe_key)
        s.add(n)
        s.flush()
        view = {"id": n.id, "ts": n.ts.isoformat() + "Z", "level": n.level, "source": n.source, "title": n.title, "body": n.body,
                "symbol": n.symbol, "read": False}
    if bus is not None:
        bus.publish_nowait("notification", view)
    return view
