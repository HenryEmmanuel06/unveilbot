"""Provider abstraction. The strategy engine never depends on an implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from src.data.models import Candle, Tick


class MarketDataProvider(ABC):
    """Base interface for every market-data source."""

    name: str = "base"
    #: Value written into every candle's `source` field.
    source: str = "UNKNOWN"
    #: Timeframes the provider can deliver natively as candles.
    native_timeframes: tuple[str, ...] = ()
    #: Whether the provider streams prices frequently enough to build candles.
    supports_streaming: bool = False

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def subscribe(self, assets: list[str]) -> None: ...

    @abstractmethod
    async def get_candles(self, asset: str, timeframe: str, limit: int) -> list[Candle]: ...

    @abstractmethod
    def stream_prices(self) -> AsyncIterator[Tick]: ...

    async def resubscribe(self) -> None:
        """Re-issue active subscriptions (after history requests or reconnects)."""
        return None

    async def get_ticks(self, asset: str) -> list[Tick]:
        """Raw price ticks, when the provider exposes them."""
        raise ProviderError(f"{self.name} does not expose historical ticks")

    async def get_historical_candles(
        self, asset: str, timeframe: str, limit: int
    ) -> list[Candle]:
        """Backwards compatible alias of :meth:`get_candles`."""
        return await self.get_candles(asset, timeframe, limit)


class ProviderError(RuntimeError):
    """Raised when a provider cannot deliver reliable data."""
