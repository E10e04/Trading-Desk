"""Historique 5 min complémentaire depuis Yahoo Finance (~60 jours), pour alimenter le TimeMachine.

Source NON officielle (point d'accès interne du site de Yahoo) : elle peut changer, être limitée ou bloquée sans préavis. Elle ne sert
qu'à combler l'historique que BiQuote ne garde pas (3-4 jours) ; BiQuote reste la source des prix en direct, des ordres et des bougies qu'il fournit.
Toute panne est silencieuse pour le reste de l'application."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from urllib.parse import quote

import httpx

from .provider import Bar, Instrument, ProviderError

log = logging.getLogger(__name__)

BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
INDEX = {"US500": "^GSPC", "SPX500": "^GSPC", "SPX": "^GSPC", "US30": "^DJI", "DJ30": "^DJI", "DJI": "^DJI", "NAS100": "^NDX", "US100": "^NDX", "NDX": "^NDX",
         "GER40": "^GDAXI", "DAX40": "^GDAXI", "DE40": "^GDAXI", "UK100": "^FTSE", "FRA40": "^FCHI", "JP225": "^N225", "JPN225": "^N225", "HK50": "^HSI"}
ENERGY = {"XTIUSD": "CL=F", "USOIL": "CL=F", "WTI": "CL=F", "XBRUSD": "BZ=F", "UKOIL": "BZ=F", "BRENT": "BZ=F", "XNGUSD": "NG=F", "NATGAS": "NG=F"}
METAL = {"XAUUSD": "XAUUSD=X", "XAGUSD": "XAGUSD=X"}


def yahoo_symbol(inst: Instrument) -> str | None:
    """Symbole Yahoo équivalent, ou None si on n'en connaît pas avec certitude (on ne devine pas : un mauvais symbole = de mauvais prix)."""
    s = inst.symbol.upper()
    c = inst.asset_class
    if c == "forex" and inst.base_ccy and inst.quote_ccy:
        return f"{inst.base_ccy.upper()}{inst.quote_ccy.upper()}=X"
    if c == "metal":
        return METAL.get(s)
    if c == "energy":
        return ENERGY.get(s)
    if c == "index":
        return INDEX.get(s)
    if c == "crypto":
        base = (inst.base_ccy or "").upper()
        quote_ = (inst.quote_ccy or "").upper()
        if not base:
            for q in ("USDT", "USD"):
                if s.endswith(q) and len(s) > len(q):
                    base, quote_ = s[: -len(q)], q
                    break
        return f"{base}-USD" if base and quote_ in ("USD", "USDT") else None
    if c == "equity" and s.isalpha() and len(s) <= 5:
        return s
    return None


def parse_chart(payload: Any, now: float | None = None) -> list[Bar]:
    now = now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        raise ProviderError(f"Yahoo : {(chart['error'] or {}).get('description') or chart['error']}")
    res = (chart.get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        raise ProviderError("Yahoo : aucune donnée")
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    out: list[Bar] = []
    for i, ts in enumerate(res["timestamp"]):
        try:
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        except (KeyError, IndexError):
            continue
        if None in (o, h, l, c) or h < l:
            continue
        v = (q.get("volume") or [None] * (i + 1))[i] if i < len(q.get("volume") or []) else 0
        out.append(Bar(ts=int(ts), o=float(o), h=float(h), l=float(l), c=float(c), v=float(v or 0), closed=ts + 300 <= now))
    return out


class YahooHistory:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 15.0) -> None:
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=True,
                                       headers={"User-Agent": "Mozilla/5.0 (compatible; Monkora; paper-trading)"})

    async def bars_5m(self, ysym: str, days: int = 60) -> list[Bar]:
        try:
            r = await self._http.get(BASE + quote(ysym, safe=""), params={"interval": "5m", "range": f"{days}d", "includePrePost": "false"})
        except httpx.HTTPError as e:
            raise ProviderError(f"Yahoo injoignable : {type(e).__name__}") from e
        if r.status_code != 200:
            raise ProviderError(f"Yahoo : HTTP {r.status_code}")
        try:
            return parse_chart(r.json())
        except ValueError as e:
            raise ProviderError("Yahoo : réponse illisible") from e

    async def aclose(self) -> None:
        await self._http.aclose()
