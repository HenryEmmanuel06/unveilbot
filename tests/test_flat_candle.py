from __future__ import annotations

import pytest
from conftest import M_STRUCTURE_CLOSES, W_STRUCTURE_CLOSES, Feeder, make_candle

from config.strategy import (
    STRATEGY_CONFIG,
    flat_candle_timeframes,
    higher_timeframes,
)
from src.signals.generator import SignalEngine
from src.signals.models import SignalKind
from src.strategy.flat_candle import (
    FlatCandleGuard,
    describe,
    forming_heikin_ashi,
    is_flat_candle,
)
from src.strategy.state_machine import SetupStateMachine


# ------------------------------------------------------ higher timeframe wiring
def test_five_minute_now_uses_the_fifteen_minute_bias():
    assert higher_timeframes("5m") == ["15m"]


def test_thirty_second_and_one_minute_bias_are_unchanged():
    assert higher_timeframes("30s") == ["1m", "5m"]
    assert higher_timeframes("1m") == ["5m"]


def test_flat_candle_timeframes_match_the_expiry_ladder():
    assert flat_candle_timeframes("30s") == ["1m", "5m"]
    assert flat_candle_timeframes("1m") == ["5m"]
    assert flat_candle_timeframes("5m") == ["15m"]


def test_flat_candle_timeframes_are_empty_when_disabled(monkeypatch):
    monkeypatch.setitem(STRATEGY_CONFIG, "use_flat_candle_filter", False)
    assert flat_candle_timeframes("30s") == []


def test_engine_streams_every_timeframe_the_gates_need():
    engine = SignalEngine(assets=["EUR/USD"], timeframes=["30s", "1m", "5m"], source="test")
    required = engine.required_timeframes
    for tf in ("30s", "1m", "5m", "15m"):
        assert tf in required


# ------------------------------------------------------------- flat candle test
def flat_bottom_green():
    # open == low, so there is no wick below the open.
    return make_candle(1.1600, 1.1610, 1.1600, 1.1608)


def wicked_green():
    return make_candle(1.1600, 1.1610, 1.1595, 1.1608)


def flat_top_red():
    # open == high, so there is no wick above the open.
    return make_candle(1.1610, 1.1610, 1.1600, 1.1602)


def wicked_red():
    return make_candle(1.1610, 1.1615, 1.1600, 1.1602)


def test_green_flat_bottom_confirms_a_w_trade():
    assert is_flat_candle(flat_bottom_green(), "W")


def test_green_with_a_lower_wick_blocks_a_w_trade():
    assert not is_flat_candle(wicked_green(), "W")


def test_red_candle_never_confirms_a_w_trade():
    assert not is_flat_candle(flat_top_red(), "W")


def test_red_flat_top_confirms_an_m_trade():
    assert is_flat_candle(flat_top_red(), "M")


def test_red_with_an_upper_wick_blocks_an_m_trade():
    assert not is_flat_candle(wicked_red(), "M")


def test_green_candle_never_confirms_an_m_trade():
    assert not is_flat_candle(flat_bottom_green(), "M")


def test_tolerance_allows_a_small_wick(monkeypatch):
    monkeypatch.setitem(STRATEGY_CONFIG, "flat_candle_wick_tolerance", 0.5)
    assert is_flat_candle(wicked_green(), "W")


def test_describe_reports_colour_and_shape():
    assert describe(flat_bottom_green(), "W") == "green, flat bottom"
    assert "wick" in describe(wicked_green(), "W")


# ------------------------------------------------------ forming Heikin Ashi bar
def test_forming_candle_is_none_without_a_partial():
    assert forming_heikin_ashi([flat_bottom_green()], None) is None


def test_forming_candle_is_converted_to_heikin_ashi():
    closed = [make_candle(1.1600, 1.1602, 1.1598, 1.1601, index=i) for i in range(3)]
    partial = make_candle(1.1601, 1.1620, 1.1601, 1.1618, index=3)
    ha = forming_heikin_ashi(closed, partial)
    assert ha is not None
    # Heikin Ashi close is the average of the partial candle's OHLC.
    assert ha.close == pytest.approx((1.1601 + 1.1620 + 1.1601 + 1.1618) / 4)


def test_partial_sharing_the_last_closed_timestamp_is_not_double_counted():
    closed = [make_candle(1.1600, 1.1602, 1.1598, 1.1601, index=i) for i in range(3)]
    partial = make_candle(1.1600, 1.1610, 1.1600, 1.1608, index=2)
    ha = forming_heikin_ashi(closed, partial)
    assert ha is not None
    assert ha.timestamp == partial.timestamp


# --------------------------------------------------------------- guard results
# The guard converts to Heikin Ashi, so the fakes below feed a flat history at
# 1.16000. That parks the running ha_open at 1.16000, which makes the shape of
# the forming bar easy to reason about.
FLAT_HISTORY = [make_candle(1.1600, 1.1600, 1.1600, 1.1600, index=i) for i in range(10)]


def raw_flat_bottom():
    """Green bar whose low sits on the running ha_open -> flat Heikin Ashi bottom."""
    return make_candle(1.1600, 1.1610, 1.1600, 1.1608, index=10)


def raw_lower_wick():
    return make_candle(1.1600, 1.1610, 1.1595, 1.1608, index=10)


def raw_flat_top():
    """Red bar whose high sits on the running ha_open -> flat Heikin Ashi top."""
    return make_candle(1.1600, 1.1600, 1.1590, 1.1592, index=10)


def raw_upper_wick():
    return make_candle(1.1600, 1.1605, 1.1590, 1.1592, index=10)


