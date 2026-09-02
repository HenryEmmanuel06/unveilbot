"""Non-live market-data providers plus the provider factory.

Pocket Option is the ONLY source of live market data (see
`src/data/pocket_option.py`). The providers in this module exist purely for
offline work:

- `MockProvider`: synthetic prices for unit tests and development.
- `CsvProvider`: locally stored historical candles for backtests.

Neither may be used in PAPER or LIVE_SIGNAL mode: candles they emit are tagged
with a non-Pocket-Option source and are rejected by the strategy engine.
"""

from __future__ import annotations

import asyncio
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator

from config.strategy import timeframe_seconds
from src.data.base import MarketDataProvider, ProviderError
from src.data.models import Candle, Tick
from src.utils.logging import get_logger
from src.utils.time import UTC, ensure_utc, floor_to_timeframe, now_utc

logger = get_logger(__name__)

BASE_PRICES = {
    "EUR/USD": 1.1650,
    "GBP/USD": 1.2720,
    "USD/JPY": 147.20,
    "AUD/USD": 0.6640,
    "USD/CAD": 1.3580,
    "USD/CHF": 0.8820,
    "EUR/GBP": 0.8580,
    "EUR/JPY": 171.50,
    "GBP/JPY": 187.40,
}


def base_price(asset: str) -> float:
    key = asset.replace(" OTC", "")
    return BASE_PRICES.get(key, 1.0)


def tick_size(asset: str) -> float:
    return 0.01 if "JPY" in asset else 0.0001


class MockProvider(MarketDataProvider):
    """Deterministic synthetic price stream for development and tests."""

    name = "mock"
    source = "MOCK"
    native_timeframes = ("15s", "1m", "5m", "15m")
    supports_streaming = True

    def __init__(
        self,
        assets: list[str] | None = None,
        seed: int = 7,
        tick_interval: float = 0.25,
        speed: float = 1.0,
    ) -> None:
        self.assets = assets or []
        self.tick_interval = tick_interval
        self.speed = speed
        self._rng = random.Random(seed)
        self._prices: dict[str, float] = {}
        self._connected = False

    async def connect(self) -> None:
        self._connected = True
        logger.info("MockProvider connected")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("MockProvider disconnected")

    async def subscribe(self, assets: list[str]) -> None:
        self.assets = list(assets)
        for asset in self.assets:
            self._prices.setdefault(asset, base_price(asset))

    def _next_price(self, asset: str) -> float:
        step = tick_size(asset)
        price = self._prices.setdefault(asset, base_price(asset))
        drift = self._rng.gauss(0, 1.2) * step
        price = max(step, price + drift)
        self._prices[asset] = price
        return round(price, 5 if step < 0.001 else 3)

    async def get_candles(self, asset: str, timeframe: str, limit: int) -> list[Candle]:
        seconds = timeframe_seconds(timeframe)
        end = floor_to_timeframe(now_utc(), seconds)
        candles: list[Candle] = []
        price = base_price(asset)
        step = tick_size(asset)
        rng = random.Random(f"{asset}-{timeframe}")
        for i in range(limit, 0, -1):
            ts = end - timedelta(seconds=seconds * i)
            opens = price
            highs = lows = price
            for _ in range(8):
                price = max(step, price + rng.gauss(0, 1.1) * step)
                highs = max(highs, price)
                lows = min(lows, price)
            candles.append(
                Candle(
                    asset=asset,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=round(opens, 5),
                    high=round(highs, 5),
                    low=round(lows, 5),
                    close=round(price, 5),
                    volume=8.0,
                    source=self.source,
                )
            )
        self._prices[asset] = price
        return candles

    async def stream_prices(self) -> AsyncIterator[Tick]:
        if not self._connected:
            raise ProviderError("MockProvider not connected")
        virtual = now_utc()
        while self._connected:
            for asset in self.assets:
                yield Tick(
                    asset=asset,
                    timestamp=virtual,
                    price=self._next_price(asset),
                    source=self.source,
                )
            virtual += timedelta(seconds=self.tick_interval * self.speed)
            await asyncio.sleep(self.tick_interval)


class CsvProvider(MarketDataProvider):
    """Historical candles from `data/historical/<SLUG>_<timeframe>.csv`.

    Expected header: timestamp,open,high,low,close[,volume]
    """

    name = "csv"
    source = "CSV_HISTORICAL"
    native_timeframes = ("15s", "1m", "5m", "15m")
    supports_streaming = False

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    async def connect(self) -> None:
        if not self.directory.exists():
            raise ProviderError(f"Historical directory missing: {self.directory}")

    async def disconnect(self) -> None:
        return None

    async def subscribe(self, assets: list[str]) -> None:
        return None

    def _path(self, asset: str, timeframe: str) -> Path:
        from config.assets import asset_slug

        return self.directory / f"{asset_slug(asset)}_{timeframe}.csv"

    def load(self, asset: str, timeframe: str) -> list[Candle]:
        path = self._path(asset, timeframe)
        if not path.exists():
            raise ProviderError(f"No historical file for {asset} {timeframe}: {path}")
        candles: list[Candle] = []
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                candles.append(
                    Candle(
                        asset=asset,
                        timeframe=timeframe,
                        timestamp=_parse_timestamp(row["timestamp"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]) if row.get("volume") else None,
                        source=self.source,
                    )
                )
        candles.sort(key=lambda c: c.timestamp)
        return candles

    async def get_candles(self, asset: str, timeframe: str, limit: int) -> list[Candle]:
        return self.load(asset, timeframe)[-limit:]

    async def stream_prices(self) -> AsyncIterator[Tick]:
        raise ProviderError("CsvProvider does not stream prices")
        yield  # pragma: no cover


def _parse_timestamp(raw: str) -> datetime:
    raw = raw.strip()
    if raw.isdigit():
        value = int(raw)
        if value > 10_000_000_000:
            value //= 1000
        return datetime.fromtimestamp(value, tz=UTC)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return ensure_utc(datetime.strptime(raw, fmt))
        except ValueError:
            continue
    return ensure_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))


def build_provider(name: str, *, settings=None) -> MarketDataProvider:
    """Pocket Option for live data; mock/csv only for offline work."""
    from config.settings import settings as default_settings

    cfg = settings or default_settings
    key = name.lower()
    if key in ("pocketoption", "pocket_option", "po"):
        from src.data.pocket_option import PocketOptionDataProvider

        return PocketOptionDataProvider(cfg.po_ssid, cfg.po_region)
    if key == "mock":
        return MockProvider()
    if key == "csv":
        return CsvProvider(cfg.historical_dir)
    raise ProviderError(
        f"Unknown market data provider {name!r}. Available: pocketoption, mock, csv"
    )
