"""50% retracement mathematics and tolerance checks (README 15, 17, 23, 25)."""

from __future__ import annotations

from config.strategy import STRATEGY_CONFIG


def w_retracement_level(l1: float, p: float, ratio: float | None = None) -> float:
    """Price level that price must reach (or go below) for a valid W."""
    r = float(STRATEGY_CONFIG["minimum_retracement"] if ratio is None else ratio)
    return l1 + ((p - l1) * r)


def m_retracement_level(h1: float, t: float, ratio: float | None = None) -> float:
    """Price level that price must reach (or exceed) for a valid M."""
    r = float(STRATEGY_CONFIG["minimum_retracement"] if ratio is None else ratio)
    return h1 - ((h1 - t) * r)


def w_retracement_ok(l1: float, p: float, l2: float, ratio: float | None = None) -> bool:
    if p <= l1:
        return False
    return l2 <= w_retracement_level(l1, p, ratio) and l2 > l1


def m_retracement_ok(h1: float, t: float, h2: float, ratio: float | None = None) -> bool:
    if t >= h1:
        return False
    return h2 >= m_retracement_level(h1, t, ratio) and h2 < h1


def retracement_fraction_w(l1: float, p: float, l2: float) -> float:
    if p == l1:
        return 0.0
    return (p - l2) / (p - l1)


def retracement_fraction_m(h1: float, t: float, h2: float) -> float:
    if h1 == t:
        return 0.0
    return (h2 - t) / (h1 - t)


def bottom_tolerance_ok(l1: float, l2: float, tolerance: float | None = None) -> bool:
    tol = float(STRATEGY_CONFIG["w_bottom_tolerance"] if tolerance is None else tolerance)
    reference = abs(l1) or 1.0
    return abs(l2 - l1) / reference <= tol


def top_tolerance_ok(h1: float, h2: float, tolerance: float | None = None) -> bool:
    tol = float(STRATEGY_CONFIG["m_top_tolerance"] if tolerance is None else tolerance)
    reference = abs(h1) or 1.0
    return abs(h2 - h1) / reference <= tol


def peak_depth_ok(l1: float, p: float, minimum: float | None = None) -> bool:
    min_depth = float(STRATEGY_CONFIG["min_w_peak_depth"] if minimum is None else minimum)
    reference = abs(l1) or 1.0
    return (p - l1) / reference >= min_depth


def trough_depth_ok(h1: float, t: float, minimum: float | None = None) -> bool:
    min_depth = float(STRATEGY_CONFIG["min_m_trough_depth"] if minimum is None else minimum)
    reference = abs(h1) or 1.0
    return (h1 - t) / reference >= min_depth
