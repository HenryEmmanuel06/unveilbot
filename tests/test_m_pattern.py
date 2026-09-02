from __future__ import annotations

from conftest import M_STRUCTURE_CLOSES, Feeder, make_candle

from src.strategy.m_pattern import detect_m_structure, evaluate_m_structure, t_broken
from src.strategy.swing_detector import Swing, SwingKind


def build_m() -> Feeder:
    feeder = Feeder(1.1600)
    feeder.filler(60)
    feeder.add_closes(M_STRUCTURE_CLOSES)
    return feeder


def test_detects_valid_m_structure():
    structure = detect_m_structure(build_m().candles)
    assert structure is not None
    assert structure.valid
    assert structure.t < structure.h2 < structure.h1
    assert structure.checks["retracement_50"]
    assert structure.checks["h1_protection"]
    assert structure.checks["h2_tolerance"]


def test_h2_above_h1_is_invalid():
    feeder = Feeder(1.1600)
    feeder.filler(60)
    closes = list(M_STRUCTURE_CLOSES)
    closes[9] = 1.1625  # H2 breaks above H1
    feeder.add_closes(closes)
    assert detect_m_structure(feeder.candles) is None


def test_shallow_retracement_is_invalid():
    feeder = Feeder(1.1600)
    feeder.filler(60)
    closes = list(M_STRUCTURE_CLOSES)
    closes[8] = 1.1591
    closes[9] = 1.1593
    feeder.add_closes(closes)
    assert detect_m_structure(feeder.candles) is None


def test_structure_checks_report_individual_failures():
    candles = [
        make_candle(1.1600, 1.16100, 1.15995, 1.1605, index=0),
        make_candle(1.1605, 1.16060, 1.15880, 1.1590, index=1),
        make_candle(1.1590, 1.16150, 1.15895, 1.1610, index=2),
    ]
    h1 = Swing(0, candles[0].timestamp, 1.16100, SwingKind.HIGH)
    t = Swing(1, candles[1].timestamp, 1.15880, SwingKind.LOW)
    h2 = Swing(2, candles[2].timestamp, 1.16150, SwingKind.HIGH)
    structure = evaluate_m_structure(candles, h1, t, h2)
    assert not structure.valid
    assert "h2_below_h1" in structure.failures


def test_t_break_requires_close_below_t():
    above = make_candle(1.1590, 1.15920, 1.15870, 1.1588)
    below = make_candle(1.1588, 1.15890, 1.15840, 1.1585)
    assert not t_broken(above, 1.15878)
    assert t_broken(below, 1.15878)
