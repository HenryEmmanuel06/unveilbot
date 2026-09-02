"""Flat-candle execution guard.

A Heikin Ashi candle with no wick against the trade direction is the classic
"no opposition" bar: buyers never gave price back below the open (flat bottom)
or sellers never let it above the open (flat top).

    W / CALL  -> the forming candle must be GREEN with a flat bottom
                 (ha_low == ha_open, no lower wick)
    M / PUT   -> the forming candle must be RED with a flat top
                 (ha_high == ha_open, no upper wick)

The candle inspected is the one *currently forming* on a higher timeframe, so
the check answers "is the bigger picture pushing my way right now?" at the
moment the order would be sent. Because Heikin Ashi values depend on the
preceding candle, the guard rebuilds the tail of the higher-timeframe series
(closed history + the partial candle) before reading the last bar.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.strategy import STRATEGY_CONFIG, flat_candle_timeframes
from src.data.models import Candle
from src.strategy.heikin_ashi import heikin_ashi_candle, heikin_ashi_series

#: Closed candles used to seed the Heikin Ashi state before the partial candle.
HA_WARMUP_CANDLES = 60


@dataclass(frozen=True)
class FlatCandleResult:
    allowed: bool
    reason: str
    #: timeframe -> human readable state, for logs and the database.
    states: dict[str, str]


def _wick_against(candle: Candle, pattern: str) -> float:
    """Size of the wick that opposes the trade."""
    if pattern == "W":
        return max(0.0, candle.open - candle.low)
    return max(0.0, candle.high - candle.open)


def is_flat_candle(candle: Candle, pattern: str, tolerance: float | None = None) -> bool:
    """True when `candle` has no meaningful wick against `pattern`."""
    if tolerance is None:
        tolerance = float(STRATEGY_CONFIG["flat_candle_wick_tolerance"])
    if pattern == "W" and not candle.is_green:
        return False
    if pattern == "M" and not candle.is_red:
        return False
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return True
    return _wick_against(candle, pattern) <= tolerance * candle_range


def describe(candle: Candle, pattern: str) -> str:
    colour = "green" if candle.is_green else "red" if candle.is_red else "doji"
    side = "flat bottom" if pattern == "W" else "flat top"
    wick = _wick_against(candle, pattern)
    state = side if is_flat_candle(candle, pattern) else f"wick {wick:.5f}"
    return f"{colour}, {state}"


def forming_heikin_ashi(closed: list[Candle], partial: Candle | None) -> Candle | None:
    """Heikin Ashi version of the candle currently forming on a timeframe."""
    if partial is None:
        return None
    warmup = closed[-HA_WARMUP_CANDLES:] if closed else []
    # A partial candle that shares its timestamp with the last closed candle is
    # the same bar seen twice; the closed one already covers it.
    if warmup and partial.timestamp <= warmup[-1].timestamp:
        warmup = warmup[:-1]
    if not warmup:
        return heikin_ashi_candle(partial, None)
    return heikin_ashi_candle(partial, heikin_ashi_series(warmup)[-1])


class FlatCandleGuard:
    """Checks the forming higher-timeframe candles before an execution."""

    def __init__(self, closed_source, partial_source=None) -> None:
        """`closed_source(asset, tf) -> list[Candle]`,
        `partial_source(asset, tf) -> Candle | None`."""
        self._closed = closed_source
        self._partial = partial_source

    def check(self, asset: str, timeframe: str, pattern: str) -> FlatCandleResult:
        required = flat_candle_timeframes(timeframe)
        if not required or self._partial is None:
            return FlatCandleResult(True, "flat candle filter not applied", {})

        states: dict[str, str] = {}
        blocking: list[str] = []
        for htf in required:
            candle = forming_heikin_ashi(self._closed(asset, htf), self._partial(asset, htf))
            if candle is None:
                states[htf] = "no data"
                blocking.append(f"{htf} has no forming candle")
                continue
            states[htf] = describe(candle, pattern)
            if not is_flat_candle(candle, pattern):
                blocking.append(f"{htf} {states[htf]}")

        if blocking:
            side = "flat bottom" if pattern == "W" else "flat top"
            return FlatCandleResult(
                False, f"forming candle is not a {side}: {', '.join(blocking)}", states
            )
        return FlatCandleResult(True, "forming higher timeframe candles are flat", states)
