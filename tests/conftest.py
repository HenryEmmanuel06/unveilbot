from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.strategy import STRATEGY_CONFIG, timeframe_seconds  # noqa: E402
from src.data.models import Candle  # noqa: E402

START = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
PAD = 0.00002


def make_candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    index: int = 0,
    asset: str = "EUR/USD",
    timeframe: str = "5m",
    start: datetime = START,
) -> Candle:
    seconds = timeframe_seconds(timeframe)
    return Candle(
        asset=asset,
        timeframe=timeframe,
        timestamp=start + timedelta(seconds=seconds * index),
        open=round(open_, 5),
        high=round(high, 5),
        low=round(low, 5),
        close=round(close, 5),
        volume=1.0,
        source="test",
    )


class Feeder:
    """Builds a candle series where each candle opens at the previous close."""

    def __init__(self, start_price: float, asset: str = "EUR/USD", timeframe: str = "5m") -> None:
        self.price = start_price
        self.asset = asset
        self.timeframe = timeframe
        self.index = 0
        self.candles: list[Candle] = []

    def add_close(self, close: float, pad: float = PAD) -> Candle:
        open_ = self.price
        high = max(open_, close) + pad
        low = min(open_, close) - pad
        return self.add(open_, high, low, close)

    def add(self, open_: float, high: float, low: float, close: float) -> Candle:
        candle = make_candle(
            open_, high, low, close, index=self.index, asset=self.asset, timeframe=self.timeframe
        )
        self.candles.append(candle)
        self.price = close
        self.index += 1
        return candle

    def add_closes(self, closes: list[float], pad: float = PAD) -> list[Candle]:
        return [self.add_close(close, pad) for close in closes]

    def filler(self, count: int, base: float | None = None) -> list[Candle]:
        base = self.price if base is None else base
        closes = [base + (0.00001 if i % 2 else -0.00001) for i in range(count)]
        return self.add_closes(closes)


W_STRUCTURE_CLOSES = [
    1.1600,
    1.1598,
    1.1590,  # L1
    1.1595,
    1.1600,
    1.1610,
    1.1612,  # P
    1.1608,
    1.1600,
    1.1594,  # L2
    1.1598,
    1.1604,
]

M_STRUCTURE_CLOSES = [
    1.1600,
    1.1602,
    1.1610,  # H1
    1.1605,
    1.1600,
    1.1590,
    1.1588,  # T
    1.1592,
    1.1600,
    1.1606,  # H2
    1.1602,
    1.1596,
]


@pytest.fixture
def raw_candles(monkeypatch) -> None:
    """Run a test against plain OHLC candles instead of Heikin Ashi.

    The base W/M rules are specified on real candles, so their tests must not
    depend on the Heikin Ashi smoothing layer.
    """
    monkeypatch.setitem(STRATEGY_CONFIG, "use_heikin_ashi", False)


@pytest.fixture
def heikin_ashi_everywhere(monkeypatch) -> None:
    """Heikin Ashi drives structure detection and level breaks as well."""
    monkeypatch.setitem(STRATEGY_CONFIG, "use_heikin_ashi", True)
    monkeypatch.setitem(STRATEGY_CONFIG, "heikin_ashi_scope", "all")


@pytest.fixture
def w_feeder() -> Feeder:
    feeder = Feeder(1.1600)
    feeder.filler(60)
    return feeder


@pytest.fixture
def m_feeder() -> Feeder:
    feeder = Feeder(1.1600)
    feeder.filler(60)
    return feeder
