from __future__ import annotations

from conftest import make_candle

from src.strategy.confirmation import confirm_m_entry, confirm_w_entry


# Reference candle is red for a W pullback: open/high near the top, close/low near the bottom.
def w_reference():
    return make_candle(1.1650, 1.1650, 1.16150, 1.1616)


# Reference candle is green for an M pullback: low=1.16420, high=1.1650.
# 50% threshold is 1.1646.
def m_reference():
    return make_candle(1.16420, 1.1650, 1.16420, 1.1650)


def test_w_entry_confirmed_at_full_break():
    green = make_candle(1.1616, 1.16580, 1.16150, 1.1650)
    result = confirm_w_entry(green, w_reference(), threshold=1.0)
    assert result.confirmed
    assert result.entry_price == 1.16580


def test_w_entry_confirmed_at_50_percent_retracement():
    # Midpoint of reference (1.1650, 1.16150) = 1.16325. Green high of 1.16330 confirms.
    green = make_candle(1.1616, 1.16330, 1.16150, 1.16310)
    result = confirm_w_entry(green, w_reference())
    assert result.confirmed
    assert result.reference_price == 1.16325


def test_w_entry_fails_when_green_does_not_reach_50_percent():
    green = make_candle(1.1616, 1.16320, 1.16150, 1.16300)
    result = confirm_w_entry(green, w_reference())
    assert result.failed
    assert "did not reach" in result.reason


def test_m_entry_confirmed_at_full_break():
    red = make_candle(1.1650, 1.16550, 1.16420, 1.1643)
    assert confirm_m_entry(red, m_reference(), threshold=1.0).confirmed


def test_m_entry_confirmed_at_50_percent_retracement():
    # 50% threshold of reference (1.16420, 1.1650) = 1.1646. Red low of 1.16455 confirms.
    red = make_candle(1.1650, 1.16550, 1.16455, 1.16460)
    result = confirm_m_entry(red, m_reference())
    assert result.confirmed
    assert result.reference_price == 1.1646


def test_m_entry_fails_when_red_does_not_reach_50_percent():
    red = make_candle(1.1650, 1.16550, 1.16465, 1.16470)
    assert confirm_m_entry(red, m_reference()).failed
