"""Pullback tracking - README 29-32, 36-39.

W: 2 or 3 consecutive RED candles, reference = high of RED #1.
M: 2 or 3 consecutive GREEN candles, reference = low of GREEN #1.
A 4th pullback candle invalidates the setup permanently.

The tracker is deliberately agnostic about how a candle was built. When
``STRATEGY_CONFIG["use_heikin_ashi"]`` is on, `SetupStateMachine` feeds it
Heikin Ashi candles so that the colour runs counted here reflect the smoothed
direction instead of single-tick noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from config.strategy import STRATEGY_CONFIG
from src.data.models import Candle


class PullbackEvent(str, Enum):
    WAITING = "WAITING"
    STARTED = "STARTED"
    CONTINUED = "CONTINUED"
    VALID = "VALID"
    CONFIRMATION_CANDLE = "CONFIRMATION_CANDLE"
    INVALID_TOO_MANY = "INVALID_TOO_MANY"
    INVALID_TOO_SHORT = "INVALID_TOO_SHORT"
    IGNORED_DOJI = "IGNORED_DOJI"


@dataclass
class PullbackState:
    pattern: str  # "W" or "M"
    count: int = 0
    reference_price: float | None = None
    reference_candle: Candle | None = None
    started_at: datetime | None = None
    minimum: int = field(default_factory=lambda: int(STRATEGY_CONFIG["minimum_pullback_candles"]))
    maximum: int = field(default_factory=lambda: int(STRATEGY_CONFIG["maximum_pullback_candles"]))

    @property
    def is_valid_length(self) -> bool:
        return self.minimum <= self.count <= self.maximum


class PullbackTracker:
    """Feeds closed candles into the pullback rules of one setup."""

    def __init__(self, pattern: str) -> None:
        if pattern not in ("W", "M"):
            raise ValueError("pattern must be 'W' or 'M'")
        self.state = PullbackState(pattern=pattern)

    @property
    def count(self) -> int:
        return self.state.count

    @property
    def reference_price(self) -> float | None:
        return self.state.reference_price

    @property
    def reference_candle(self) -> Candle | None:
        return self.state.reference_candle

    def _is_pullback_candle(self, candle: Candle) -> bool:
        return candle.is_red if self.state.pattern == "W" else candle.is_green

    def _is_opposite_candle(self, candle: Candle) -> bool:
        return candle.is_green if self.state.pattern == "W" else candle.is_red

    def add(self, candle: Candle) -> PullbackEvent:
        state = self.state

        if candle.is_doji and not STRATEGY_CONFIG["allow_doji"]:
            return PullbackEvent.IGNORED_DOJI

        if self._is_pullback_candle(candle):
            if state.count >= state.maximum:
                return PullbackEvent.INVALID_TOO_MANY
            state.count += 1
            if state.count == 1:
                state.started_at = candle.timestamp
                state.reference_candle = candle
                state.reference_price = candle.high if state.pattern == "W" else candle.low
                return PullbackEvent.STARTED
            if state.count == state.minimum:
                return PullbackEvent.VALID
            return PullbackEvent.CONTINUED

        if self._is_opposite_candle(candle):
            if state.count == 0:
                return PullbackEvent.WAITING
            if state.count < state.minimum:
                return PullbackEvent.INVALID_TOO_SHORT
            return PullbackEvent.CONFIRMATION_CANDLE

        return PullbackEvent.WAITING
