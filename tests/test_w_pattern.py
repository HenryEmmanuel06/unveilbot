from __future__ import annotations

from conftest import W_STRUCTURE_CLOSES, Feeder, make_candle

from src.strategy.swing_detector import Swing, SwingKind
from src.strategy.w_pattern import detect_w_structure, evaluate_w_structure, p_broken


def build_w() -> Feeder:
    feeder = Feeder(1.1600)
    feeder.filler(60)
    feeder.add_closes(W_STRUCTURE_CLOSES)
    return feeder


def test_detects_valid_w_structure():
    structure = detect_w_structure(build_w().candles)
    assert structure is not None
    assert structure.valid
    assert structure.l1 < structure.l2 < structure.p
    assert structure.checks["retracement_50"]
    assert structure.checks["l1_protection"]
    assert structure.checks["l2_tolerance"]


def test_l2_below_l1_is_invalid():
    feeder = Feeder(1.1600)
    feeder.filler(60)
    closes = list(W_STRUCTURE_CLOSES)
    closes[9] = 1.1585  # L2 breaks below L1
    feeder.add_closes(closes)
    assert detect_w_structure(feeder.candles) is None


def test_shallow_retracement_is_invalid():
    feeder = Feeder(1.1600)
    feeder.filler(60)
    closes = list(W_STRUCTURE_CLOSES)
    closes[8] = 1.1608
    closes[9] = 1.1606  # only a small retracement
    feeder.add_closes(closes)
    assert detect_w_structure(feeder.candles) is None


def test_structure_checks_report_individual_failures():
    candles = [
        make_candle(1.1600, 1.16005, 1.15900, 1.1595, index=0),
        make_candle(1.1595, 1.16130, 1.15940, 1.1612, index=1),
        make_candle(1.1612, 1.16130, 1.15850, 1.1590, index=2),
    ]
    l1 = Swing(0, candles[0].timestamp, 1.15900, SwingKind.LOW)
    p = Swing(1, candles[1].timestamp, 1.16130, SwingKind.HIGH)
    l2 = Swing(2, candles[2].timestamp, 1.15850, SwingKind.LOW)
    structure = evaluate_w_structure(candles, l1, p, l2)
    assert not structure.valid
    assert "l2_above_l1" in structure.failures


def test_p_break_requires_close_above_p():
    below = make_candle(1.1610, 1.16125, 1.16080, 1.1612)
    above = make_candle(1.1612, 1.16160, 1.16110, 1.1615)
    assert not p_broken(below, 1.16122)
    assert p_broken(above, 1.16122)


def test_structure_is_not_returned_after_p_already_broken():
    feeder = build_w()
    feeder.add_closes([1.1620])
    assert detect_w_structure(feeder.candles) is None
