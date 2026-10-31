"""Interface de fournisseur de données de marché + types normalisés.

Les agents et le moteur de simulation ne dépendent QUE de ces types : ajouter un autre
fournisseur (autre API) = écrire une classe `MarketDataProvider`, sans toucher au reste.
"""
from __future__ import annotations

import abc
import datetime as dt
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

TickHandler = Callable[["Quote"], Awaitable[None] | None]
StateHandler = Callable[[str, str], Awaitable[None] | None]  # (symbol, 'open'|'closed'|'unknown')


@dataclass
class Quote:
    symbol: str
    bid: float | None
    ask: float | None
    mid: float
    ts_source: dt.datetime | None  # horodatage fourni par la source (UTC naïf)
    ts_recv: dt.datetime  # horodatage de réception local (UTC naïf)
    market_state: str = "unknown"  # open | closed | unknown
    provider: str = ""
    age_at_recv_s: float = 0.0  # âge de la cotation à la réception (ex. `quoteAgeSeconds` de BiQuote)
    change_pct: float | None = None  # variation de la journée en %, si la source la fournit

    @property
    def spread(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None

    def age_s(self, now: dt.datetime | None = None) -> float:
        """Âge actuel de la cotation. Indépendant de l'horloge de la source (pas de décalage possible) :
        âge annoncé à la réception + temps écoulé depuis la réception."""
        now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        return max(0.0, self.age_at_recv_s + (now - self.ts_recv).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts_source"] = self.ts_source.isoformat() + "Z" if self.ts_source else None
        d["ts_recv"] = self.ts_recv.isoformat() + "Z"
        d["spread"] = self.spread
        return d


@dataclass
class Bar:
    ts: int  # début de bougie, secondes epoch UTC
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0  # BiQuote : toujours 0 ; on y met le nombre de ticks pour les bougies live
    closed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"t": self.ts, "o": self.o, "h": self.h, "l": self.l, "c": self.c, "v": self.v, "closed": self.closed}


@dataclass
class Instrument:
    symbol: str
    description: str = ""
    asset_class: str = "other"  # forex | metal | energy | crypto | index | equity | other
    exchange: str = ""
    digits: int = 5
    tick_size: float | None = None
    contract_size: float | None = None
    base_ccy: str | None = None
    quote_ccy: str | None = None
    live: bool = False  # cote « à cet instant »
    raw_type: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("extra", None)
        return d


def parse_ts(value: Any) -> dt.datetime | None:
    """Accepte ISO-8601 (avec ou sans Z / offset), epoch secondes ou millisecondes. Retourne UTC naïf."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            if not math.isfinite(v) or v <= 0:
                return None
            if v > 1e14:  # microsecondes
                v /= 1e6
            elif v > 1e11:  # millisecondes
                v /= 1e3
            return dt.datetime.fromtimestamp(v, dt.timezone.utc).replace(tzinfo=None)
        s = str(value).strip()
        if s.replace(".", "", 1).isdigit():
            return parse_ts(float(s))
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is not None:
            d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return d
    except (ValueError, OverflowError, OSError):
        return None


class MarketDataProvider(abc.ABC):
    """Contrat d'un fournisseur de données (REST + flux)."""

    name: str = "provider"

    @abc.abstractmethod
    async def list_instruments(self) -> list[Instrument]: ...

    @abc.abstractmethod
    async def latest(self, symbols: list[str]) -> dict[str, Quote]: ...

    @abc.abstractmethod
    async def history(self, symbol: str, interval: str = "5m", limit: int = 500,
                      from_: dt.datetime | None = None, to: dt.datetime | None = None) -> list[Bar]:
        """Bougies, ordre chronologique (plus ancienne d'abord)."""

    @abc.abstractmethod
    async def start_stream(self, on_tick: TickHandler, on_state: StateHandler | None = None) -> None: ...

    @abc.abstractmethod
    async def subscribe(self, symbols: list[str]) -> None: ...

    @abc.abstractmethod
    async def stop_stream(self) -> None: ...

    @abc.abstractmethod
    def stream_status(self) -> dict[str, Any]: ...

    async def aclose(self) -> None:  # pragma: no cover - optionnel
        await self.stop_stream()


class ProviderError(RuntimeError):
    pass
