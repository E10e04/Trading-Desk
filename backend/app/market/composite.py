"""Fournisseur composite : BiQuote pour tout, Kraken pour les cryptos (prix du marché réel plutôt que CFD de courtier)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from .kraken import KrakenProvider
from .provider import Bar, Instrument, MarketDataProvider, Quote, StateHandler, TickHandler

log = logging.getLogger(__name__)


class CompositeProvider(MarketDataProvider):
    def __init__(self, primary: MarketDataProvider, crypto: KrakenProvider, *, use_crypto: bool = True) -> None:
        self.primary, self.crypto, self.use_crypto = primary, crypto, use_crypto
        self.name = f"{primary.name}+{crypto.name}" if use_crypto else primary.name
        self._crypto_syms: set[str] = set()
        self.crypto_error: str | None = None

    def __getattr__(self, item: str):  # news(), calendar_upcoming()… viennent de la source principale
        if item in ("primary", "crypto", "use_crypto", "_crypto_syms", "crypto_error"):
            raise AttributeError(item)
        return getattr(self.primary, item)

    def _split(self, symbols: list[str]) -> tuple[list[str], list[str]]:
        c = [s for s in symbols if s.upper() in self._crypto_syms]
        return [s for s in symbols if s.upper() not in self._crypto_syms], c

    async def list_instruments(self) -> list[Instrument]:
        if not self.use_crypto:
            return await self.primary.list_instruments()
        # les deux catalogues sont demandés en parallèle (le démarrage ne cumule plus leurs délais)
        base, crypto = await asyncio.gather(self.primary.list_instruments(), self.crypto.list_instruments(),
                                            return_exceptions=True)
        if isinstance(base, BaseException):
            raise base
        if isinstance(crypto, BaseException):  # Kraken injoignable : on garde les cryptos de BiQuote
            self.crypto_error = f"{type(crypto).__name__}: {str(crypto)[:160]}"
            log.warning("Kraken indisponible (%s) : cryptos servies par BiQuote", self.crypto_error)
            self._crypto_syms = set()
            return base
        self.crypto_error = None
        self._crypto_syms = {i.symbol for i in crypto}
        # on garde les cryptos de BiQuote que Kraken ne cote pas (ex. BNB)
        return [i for i in base if i.asset_class != "crypto" or i.symbol not in self._crypto_syms] + crypto

    async def latest(self, symbols: list[str]) -> dict[str, Quote]:
        other, crypto = self._split(symbols)
        out: dict[str, Quote] = {}
        if other:
            out.update(await self.primary.latest(other))
        if crypto:
            try:
                out.update(await self.crypto.latest(crypto))
            except Exception as e:  # noqa: BLE001
                log.info("prix Kraken indisponibles: %s", e)
        return out

    async def history(self, symbol: str, interval: str = "5m", limit: int = 500,
                      from_: dt.datetime | None = None, to: dt.datetime | None = None) -> list[Bar]:
        if symbol.upper() in self._crypto_syms:
            return await self.crypto.history(symbol, interval, limit, from_, to)
        return await self.primary.history(symbol, interval, limit, from_, to)

    async def start_stream(self, on_tick: TickHandler, on_state: StateHandler | None = None) -> None:
        await self.primary.start_stream(on_tick, on_state)
        if self.use_crypto:
            await self.crypto.start_stream(on_tick, on_state)

    async def subscribe(self, symbols: list[str]) -> None:
        other, crypto = self._split(symbols)
        await self.primary.subscribe(other)
        if crypto:
            await self.crypto.subscribe(crypto)

    async def stop_stream(self) -> None:
        await self.primary.stop_stream()
        await self.crypto.stop_stream()

    def stream_status(self) -> dict[str, Any]:
        st = self.primary.stream_status()
        if self.use_crypto:
            st = {**st, "crypto": {**self.crypto.stream_status(), "provider": "kraken", "error": self.crypto_error,
                                   "symbols": len(self._crypto_syms)}}
        return st

    async def diagnose(self) -> list[dict[str, Any]]:
        steps = list(await self.primary.diagnose()) if hasattr(self.primary, "diagnose") else []
        if self.use_crypto:
            steps += await self.crypto.diagnose()
        return steps

    async def aclose(self) -> None:
        for p in (self.primary, self.crypto):
            fn = getattr(p, "aclose", None)
            if fn:
                await fn()
