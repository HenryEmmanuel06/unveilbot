from __future__ import annotations

from conftest import Feeder

from src.strategy.swing_detector import SwingDetector, SwingKind


def test_detects_alternating_swings():
    feeder = Feeder(1.1600)
    feeder.add_closes([1.1600, 1.1598, 1.1590, 1.1595, 1.1600, 1.1610, 1.1612, 1.1608, 1.1600])
    swings = SwingDetector().detect(feeder.candles)
    kinds = [s.kind for s in swings]
    assert SwingKind.LOW in kinds
    assert SwingKind.HIGH in kinds
    for previous, current in zip(swings, swings[1:]):
        assert previous.kind is not current.kind


def test_tiny_fluctuations_are_filtered():
    feeder = Feeder(1.1600)
    feeder.filler(40)
    swings = SwingDetector(lookback=2, min_swing_size=0.0002).detect(feeder.candles)
    assert len(swings) <= 1


def test_lookback_is_configurable():
    feeder = Feeder(1.1600)
    feeder.add_closes([1.1600, 1.1590, 1.1600, 1.1610, 1.1600])
    assert SwingDetector(lookback=1, min_swing_size=0.0).detect(feeder.candles)
    assert SwingDetector(lookback=10, min_swing_size=0.0).detect(feeder.candles) == []
