"""Market data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.utils.time import ensure_utc


class CandleColor(str, Enum):
    GREEN = "GREEN"
    RED = "RED"
    DOJI = "DOJI"


@dataclass(frozen=True)
class Tick:
    asset: str
    timestamp: datetime
    price: float
    source: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))


@dataclass(frozen=True)
class Candle:
    asset: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    source: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        if self.high < self.low:
            raise ValueError(f"Malformed candle (high < low) for {self.asset} {self.timeframe}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"Malformed candle (open outside range) for {self.asset}")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"Malformed candle (close outside range) for {self.asset}")

    @property
    def color(self) -> CandleColor:
        if self.close > self.open:
            return CandleColor.GREEN
        if self.close < self.open:
            return CandleColor.RED
        return CandleColor.DOJI

    @property
    def is_green(self) -> bool:
        return self.color is CandleColor.GREEN

    @property
    def is_red(self) -> bool:
        return self.color is CandleColor.RED

    @property
    def is_doji(self) -> bool:
        return self.color is CandleColor.DOJI

    def as_row(self) -> dict:
        return {
            "asset": self.asset,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source": self.source,
        }
