"""Higher-timeframe directional bias (README 46, 47).

No indicators (RSI/MACD/EMA/SMA/stochastic) are used. The bias methodology is
configurable and must be validated separately through backtesting.
"""

from __future__ import annotations

from enum import Enum

from config.strategy import STRATEGY_CONFIG, higher_timeframes
from src.data.models import Candle
from src.strategy.swing_detector import SwingDetector, SwingKind


class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


def _swing_structure_bias(candles: list[Candle]) -> Bias:
    """Higher highs + higher lows = bullish, lower highs + lower lows = bearish."""
    swings = SwingDetector().detect(candles)
    highs = [s for s in swings if s.kind is SwingKind.HIGH]
    lows = [s for s in swings if s.kind is SwingKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return Bias.UNKNOWN
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price
    if higher_high and higher_low:
        return Bias.BULLISH
    if lower_high and lower_low:
        return Bias.BEARISH
    return Bias.NEUTRAL


def _range_position_bias(candles: list[Candle]) -> Bias:
    highest = max(c.high for c in candles)
    lowest = min(c.low for c in candles)
    if highest == lowest:
        return Bias.NEUTRAL
    position = (candles[-1].close - lowest) / (highest - lowest)
    if position >= 0.6:
        return Bias.BULLISH
    if position <= 0.4:
        return Bias.BEARISH
    return Bias.NEUTRAL


def compute_bias(candles: list[Candle], method: str | None = None) -> Bias:
    if not candles:
        return Bias.UNKNOWN
    lookback = int(STRATEGY_CONFIG["mtf_bias_lookback"])
    window = candles[-lookback:]
    chosen = (method or STRATEGY_CONFIG["mtf_bias_method"]).lower()
    if len(window) < 10:
        return Bias.UNKNOWN
    if chosen == "range_position":
        return _range_position_bias(window)
    if chosen == "swing_structure":
        return _swing_structure_bias(window)
    raise ValueError(f"Unknown mtf_bias_method: {chosen}")


class MtfFilter:
    """Evaluates the higher-timeframe filter for a (asset, timeframe) setup."""

    def __init__(self, candle_source) -> None:
        """`candle_source(asset, timeframe) -> list[Candle]`"""
        self._source = candle_source

    def biases(self, asset: str, timeframe: str) -> dict[str, Bias]:
        return {
            htf: compute_bias(self._source(asset, htf))
            for htf in higher_timeframes(timeframe)
        }

    def allows(self, asset: str, timeframe: str, direction: str) -> tuple[bool, dict[str, Bias]]:
        biases = self.biases(asset, timeframe)
        if not STRATEGY_CONFIG["use_mtf_filter"] or not biases:
            return True, biases
        required = Bias.BULLISH if direction.upper() in ("CALL", "UP", "W") else Bias.BEARISH
        allowed = all(bias is required for bias in biases.values())
        return allowed, biases
