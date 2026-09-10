"""The trade must fire the moment price touches the entry level.

Waiting for the confirmation candle to close costs a full candle of the
expiry, which is enough to lose an otherwise correct signal. These tests pin
the intrabar trigger and the gates that still have to pass with it.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import M_STRUCTURE_CLOSES, W_STRUCTURE_CLOSES, Feeder, make_candle

from config.strategy import STRATEGY_CONFIG, TIMEFRAME_CONFIG
from src.data.models import Tick
from src.signals.generator import SignalEngine
from src.signals.models import SignalKind
from src.strategy.flat_candle import FlatCandleGuard
from src.strategy.state_machine import SetupStateMachine

# Red pullback candle #1 of the W fixture below: low 1.16100, high 1.16200.
W_ENTRY_LEVEL = 1.16150
# Green pullback candle #1 of the M fixture below: low 1.15840, high 1.15900.
M_ENTRY_LEVEL = 1.15870


def feed(machine: SetupStateMachine, candles: list) -> list:
    events: list = []
    for candle in candles:
        events.extend(machine.on_closed_candle(candle))
    return events


def w_machine_at_entry() -> tuple[SetupStateMachine, Feeder]:
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(60))
    feed(machine, feeder.add_closes(W_STRUCTURE_CLOSES))
    feed(machine, [feeder.add(1.1604, 1.16190, 1.16035, 1.1618)])  # P break
    feed(machine, [feeder.add(1.1618, 1.16200, 1.16100, 1.1612)])  # red #1
    feed(machine, [feeder.add(1.1612, 1.16130, 1.16050, 1.1608)])  # red #2
    return machine, feeder


def m_machine_at_entry() -> tuple[SetupStateMachine, Feeder]:
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(60))
    feed(machine, feeder.add_closes(M_STRUCTURE_CLOSES))
    feed(machine, [feeder.add(1.1596, 1.15965, 1.15840, 1.1585)])  # T break
    feed(machine, [feeder.add(1.1585, 1.15900, 1.15840, 1.1589)])  # green #1
    feed(machine, [feeder.add(1.1589, 1.15930, 1.15880, 1.1592)])  # green #2
    return machine, feeder


def partial_for(feeder: Feeder, open_: float, high: float, low: float, close: float):
    """A still-forming candle in the bucket after the last closed one."""
    return make_candle(open_, high, low, close, index=feeder.index, timeframe="5m")


def attach(machine: SetupStateMachine, partial) -> None:
    machine.partial_source = lambda asset, timeframe: partial


def tick_time(partial):
    return partial.timestamp + timedelta(seconds=30)


# ------------------------------------------------------------------ W / CALL
def test_call_fires_as_soon_as_price_touches_the_level(raw_candles):
    machine, feeder = w_machine_at_entry()
    partial = partial_for(feeder, 1.1608, W_ENTRY_LEVEL, 1.16070, W_ENTRY_LEVEL)
    attach(machine, partial)

    events = machine.on_tick(W_ENTRY_LEVEL, tick_time(partial))

    assert [e.kind for e in events] == [SignalKind.PHASE3_EXECUTION]
    setup = events[0].setup
    assert setup.entry_price == W_ENTRY_LEVEL
    assert setup.entry_confirmation_time == tick_time(partial)
    # The candle it fired inside has not closed yet.
    assert setup.entry_confirmation_time > machine.candles[-1].timestamp
    assert machine.active is None


def test_expiry_is_counted_from_the_moment_of_entry(raw_candles, monkeypatch):
    monkeypatch.setitem(TIMEFRAME_CONFIG["5m"], "expiration", "10m")
    machine, feeder = w_machine_at_entry()
    partial = partial_for(feeder, 1.1608, W_ENTRY_LEVEL, 1.16070, W_ENTRY_LEVEL)
    attach(machine, partial)

    moment = tick_time(partial)
    setup = machine.on_tick(W_ENTRY_LEVEL, moment)[0].setup
    assert setup.expiry_time == moment + timedelta(minutes=10)


def test_nothing_fires_before_the_level_is_reached(raw_candles):
    machine, feeder = w_machine_at_entry()
    below = W_ENTRY_LEVEL - 0.0002
    attach(machine, partial_for(feeder, 1.1608, below, 1.16070, below))

    assert machine.on_tick(below, tick_time(machine.candles[-1])) == []
    assert machine.active is not None


def test_a_red_forming_candle_is_not_a_confirmation_candle(raw_candles):
    """Price may spike through the level while the pullback is still going."""
    machine, feeder = w_machine_at_entry()
    # Opens above the level and is falling: still a pullback candle.
    partial = partial_for(feeder, 1.16180, 1.16200, 1.16070, 1.16080)
    attach(machine, partial)

    assert machine.on_tick(1.16080, tick_time(partial)) == []
    assert machine.active is not None


def test_the_trigger_only_fires_once(raw_candles):
    machine, feeder = w_machine_at_entry()
    partial = partial_for(feeder, 1.1608, W_ENTRY_LEVEL, 1.16070, W_ENTRY_LEVEL)
    attach(machine, partial)

    assert machine.on_tick(W_ENTRY_LEVEL, tick_time(partial))
    assert machine.on_tick(W_ENTRY_LEVEL, tick_time(partial)) == []


def test_a_pullback_shorter_than_the_minimum_cannot_trigger(raw_candles):
    machine = SetupStateMachine("EUR/USD", "5m", source="test")
    feeder = Feeder(1.1600)
    feed(machine, feeder.filler(60))
    feed(machine, feeder.add_closes(W_STRUCTURE_CLOSES))
    feed(machine, [feeder.add(1.1604, 1.16190, 1.16035, 1.1618)])  # P break
    feed(machine, [feeder.add(1.1618, 1.16200, 1.16100, 1.1612)])  # red #1 only
    attach(machine, partial_for(feeder, 1.1612, 1.16200, 1.16110, 1.16190))

    assert machine.on_tick(1.16190, tick_time(machine.candles[-1])) == []


def test_trigger_is_off_when_intrabar_execution_is_disabled(raw_candles, monkeypatch):
    monkeypatch.setitem(STRATEGY_CONFIG, "intrabar_execution", False)
    machine, feeder = w_machine_at_entry()
    partial = partial_for(feeder, 1.1608, W_ENTRY_LEVEL, 1.16070, W_ENTRY_LEVEL)
    attach(machine, partial)

    assert machine.on_tick(W_ENTRY_LEVEL, tick_time(partial)) == []
    assert machine.active is not None


def test_no_partial_source_means_no_intrabar_trigger(raw_candles):
    machine, _feeder = w_machine_at_entry()
    assert machine.partial_source is None
    assert machine.on_tick(W_ENTRY_LEVEL, tick_time(machine.candles[-1])) == []


# ------------------------------------------------------------------- M / PUT
def test_put_fires_as_soon_as_price_touches_the_level(raw_candles):
    machine, feeder = m_machine_at_entry()
    partial = partial_for(feeder, 1.1592, 1.15925, M_ENTRY_LEVEL, M_ENTRY_LEVEL)
    attach(machine, partial)

    events = machine.on_tick(M_ENTRY_LEVEL, tick_time(partial))

    assert [e.kind for e in events] == [SignalKind.PHASE3_EXECUTION]
    assert events[0].setup.entry_price == M_ENTRY_LEVEL


# ----------------------------------------------------- higher timeframe gates
FLAT_HISTORY = [make_candle(1.1600, 1.1600, 1.1600, 1.1600, index=i) for i in range(10)]


class Higher:
    """Closed history plus one forming candle per higher timeframe."""

    def __init__(self, partial) -> None:
        self._partial = partial

    def closed(self, asset: str, timeframe: str) -> list:
        return list(FLAT_HISTORY)

    def partial(self, asset: str, timeframe: str):
        return self._partial


def flat_bottom_15m():
    return make_candle(1.1600, 1.1610, 1.1600, 1.1608, index=10)


def wicked_15m():
    return make_candle(1.1600, 1.1610, 1.1595, 1.1608, index=10)


def test_intrabar_entry_still_requires_a_flat_higher_timeframe_candle(raw_candles):
    machine, feeder = w_machine_at_entry()
    source = Higher(wicked_15m())
    machine.flat_guard = FlatCandleGuard(source.closed, source.partial)
    partial = partial_for(feeder, 1.1608, W_ENTRY_LEVEL, 1.16070, W_ENTRY_LEVEL)
    attach(machine, partial)

    assert machine.on_tick(W_ENTRY_LEVEL, tick_time(partial)) == []
    # The setup is not killed intrabar: the closed candle rules decide that.
    assert machine.active is not None


def test_intrabar_entry_passes_with_a_flat_higher_timeframe_candle(raw_candles):
    machine, feeder = w_machine_at_entry()
    source = Higher(flat_bottom_15m())
    machine.flat_guard = FlatCandleGuard(source.closed, source.partial)
    partial = partial_for(feeder, 1.1608, W_ENTRY_LEVEL, 1.16070, W_ENTRY_LEVEL)
    attach(machine, partial)

    events = machine.on_tick(W_ENTRY_LEVEL, tick_time(partial))
    assert [e.kind for e in events] == [SignalKind.PHASE3_EXECUTION]
    assert events[0].setup.flat_candle_states["15m"] == "green, flat bottom"


# ------------------------------------------------------------------- wiring
def test_engine_hands_the_partial_source_to_every_machine():
    engine = SignalEngine(assets=["EUR/USD"], timeframes=["5m"], source="test")
    partials = {}
    engine.set_partial_source(lambda asset, timeframe: partials.get(timeframe))
    machine = engine.machines[("EUR/USD", "5m")]
    assert machine.partial_source is not None
    assert machine.flat_guard is not None


def test_engine_on_tick_emits_the_execution_event(raw_candles):
    engine = SignalEngine(assets=["EUR/USD"], timeframes=["5m"], source="test")
    machine = engine.machines[("EUR/USD", "5m")]
    machine.mtf_filter = None
    built, feeder = w_machine_at_entry()
    for candle in feeder.candles:
        engine.on_closed_candle(candle)

    partial = partial_for(feeder, 1.1608, W_ENTRY_LEVEL, 1.16070, W_ENTRY_LEVEL)
    engine.set_partial_source(lambda asset, timeframe: partial)
    # The higher timeframe gates have their own tests; this one is about the
    # tick reaching the machine at all.
    machine.flat_guard = None

    events = engine.on_tick(
        Tick(asset="EUR/USD", timestamp=tick_time(partial), price=W_ENTRY_LEVEL)
    )
    assert [e.kind for e in events] == [SignalKind.PHASE3_EXECUTION]
