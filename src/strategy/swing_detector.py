"""Fractal swing high/low detection with configurable noise filtering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from config.strategy import STRATEGY_CONFIG
from src.data.models import Candle


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True)
class Swing:
    index: int
    timestamp: datetime
    price: float
    kind: SwingKind

    @property
    def is_low(self) -> bool:
        return self.kind is SwingKind.LOW

    @property
    def is_high(self) -> bool:
        return self.kind is SwingKind.HIGH


class SwingDetector:
    """Detects meaningful swings so random fluctuations cannot become L1/P/L2."""

    def __init__(self, lookback: int | None = None, min_swing_size: float | None = None) -> None:
        self.lookback = int(lookback if lookback is not None else STRATEGY_CONFIG["swing_lookback"])
        self.min_swing_size = float(
            min_swing_size if min_swing_size is not None else STRATEGY_CONFIG["min_swing_size"]
        )

    def detect(self, candles: list[Candle]) -> list[Swing]:
        raw = self._raw_swings(candles)
        alternating = self._alternate(raw)
        return self._filter_noise(alternating)

    def _raw_swings(self, candles: list[Candle]) -> list[Swing]:
        lb = self.lookback
        swings: list[Swing] = []
        if len(candles) < 2 * lb + 1:
            return swings
        for i in range(lb, len(candles) - lb):
            candle = candles[i]
            left = candles[i - lb : i]
            right = candles[i + 1 : i + 1 + lb]
            if candle.low < min(c.low for c in left) and candle.low <= min(c.low for c in right):
                swings.append(Swing(i, candle.timestamp, candle.low, SwingKind.LOW))
                continue
            if candle.high > max(c.high for c in left) and candle.high >= max(c.high for c in right):
                swings.append(Swing(i, candle.timestamp, candle.high, SwingKind.HIGH))
        swings.sort(key=lambda s: s.index)
        return swings

    @staticmethod
    def _alternate(swings: list[Swing]) -> list[Swing]:
        """Keep a strict HIGH/LOW alternation, keeping the most extreme of a run."""
        result: list[Swing] = []
        for swing in swings:
            if not result:
                result.append(swing)
                continue
            last = result[-1]
            if last.kind is swing.kind:
                better = (
                    swing.price < last.price if swing.is_low else swing.price > last.price
                )
                if better:
                    result[-1] = swing
            else:
                result.append(swing)
        return result

    def _filter_noise(self, swings: list[Swing]) -> list[Swing]:
        if self.min_swing_size <= 0 or len(swings) < 2:
            return swings
        result: list[Swing] = [swings[0]]
        for swing in swings[1:]:
            previous = result[-1]
            reference = abs(previous.price) or 1.0
            if abs(swing.price - previous.price) / reference < self.min_swing_size:
                # Too small to be meaningful: keep the more extreme point.
                if previous.kind is swing.kind:
                    better = (
                        swing.price < previous.price if swing.is_low else swing.price > previous.price
                    )
                    if better:
                        result[-1] = swing
                continue
            result.append(swing)
        return self._alternate(result)


def last_swings(swings: list[Swing], kind: SwingKind, count: int) -> list[Swing]:
    matched = [s for s in swings if s.kind is kind]
    return matched[-count:]
