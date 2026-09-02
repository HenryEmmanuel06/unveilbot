"""M (double top) structure detection - README 19-26. Exact inverse of the W."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from config.strategy import STRATEGY_CONFIG
from src.data.models import Candle
from src.strategy.retracement import (
    m_retracement_level,
    m_retracement_ok,
    retracement_fraction_m,
    top_tolerance_ok,
    trough_depth_ok,
)
from src.strategy.swing_detector import Swing, SwingDetector, SwingKind


@dataclass
class MStructure:
    h1: float
    t: float
    h2: float
    h1_index: int
    t_index: int
    h2_index: int
    h1_time: datetime
    t_time: datetime
    h2_time: datetime
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
        return ("M", self.h1_time, self.t_time, self.h2_time)


def evaluate_m_structure(
    candles: list[Candle], h1: Swing, t: Swing, h2: Swing
) -> MStructure:
    segment = candles[h1.index : h2.index + 1]
    h1_protected = all(c.high <= h1.price for c in segment)

    checks = {
        "order": h1.index < t.index < h2.index,
        "trough_depth": trough_depth_ok(h1.price, t.price),
        "retracement_50": m_retracement_ok(h1.price, t.price, h2.price),
        "h1_protection": h1_protected,
        "h2_below_h1": h2.price < h1.price,
        "h2_tolerance": top_tolerance_ok(h1.price, h2.price),
    }
    return MStructure(
        h1=h1.price,
        t=t.price,
        h2=h2.price,
        h1_index=h1.index,
        t_index=t.index,
        h2_index=h2.index,
        h1_time=h1.timestamp,
        t_time=t.timestamp,
        h2_time=h2.timestamp,
        retracement_level=m_retracement_level(h1.price, t.price),
        retracement_fraction=retracement_fraction_m(h1.price, t.price, h2.price),
        checks=checks,
    )


def detect_m_structure(
    candles: list[Candle], detector: SwingDetector | None = None
) -> MStructure | None:
    detector = detector or SwingDetector()
    lookback = int(STRATEGY_CONFIG["structure_lookback_candles"])
    window = candles[-lookback:] if lookback and len(candles) > lookback else candles
    swings = detector.detect(window)
    if len(swings) < 3:
        return None

    for i in range(len(swings) - 1, 1, -1):
        h2 = swings[i]
        t = swings[i - 1]
        h1 = swings[i - 2]
        if not (h2.kind is SwingKind.HIGH and t.kind is SwingKind.LOW and h1.kind is SwingKind.HIGH):
            continue
        structure = evaluate_m_structure(window, h1, t, h2)
        if not structure.valid:
            continue
        after_h2 = window[h2.index + 1 :]
        if any(c.close < t.price for c in after_h2):
            continue
        if any(c.high > h1.price for c in after_h2):
            continue
        return structure
    return None


def t_broken(candle: Candle, t: float) -> bool:
    """The candle that closes below T confirms the M (README 26)."""
    if not STRATEGY_CONFIG["require_center_break"]:
        return True
    return candle.close < t
