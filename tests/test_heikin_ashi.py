from __future__ import annotations

from conftest import W_STRUCTURE_CLOSES, Feeder, make_candle

from config.strategy import STRATEGY_CONFIG
from src.signals.models import Pattern, Setup, SetupStatus, build_setup_id
from src.strategy.heikin_ashi import (
    HeikinAshiConverter,
    heikin_ashi_candle,
    heikin_ashi_series,
)
from src.strategy.pullback import PullbackEvent
from src.strategy.state_machine import SetupStateMachine


def feed(machine: SetupStateMachine, candles: list) -> list:
    events: list = []
    for candle in candles:
        events.extend(machine.on_closed_candle(candle))
    return events


# ------------------------------------------------------------------ formulas
def test_first_candle_seeds_open_from_open_and_close():
    candle = make_candle(1.1600, 1.1620, 1.1590, 1.1610)
    ha = heikin_ashi_candle(candle)
    assert ha.close == (1.1600 + 1.1620 + 1.1590 + 1.1610) / 4
    assert ha.open == (1.1600 + 1.1610) / 2


def test_subsequent_open_is_midpoint_of_previous_heikin_ashi_body():
    first = heikin_ashi_candle(make_candle(1.1600, 1.1620, 1.1590, 1.1610, index=0))
    second = heikin_ashi_candle(make_candle(1.1610, 1.1630, 1.1605, 1.1625, index=1), first)
    assert second.open == (first.open + first.close) / 2


def test_high_and_low_envelop_the_body():
    candles = [
        make_candle(1.1600, 1.1620, 1.1590, 1.1610, index=0),
        make_candle(1.1610, 1.1615, 1.1560, 1.1570, index=1),
    ]
    for ha in heikin_ashi_series(candles):
        assert ha.high >= max(ha.open, ha.close)
        assert ha.low <= min(ha.open, ha.close)


def test_series_preserves_timestamps_and_length():
    feeder = Feeder(1.1600)
    candles = feeder.add_closes(W_STRUCTURE_CLOSES)
    series = heikin_ashi_series(candles)
    assert len(series) == len(candles)
    assert [c.timestamp for c in series] == [c.timestamp for c in candles]


def test_converter_matches_series():
    feeder = Feeder(1.1600)
    candles = feeder.add_closes(W_STRUCTURE_CLOSES)
    converter = HeikinAshiConverter()
    rolling = [converter.add(candle) for candle in candles]
    assert rolling == heikin_ashi_series(candles)


def test_smoothing_removes_a_single_noise_candle():
    """A lone counter-candle inside a downtrend stays red in Heikin Ashi."""
    downtrend = [
        make_candle(1.1600, 1.16005, 1.15900, 1.1591, index=0),
        make_candle(1.1591, 1.15915, 1.15800, 1.1581, index=1),
        # Real candle closes above its open -> green, but the bar is still
        # deep inside the falling range.
        make_candle(1.1581, 1.15830, 1.15750, 1.1582, index=2),
        make_candle(1.1582, 1.15825, 1.15700, 1.1571, index=3),
    ]
    assert downtrend[2].is_green
    series = heikin_ashi_series(downtrend)
    assert series[2].is_red


# -------------------------------------------------------- state machine wiring
def test_machine_keeps_a_heikin_ashi_mirror_of_every_candle():
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    candles = feeder.filler(10)
    feed(machine, candles)

    assert len(machine.ha_candles) == len(machine.candles)
    assert machine.ha_candles == heikin_ashi_series(machine.candles)


def test_seeded_history_is_converted_too():
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    machine.seed(feeder.filler(30))
    assert machine.ha_candles == heikin_ashi_series(machine.candles)


def test_trim_keeps_both_series_aligned():
    machine = SetupStateMachine("EUR/USD", "5m", source="test", history_size=20)
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(50))
    assert len(machine.candles) == 20
    assert len(machine.ha_candles) == 20
    assert [c.timestamp for c in machine.ha_candles] == [
        c.timestamp for c in machine.candles
    ]


def test_heikin_ashi_is_enabled_by_default_for_pullbacks():
    assert STRATEGY_CONFIG["use_heikin_ashi"] is True
    assert STRATEGY_CONFIG["heikin_ashi_scope"] in ("pullback", "all")


class RecordingTracker:
    """Stands in for `PullbackTracker` to capture what the machine feeds it."""

    def __init__(self) -> None:
        self.seen: list = []
        self.count = 0
        self.reference_price = None

    def add(self, candle):
        self.seen.append(candle)
        return PullbackEvent.WAITING


def _confirmed_setup(machine: SetupStateMachine) -> Setup:
    """A confirmed setup so `_advance` reaches the pullback tracker."""
    last = machine.candles[-1]
    setup = Setup(
        setup_id=build_setup_id(machine.asset, machine.timeframe, Pattern.W, last.timestamp),
        asset=machine.asset,
        timeframe=machine.timeframe,
        pattern=Pattern.W,
        level_1=last.low - 0.01,
        level_mid=last.high - 0.005,
        level_2=last.low - 0.005,
        retracement_level=last.low,
        retracement_fraction=0.6,
        structure_checks={},
        source=machine.source,
    )
    setup.pattern_confirmation_time = last.timestamp
    setup.status = SetupStatus.CONFIRMED
    return setup


def test_pullback_tracker_receives_the_heikin_ashi_candle():
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(60))

    machine.active = _confirmed_setup(machine)
    tracker = RecordingTracker()
    machine._tracker = tracker

    raw = feeder.add(1.1600, 1.16070, 1.15970, 1.1604)
    feed(machine, [raw])

    assert tracker.seen, "pullback tracker never received a candle"
    assert tracker.seen[-1] == machine.ha_candles[-1]
    assert tracker.seen[-1] != raw


def test_pullback_tracker_receives_the_raw_candle_when_disabled(raw_candles):
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(60))

    machine.active = _confirmed_setup(machine)
    tracker = RecordingTracker()
    machine._tracker = tracker

    raw = feeder.add(1.1600, 1.16070, 1.15970, 1.1604)
    feed(machine, [raw])

    assert tracker.seen[-1] == raw


def test_scope_all_detects_structure_on_heikin_ashi(heikin_ashi_everywhere):
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(60))
    assert machine._structure_series is machine.ha_candles


def test_scope_pullback_keeps_structure_on_real_candles():
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(60))
    assert machine._structure_series is machine.candles
