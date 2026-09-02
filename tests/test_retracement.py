from __future__ import annotations

from src.strategy.retracement import (
    bottom_tolerance_ok,
    m_retracement_level,
    m_retracement_ok,
    peak_depth_ok,
    top_tolerance_ok,
    w_retracement_level,
    w_retracement_ok,
)


def test_w_retracement_level_matches_readme_example():
    assert w_retracement_level(100, 120) == 110


def test_m_retracement_level_matches_readme_example():
    assert m_retracement_level(120, 100) == 110


def test_valid_w_retracement_range():
    assert w_retracement_ok(100, 120, 110)
    assert w_retracement_ok(100, 120, 105)
    assert not w_retracement_ok(100, 120, 111)  # less than 50% retracement
    assert not w_retracement_ok(100, 120, 100)  # L2 must stay above L1
    assert not w_retracement_ok(100, 120, 99)   # L1 broken


def test_valid_m_retracement_range():
    assert m_retracement_ok(120, 100, 110)
    assert m_retracement_ok(120, 100, 115)
    assert not m_retracement_ok(120, 100, 109)
    assert not m_retracement_ok(120, 100, 120)
    assert not m_retracement_ok(120, 100, 121)


def test_tolerances_are_configurable():
    assert bottom_tolerance_ok(1.1600, 1.1615, tolerance=0.002)
    assert not bottom_tolerance_ok(1.1600, 1.1900, tolerance=0.002)
    assert top_tolerance_ok(1.1600, 1.1590, tolerance=0.002)
    assert not top_tolerance_ok(1.1600, 1.1300, tolerance=0.002)


def test_peak_depth_filter():
    assert peak_depth_ok(1.1600, 1.1612, minimum=0.0004)
    assert not peak_depth_ok(1.1600, 1.16005, minimum=0.0004)
