from __future__ import annotations

from conftest import make_candle

from src.strategy.pullback import PullbackEvent, PullbackTracker


def red(i: int) -> object:
    return make_candle(1.1620, 1.16210, 1.16150, 1.1616, index=i)


def green(i: int, high: float = 1.16250) -> object:
    return make_candle(1.1616, high, 1.16150, 1.1624, index=i)


def test_two_red_candles_make_a_valid_w_pullback():
    tracker = PullbackTracker("W")
    assert tracker.add(red(0)) is PullbackEvent.STARTED
    assert tracker.add(red(1)) is PullbackEvent.VALID
    assert tracker.count == 2
    assert tracker.reference_price == 1.16210


def test_three_red_candles_are_still_valid():
    tracker = PullbackTracker("W")
    tracker.add(red(0))
    tracker.add(red(1))
    assert tracker.add(red(2)) is PullbackEvent.CONTINUED
    assert tracker.count == 3


def test_fourth_red_candle_invalidates():
    tracker = PullbackTracker("W")
    for i in range(3):
        tracker.add(red(i))
    assert tracker.add(red(3)) is PullbackEvent.INVALID_TOO_MANY


def test_single_red_then_green_is_too_short():
    tracker = PullbackTracker("W")
    tracker.add(red(0))
    assert tracker.add(green(1)) is PullbackEvent.INVALID_TOO_SHORT


def test_reference_is_first_pullback_candle_high():
    tracker = PullbackTracker("W")
    tracker.add(make_candle(1.1620, 1.16300, 1.16150, 1.1616, index=0))
    tracker.add(make_candle(1.1616, 1.16200, 1.16100, 1.1612, index=1))
    assert tracker.reference_price == 1.16300


def test_m_pullback_uses_green_candles_and_first_low():
    tracker = PullbackTracker("M")
    first = make_candle(1.1600, 1.16100, 1.15990, 1.1608, index=0)
    second = make_candle(1.1608, 1.16150, 1.16050, 1.1612, index=1)
    assert tracker.add(first) is PullbackEvent.STARTED
    assert tracker.add(second) is PullbackEvent.VALID
    assert tracker.reference_price == 1.15990


def test_doji_does_not_count_by_default():
    tracker = PullbackTracker("W")
    tracker.add(red(0))
    doji = make_candle(1.1616, 1.16180, 1.16140, 1.1616, index=1)
    assert tracker.add(doji) is PullbackEvent.IGNORED_DOJI
    assert tracker.count == 1
