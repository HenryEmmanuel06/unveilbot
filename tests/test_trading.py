from __future__ import annotations

import pytest

from src.data.pocket_option import ORDER_ACTION_CALL, ORDER_ACTION_PUT, OrderResult
from src.signals.models import Direction, Pattern, Setup, SetupStatus, build_setup_id
from src.trading.executor import TradeExecutor, expiry_seconds, order_action
from src.trading.risk import RiskError, RiskManager
from src.utils.time import now_utc


def make_setup(asset: str = "EUR/USD", timeframe: str = "5m", pattern=Pattern.W) -> Setup:
    moment = now_utc()
    return Setup(
        setup_id=build_setup_id(asset, timeframe, pattern, moment),
        asset=asset,
        timeframe=timeframe,
        pattern=pattern,
        level_1=1.1590,
        level_mid=1.1612,
        level_2=1.1594,
        retracement_level=1.1601,
        retracement_fraction=0.6,
        structure_checks={},
        source="test",
        status=SetupStatus.EXECUTED,
    )


class FakeProvider:
    """Records orders instead of sending them."""

    def __init__(self, accepted: bool = True, error: str | None = None) -> None:
        self.accepted = accepted
        self.error = error
        self.orders: list[dict] = []
        self.is_demo = True

    async def place_order(self, asset, amount, action, duration):
        self.orders.append(
            {"asset": asset, "amount": amount, "action": action, "duration": duration}
        )
        return OrderResult(
            accepted=self.accepted,
            request_id=len(self.orders),
            symbol=asset,
            amount=amount,
            action=action,
            duration=duration,
            is_demo=True,
            order_id=f"order-{len(self.orders)}" if self.accepted else None,
            error=self.error,
        )


# ------------------------------------------------------------------ sizing
def test_stake_is_the_configured_percentage_of_balance():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    assert risk.stake_for_next_trade() == 50.0


def test_concurrent_trades_size_off_the_remaining_balance():
    """Three simultaneous signals each take 5% of what is still available."""
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)

    # 5% of 1000.00, then of 950.00, then of 902.50 - each stake floored to cents.
    assert risk.reserve("a") == 50.0
    assert risk.reserve("b") == 47.50
    assert risk.reserve("c") == 45.12

    assert risk.open_trades == 3
    assert risk.committed == pytest.approx(142.62)
    # Total exposure stays well inside the balance.
    assert risk.committed < risk.balance


def test_exposure_never_exceeds_the_balance():
    risk = RiskManager(trade_percentage=50.0, min_trade_amount=0.01)
    risk.update_balance(100.0)
    for i in range(20):
        risk.reserve(f"t{i}")
    assert risk.committed <= risk.balance


def test_stake_below_minimum_is_refused_not_rounded_up():
    risk = RiskManager(trade_percentage=5.0, min_trade_amount=10.0)
    risk.update_balance(100.0)  # 5% = 5.00, below the 10.00 minimum
    assert risk.stake_for_next_trade() == 0.0
    assert risk.reserve("a") == 0.0
    assert risk.open_trades == 0


def test_releasing_frees_capital_for_the_next_trade():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    risk.reserve("a")
    assert risk.available == 950.0
    assert risk.release("a") == 50.0
    assert risk.available == 1000.0
    assert risk.open_trades == 0


def test_zero_balance_yields_no_stake():
    risk = RiskManager(trade_percentage=5.0)
    assert risk.stake_for_next_trade() == 0.0


def test_duplicate_reference_is_rejected():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    risk.reserve("a")
    with pytest.raises(RiskError):
        risk.reserve("a")


def test_negative_balance_report_is_ignored():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(500.0)
    risk.update_balance(-1.0)
    assert risk.balance == 500.0


@pytest.mark.parametrize("percentage", [0, -5, 101])
def test_invalid_percentage_is_rejected(percentage):
    with pytest.raises(RiskError):
        RiskManager(trade_percentage=percentage)


# -------------------------------------------------------------- expirations
@pytest.mark.parametrize(
    "timeframe,seconds",
    [("15s", 60), ("30s", 60), ("1m", 180), ("5m", 600)],
)
def test_expiry_matches_the_timeframe_configuration(timeframe, seconds):
    assert expiry_seconds(timeframe) == seconds


def test_direction_maps_to_broker_action():
    assert order_action(Direction.CALL) == ORDER_ACTION_CALL
    assert order_action(Direction.PUT) == ORDER_ACTION_PUT


# ----------------------------------------------------------------- executor
@pytest.mark.asyncio
async def test_executor_places_a_sized_order():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    provider = FakeProvider()
    executor = TradeExecutor(provider, risk)

    setup = make_setup(timeframe="5m", pattern=Pattern.W)
    outcome = await executor.execute(setup)

    assert outcome.placed
    assert outcome.stake == 50.0
    assert provider.orders == [
        {
            "asset": "EUR/USD",
            "amount": 50.0,
            "action": ORDER_ACTION_CALL,
            "duration": 600,
        }
    ]
    assert setup.stake == 50.0
    assert setup.order_id == "order-1"


@pytest.mark.asyncio
async def test_m_pattern_sends_a_put_order():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    provider = FakeProvider()
    executor = TradeExecutor(provider, risk)

    await executor.execute(make_setup(timeframe="1m", pattern=Pattern.M))
    assert provider.orders[0]["action"] == ORDER_ACTION_PUT
    assert provider.orders[0]["duration"] == 180


@pytest.mark.asyncio
async def test_rejected_order_releases_the_stake():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    provider = FakeProvider(accepted=False, error="market closed")
    executor = TradeExecutor(provider, risk)

    outcome = await executor.execute(make_setup())

    assert outcome.skipped
    assert "market closed" in outcome.reason
    assert risk.committed == 0.0
    assert risk.open_trades == 0


@pytest.mark.asyncio
async def test_no_order_before_the_balance_is_known():
    risk = RiskManager(trade_percentage=5.0)
    provider = FakeProvider()
    executor = TradeExecutor(provider, risk)

    outcome = await executor.execute(make_setup())

    assert outcome.skipped
    assert provider.orders == []


@pytest.mark.asyncio
async def test_three_simultaneous_signals_all_get_orders():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    provider = FakeProvider()
    executor = TradeExecutor(provider, risk)

    setups = [
        make_setup(asset="EUR/USD", timeframe="5m"),
        make_setup(asset="GBP/USD", timeframe="1m"),
        make_setup(asset="USD/JPY", timeframe="15s"),
    ]
    outcomes = [await executor.execute(s) for s in setups]

    assert all(o.placed for o in outcomes)
    assert [o.stake for o in outcomes] == [50.0, 47.50, 45.12]
    assert risk.open_trades == 3


@pytest.mark.asyncio
async def test_settling_a_trade_frees_capital():
    risk = RiskManager(trade_percentage=5.0)
    risk.update_balance(1000.0)
    provider = FakeProvider()
    executor = TradeExecutor(provider, risk)

    setup = make_setup()
    await executor.execute(setup)
    assert risk.open_trades == 1

    executor.on_settled(setup)
    assert risk.open_trades == 0
    assert risk.available == 1000.0
