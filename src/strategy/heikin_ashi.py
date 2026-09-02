"""Heikin Ashi candle conversion.

Heikin Ashi ("average bar") smooths a normal OHLC series so that noisy
alternating candles collapse into runs of one colour. That is exactly what the
W/M strategy needs for pullback counting: a 2-3 candle pullback is only
meaningful when the candle colour reflects the actual short-term direction and
not a single-tick wick.

Formulas (standard definition):

    ha_close = (open + high + low + close) / 4
    ha_open  = (previous ha_open + previous ha_close) / 2
    ha_high  = max(high, ha_open, ha_close)
    ha_low   = min(low,  ha_open, ha_close)

The first candle of a series has no predecessor, so it is seeded with
``ha_open = (open + close) / 2``.

The conversion is deterministic and stateful: every Heikin Ashi candle depends
on its predecessor, which is why `HeikinAshiConverter` keeps a rolling
reference instead of recomputing the whole series on every tick.
"""

from __future__ import annotations

from src.data.models import Candle


def heikin_ashi_candle(candle: Candle, previous: Candle | None = None) -> Candle:
    """Convert one candle. `previous` is the preceding *Heikin Ashi* candle."""
    ha_close = (candle.open + candle.high + candle.low + candle.close) / 4.0
    if previous is None:
        ha_open = (candle.open + candle.close) / 2.0
    else:
        ha_open = (previous.open + previous.close) / 2.0

    return Candle(
        asset=candle.asset,
        timeframe=candle.timeframe,
        timestamp=candle.timestamp,
        open=ha_open,
        high=max(candle.high, ha_open, ha_close),
        low=min(candle.low, ha_open, ha_close),
        close=ha_close,
        volume=candle.volume,
        source=candle.source,
    )


def heikin_ashi_series(candles: list[Candle]) -> list[Candle]:
    """Convert a whole series. Index i of the result matches index i of input."""
    converted: list[Candle] = []
    previous: Candle | None = None
    for candle in candles:
        previous = heikin_ashi_candle(candle, previous)
        converted.append(previous)
    return converted


class HeikinAshiConverter:
    """Rolling converter: feed closed candles in order, get Heikin Ashi out."""

    def __init__(self) -> None:
        self.previous: Candle | None = None

    def add(self, candle: Candle) -> Candle:
        self.previous = heikin_ashi_candle(candle, self.previous)
        return self.previous

    def seed(self, candles: list[Candle]) -> list[Candle]:
        return [self.add(candle) for candle in candles]

    def reset(self) -> None:
        self.previous = None
