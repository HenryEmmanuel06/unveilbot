"""Turns a PHASE 3 execution signal into a real broker order.

Flow for one signal:

1. `RiskManager.reserve()` sizes the stake from the *available* balance, so
   simultaneous signals each get TRADE_PERCENTAGE of what is left.
2. The expiry is taken from the setup's own timeframe (`TIMEFRAME_CONFIG`), so a
   15s setup buys a 1m option and a 5m setup buys a 10m option.
3. The order goes to the account behind PO_SSID.
4. If the broker refuses, the stake is released immediately - a rejected order
   must not keep capital locked.

Trading is off unless `TAKE_TRADE=true`. When off, this class is not built at
all, so there is no code path from a signal to an order.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.strategy import expiration_for
from src.data.pocket_option import (
    ORDER_ACTION_CALL,
    ORDER_ACTION_PUT,
    OrderResult,
    PocketOptionOrderError,
)
from src.signals.models import Direction, Setup
from src.trading.risk import RiskManager
from src.utils.logging import get_logger
from src.utils.time import parse_expiration

logger = get_logger(__name__)


@dataclass(frozen=True)
class TradeOutcome:
    """What the executor did with one signal."""

    placed: bool
    setup_id: str
    reason: str
    stake: float = 0.0
    order: OrderResult | None = None

    @property
    def skipped(self) -> bool:
        return not self.placed


def expiry_seconds(timeframe: str) -> int:
    """Option duration in seconds for a setup's timeframe."""
    return int(parse_expiration(expiration_for(timeframe)).total_seconds())


def order_action(direction: Direction) -> str:
    return ORDER_ACTION_CALL if direction is Direction.CALL else ORDER_ACTION_PUT


class TradeExecutor:
    """Places one order per executed setup, sized by `RiskManager`."""

    def __init__(self, provider, risk: RiskManager) -> None:
        self.provider = provider
        self.risk = risk
        self.placed: list[Setup] = []
        self.skipped: list[Setup] = []

    async def execute(self, setup: Setup) -> TradeOutcome:
        reference = setup.setup_id

        if self.risk.balance <= 0:
            return self._skip(setup, "broker balance not known yet")

        stake = self.risk.reserve(reference)
        if stake <= 0:
            return self._skip(
                setup,
                f"stake below minimum {self.risk.min_trade_amount:.2f} "
                f"(available {self.risk.available:.2f}, {self.risk.open_trades} open)",
            )

        action = order_action(setup.direction)
        duration = expiry_seconds(setup.timeframe)

        try:
            result = await self.provider.place_order(
                asset=setup.asset,
                amount=stake,
                action=action,
                duration=duration,
            )
        except PocketOptionOrderError as exc:
            self.risk.release(reference)
            return self._skip(setup, f"order failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - a broker error must not kill the bot
            self.risk.release(reference)
            logger.exception("unexpected error placing order for %s", reference)
            return self._skip(setup, f"unexpected order error: {exc}")

        if not result.accepted:
            self.risk.release(reference)
            return self._skip(setup, f"broker rejected the order: {result.error}")

        setup.stake = stake
        setup.order_id = result.order_id
        self.placed.append(setup)
        logger.info(
            "TRADE PLACED %s %s %s stake=%.2f expiry=%ss order=%s (%s account, %s)",
            reference,
            setup.asset,
            setup.direction.value,
            stake,
            duration,
            result.order_id,
            "DEMO" if result.is_demo else "REAL",
            self.risk.snapshot(),
        )
        return TradeOutcome(
            placed=True,
            setup_id=reference,
            reason="order accepted",
            stake=stake,
            order=result,
        )

    def on_settled(self, setup: Setup) -> None:
        """Free the reserved capital once the option has expired."""
        self.risk.release(setup.setup_id)

    def _skip(self, setup: Setup, reason: str) -> TradeOutcome:
        self.skipped.append(setup)
        logger.warning("TRADE SKIPPED %s: %s", setup.setup_id, reason)
        return TradeOutcome(placed=False, setup_id=setup.setup_id, reason=reason)
