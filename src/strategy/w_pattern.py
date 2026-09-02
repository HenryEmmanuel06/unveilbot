"""W (double bottom) structure detection - README 11-18."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from config.strategy import STRATEGY_CONFIG
from src.data.models import Candle
from src.strategy.retracement import (
    bottom_tolerance_ok,
    peak_depth_ok,
    retracement_fraction_w,
    w_retracement_level,
    w_retracement_ok,
)
from src.strategy.swing_detector import Swing, SwingDetector, SwingKind


@dataclass
class WStructure:
    l1: float
    p: float
    l2: float
    l1_index: int
    p_index: int
    l2_index: int
    l1_time: datetime
    p_time: datetime
    l2_time: datetime
    retracement_level: float
    retracement_fraction: float
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return all(self.checks.values())

    @property
    def failures(self) -> list[str]:
        return [name for name, ok in self.checks.items() if not ok]

    def fingerprint(self) -> tuple:
        return ("W", self.l1_time, self.p_time, self.l2_time)


def evaluate_w_structure(
    candles: list[Candle], l1: Swing, p: Swing, l2: Swing
) -> WStructure:
    """Applies every W structural rule and records the individual results."""
    segment_after_l1 = candles[l1.index : l2.index + 1]
    l1_protected = all(c.low >= l1.price for c in segment_after_l1)

    checks = {
        "order": l1.index < p.index < l2.index,
        "peak_depth": peak_depth_ok(l1.price, p.price),
        "retracement_50": w_retracement_ok(l1.price, p.price, l2.price),
        "l1_protection": l1_protected,
        "l2_above_l1": l2.price > l1.price,
        "l2_tolerance": bottom_tolerance_ok(l1.price, l2.price),
    }
    return WStructure(
        l1=l1.price,
        p=p.price,
        l2=l2.price,
        l1_index=l1.index,
        p_index=p.index,
        l2_index=l2.index,
        l1_time=l1.timestamp,
        p_time=p.timestamp,
        l2_time=l2.timestamp,
        retracement_level=w_retracement_level(l1.price, p.price),
        retracement_fraction=retracement_fraction_w(l1.price, p.price, l2.price),
        checks=checks,
    )


def detect_w_structure(
    candles: list[Candle], detector: SwingDetector | None = None
) -> WStructure | None:
    """Returns the most recent valid, still-unconfirmed W structure."""
    detector = detector or SwingDetector()
    lookback = int(STRATEGY_CONFIG["structure_lookback_candles"])
    window = candles[-lookback:] if lookback and len(candles) > lookback else candles
    swings = detector.detect(window)
    if len(swings) < 3:
        return None

    for i in range(len(swings) - 1, 1, -1):
        l2 = swings[i]
        p = swings[i - 1]
        l1 = swings[i - 2]
        if not (l2.kind is SwingKind.LOW and p.kind is SwingKind.HIGH and l1.kind is SwingKind.LOW):
            continue
        structure = evaluate_w_structure(window, l1, p, l2)
        if not structure.valid:
            continue
        # The P breakout must still be pending, otherwise Phase 1 would be
        # emitted after the confirmation already happened.
        after_l2 = window[l2.index + 1 :]
        if any(c.close > p.price for c in after_l2):
            continue
        if any(c.low < l1.price for c in after_l2):
            continue
        return structure
    return None


def p_broken(candle: Candle, p: float) -> bool:
    """The candle that closes above P confirms the W (README 18)."""
    if not STRATEGY_CONFIG["require_center_break"]:
        return True
    return candle.close > p