class Source:
    """Serves closed history and one partial candle per timeframe."""

    def __init__(self, partials: dict[str, object]) -> None:
        self.partials = partials

    def closed(self, asset: str, timeframe: str) -> list:
        return list(FLAT_HISTORY)

    def partial(self, asset: str, timeframe: str):
        return self.partials.get(timeframe)


def test_history_makes_the_forming_bar_a_flat_heikin_ashi_bottom():
    ha = forming_heikin_ashi(FLAT_HISTORY, raw_flat_bottom())
    assert ha is not None and is_flat_candle(ha, "W")


def test_history_makes_the_forming_bar_a_flat_heikin_ashi_top():
    ha = forming_heikin_ashi(FLAT_HISTORY, raw_flat_top())
    assert ha is not None and is_flat_candle(ha, "M")


def test_guard_allows_when_every_timeframe_is_flat():
    source = Source({"1m": raw_flat_bottom(), "5m": raw_flat_bottom()})
    guard = FlatCandleGuard(source.closed, source.partial)
    result = guard.check("EUR/USD", "30s", "W")
    assert result.allowed
    assert set(result.states) == {"1m", "5m"}


def test_guard_blocks_when_one_timeframe_has_an_opposing_wick():
    source = Source({"1m": raw_flat_bottom(), "5m": raw_lower_wick()})
    guard = FlatCandleGuard(source.closed, source.partial)
    result = guard.check("EUR/USD", "30s", "W")
    assert not result.allowed
    assert "5m" in result.reason


def test_guard_blocks_when_a_required_timeframe_has_no_candle():
    source = Source({"1m": raw_flat_bottom()})
    guard = FlatCandleGuard(source.closed, source.partial)
    result = guard.check("EUR/USD", "30s", "W")
    assert not result.allowed
    assert result.states["5m"] == "no data"


def test_five_minute_guard_reads_the_fifteen_minute_candle():
    source = Source({"15m": raw_flat_top()})
    guard = FlatCandleGuard(source.closed, source.partial)
    result = guard.check("EUR/USD", "5m", "M")
    assert result.allowed
    assert list(result.states) == ["15m"]


def test_one_minute_guard_reads_the_five_minute_candle():
    source = Source({"5m": raw_upper_wick()})
    guard = FlatCandleGuard(source.closed, source.partial)
    result = guard.check("EUR/USD", "1m", "M")
    assert not result.allowed
    assert list(result.states) == ["5m"]


def test_guard_is_inert_without_a_partial_source():
    guard = FlatCandleGuard(lambda a, t: [], None)
    assert guard.check("EUR/USD", "30s", "W").allowed


def test_guard_is_inert_when_the_filter_is_disabled(monkeypatch):
    monkeypatch.setitem(STRATEGY_CONFIG, "use_flat_candle_filter", False)
    source = Source({"1m": raw_lower_wick()})
    guard = FlatCandleGuard(source.closed, source.partial)
    assert guard.check("EUR/USD", "30s", "W").allowed


# ------------------------------------------------------- state machine gating
def feed(machine: SetupStateMachine, candles: list) -> list:
    events: list = []
    for candle in candles:
        events.extend(machine.on_closed_candle(candle))
    return events


def w_machine_at_entry(raw: bool = True) -> tuple[SetupStateMachine, Feeder]:
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


def test_execution_passes_when_the_forming_candle_is_flat(raw_candles):
    machine, feeder = w_machine_at_entry()
    source = Source({"15m": raw_flat_bottom()})
    machine.flat_guard = FlatCandleGuard(source.closed, source.partial)
    events = feed(machine, [feeder.add(1.1608, 1.16210, 1.16070, 1.1620)])
    phase3 = [e for e in events if e.kind is SignalKind.PHASE3_EXECUTION]
    assert len(phase3) == 1
    assert phase3[0].setup.flat_candle_states["15m"] == "green, flat bottom"


def test_execution_is_blocked_when_the_forming_candle_wicks_against_us(raw_candles):
    machine, feeder = w_machine_at_entry()
    source = Source({"15m": raw_lower_wick()})
    machine.flat_guard = FlatCandleGuard(source.closed, source.partial)
    events = feed(machine, [feeder.add(1.1608, 1.16210, 1.16070, 1.1620)])
    assert not [e for e in events if e.kind is SignalKind.PHASE3_EXECUTION]
    invalid = [e for e in events if e.kind is SignalKind.INVALIDATED]
    assert invalid and "flat bottom" in invalid[-1].detail
    assert machine.active is None


def test_put_execution_requires_a_flat_top(raw_candles):
    machine, feeder = m_machine_at_entry()
    source = Source({"15m": raw_flat_bottom()})
    machine.flat_guard = FlatCandleGuard(source.closed, source.partial)
    events = feed(machine, [feeder.add(1.1592, 1.15925, 1.15830, 1.1584)])
    invalid = [e for e in events if e.kind is SignalKind.INVALIDATED]
    assert invalid and "flat top" in invalid[-1].detail


def test_put_execution_passes_on_a_flat_top(raw_candles):
    machine, feeder = m_machine_at_entry()
    source = Source({"15m": raw_flat_top()})
    machine.flat_guard = FlatCandleGuard(source.closed, source.partial)
    events = feed(machine, [feeder.add(1.1592, 1.15925, 1.15830, 1.1584)])
    assert [e for e in events if e.kind is SignalKind.PHASE3_EXECUTION]


def test_no_guard_means_no_extra_gate(raw_candles):
    machine, feeder = w_machine_at_entry()
    assert machine.flat_guard is None
    events = feed(machine, [feeder.add(1.1608, 1.16210, 1.16070, 1.1620)])
    assert [e for e in events if e.kind is SignalKind.PHASE3_EXECUTION]
