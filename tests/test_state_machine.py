from __future__ import annotations

import pytest
from conftest import M_STRUCTURE_CLOSES, W_STRUCTURE_CLOSES, Feeder

from src.signals.models import Phase, SetupStatus, SignalKind
from src.strategy.state_machine import SetupStateMachine


@pytest.fixture(autouse=True)
def _plain_candles(raw_candles) -> None:
    """This module specifies the base W/M rules on plain OHLC candles.

    Heikin Ashi behaviour is covered in `test_heikin_ashi.py`.
    """


def feed(machine: SetupStateMachine, candles: list) -> list:
    events: list = []
    for candle in candles:
        events.extend(machine.on_closed_candle(candle))
    return events


def start_w() -> tuple[SetupStateMachine, Feeder, list]:
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    events = feed(machine, feeder.filler(60))
    events += feed(machine, feeder.add_closes(W_STRUCTURE_CLOSES))
    return machine, feeder, events


def start_m() -> tuple[SetupStateMachine, Feeder, list]:
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    events = feed(machine, feeder.filler(60))
    events += feed(machine, feeder.add_closes(M_STRUCTURE_CLOSES))
    return machine, feeder, events


def test_phase1_is_emitted_only_after_p_break():
    machine, feeder, events = start_w()
    # Structure detection alone does not emit a signal.
    phase1 = [e for e in events if e.kind is SignalKind.PHASE1_PATTERN]
    assert phase1 == []
    assert machine.active.pattern_confirmation_time is None

    events = feed(machine, [feeder.add(1.1604, 1.16190, 1.16035, 1.1618)])
    phase1 = [e for e in events if e.kind is SignalKind.PHASE1_PATTERN]
    assert len(phase1) == 1
    setup = phase1[0].setup
    assert setup.phase is Phase.PATTERN_SENT
    assert setup.pattern_confirmation_time is not None
    assert setup.state == "WAITING_FOR_PULLBACK"


def test_full_w_lifecycle_produces_three_phases():
    machine, feeder, events = start_w()
    events += feed(machine, [feeder.add(1.1604, 1.16190, 1.16035, 1.1618)])  # P break
    assert machine.active.pattern_confirmation_time is not None

    events += feed(machine, [feeder.add(1.1618, 1.16200, 1.16100, 1.1612)])  # red #1
    events += feed(machine, [feeder.add(1.1612, 1.16130, 1.16050, 1.1608)])  # red #2
    phase2 = [e for e in events if e.kind is SignalKind.PHASE2_PULLBACK]
    assert len(phase2) == 1
    assert phase2[0].setup.reference_price == 1.16200

    events += feed(machine, [feeder.add(1.1608, 1.16210, 1.16070, 1.1620)])  # green entry
    phase3 = [e for e in events if e.kind is SignalKind.PHASE3_EXECUTION]
    assert len(phase3) == 1
    setup = phase3[0].setup
    assert setup.status is SetupStatus.EXECUTED
    assert setup.direction.value == "CALL / UP"
    assert setup.entry_price == 1.16210
    assert setup.expiry_time is not None
    assert machine.active is None


def test_fourth_red_candle_invalidates_the_setup():
    machine, feeder, events = start_w()
    feed(machine, [feeder.add(1.1604, 1.16190, 1.16035, 1.1618)])
    events = feed(machine, [feeder.add(1.1618, 1.16200, 1.16100, 1.1612)])
    events += feed(machine, [feeder.add(1.1612, 1.16130, 1.16050, 1.1608)])
    events += feed(machine, [feeder.add(1.1608, 1.16090, 1.16010, 1.1604)])
    events += feed(machine, [feeder.add(1.1604, 1.16050, 1.15970, 1.1600)])
    invalid = [e for e in events if e.kind is SignalKind.INVALIDATED]
    assert invalid
    assert "4th red" in invalid[-1].detail
    assert machine.active is None


def test_green_that_fails_reference_high_invalidates_permanently():
    machine, feeder, events = start_w()
    feed(machine, [feeder.add(1.1604, 1.16190, 1.16035, 1.1618)])
    feed(machine, [feeder.add(1.1618, 1.16250, 1.16100, 1.1612)])  # red #1 high 1.16250
    events = feed(machine, [feeder.add(1.1612, 1.16130, 1.16050, 1.1608)])  # red #2
    events += feed(machine, [feeder.add(1.1608, 1.16150, 1.16070, 1.1614)])  # green too low
    invalid = [e for e in events if e.kind is SignalKind.INVALIDATED]
    assert invalid
    assert "did not reach" in invalid[-1].detail
    assert machine.active is None


def test_l1_break_before_confirmation_invalidates():
    machine, feeder, events = start_w()
    events = feed(machine, [feeder.add(1.1604, 1.16050, 1.15800, 1.1582)])
    invalid = [e for e in events if e.kind is SignalKind.INVALIDATED]
    assert invalid and "L1 broken" in invalid[0].detail
    assert machine.active is None


def test_full_m_lifecycle_produces_put_signal():
    machine, feeder, events = start_m()
    # PHASE1 comes after T break, not after structure detection.
    assert not [e for e in events if e.kind is SignalKind.PHASE1_PATTERN]
    events += feed(machine, [feeder.add(1.1596, 1.15965, 1.15840, 1.1585)])  # T break
    assert [e for e in events if e.kind is SignalKind.PHASE1_PATTERN]
    assert machine.active.pattern_confirmation_time is not None

    events += feed(machine, [feeder.add(1.1585, 1.15900, 1.15840, 1.1589)])  # green #1
    events += feed(machine, [feeder.add(1.1589, 1.15930, 1.15880, 1.1592)])  # green #2
    phase2 = [e for e in events if e.kind is SignalKind.PHASE2_PULLBACK]
    assert len(phase2) == 1
    assert phase2[0].setup.reference_price == 1.15840

    events += feed(machine, [feeder.add(1.1592, 1.15925, 1.15830, 1.1584)])  # red entry
    phase3 = [e for e in events if e.kind is SignalKind.PHASE3_EXECUTION]
    assert len(phase3) == 1
    assert phase3[0].setup.direction.value == "PUT / DOWN"
    assert phase3[0].setup.entry_price == 1.15830


def test_setup_expires_when_center_break_never_happens():
    machine, feeder, _ = start_w()
    for _ in range(45):
        feeder.add(1.1604, 1.16045, 1.16020, 1.1603)
    events = feed(machine, feeder.candles[-45:])
    assert [e for e in events if e.kind is SignalKind.EXPIRED]


def test_duplicate_candles_are_ignored():
    machine = SetupStateMachine("EUR/USD", "5m")
    feeder = Feeder(1.1600)
    candles = feeder.filler(10)
    feed(machine, candles)
    before = len(machine.candles)
    feed(machine, [candles[-1]])
    assert len(machine.candles) == before
