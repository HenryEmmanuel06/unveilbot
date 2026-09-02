"""Position sizing and exposure tracking.

Sizing rule (TRADE_PERCENTAGE):

    stake = available_balance * trade_percentage / 100

`available_balance` is the broker balance minus the capital already committed
to trades that have not settled yet. That single detail is what makes
simultaneous signals safe: each new trade is sized off what is *left*, so N
signals firing on the same candle close cannot over-commit the account.

With a 1000 balance at 5%:

    trade 1 -> 50.00   (available 1000.00)
    trade 2 -> 47.50   (available  950.00)
    trade 3 -> 45.13   (available  902.50)

Total exposure converges towards the balance instead of exceeding it, and no
hard cap on the number of concurrent trades is needed. A stake that lands below
the broker minimum is refused outright rather than rounded up, because rounding
up would silently breach the percentage.

The broker's own balance updates arrive asynchronously over the WebSocket. They
are treated as the source of truth for `balance`, while `committed` is tracked
locally so sizing stays correct in the window before the broker confirms.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.utils.logging import get_logger

logger = get_logger(__name__)

#: Broker stakes are whole cents.
STAKE_DECIMALS = 2


def floor_to_cents(value: float) -> float:
    """Round a stake DOWN to whole cents.

    Deliberately not `round()`: banker's rounding can round a stake up, which
    would stake marginally more than TRADE_PERCENTAGE allows. Erring downwards
    keeps the configured percentage a hard ceiling.
    """
    return math.floor(value * 100) / 100


class RiskError(Exception):
    """A trade cannot be sized under the configured risk rules."""


@dataclass
class RiskManager:
    """Tracks balance and open exposure, and sizes each new trade."""

    trade_percentage: float
    min_trade_amount: float = 1.0
    balance: float = 0.0
    #: stake currently locked in unsettled trades, keyed by trade reference.
    _open: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 < self.trade_percentage <= 100:
            raise RiskError(
                f"trade_percentage must be between 0 and 100, got {self.trade_percentage}"
            )
        if self.min_trade_amount <= 0:
            raise RiskError(f"min_trade_amount must be positive, got {self.min_trade_amount}")

    # ------------------------------------------------------------- exposure
    @property
    def committed(self) -> float:
        return round(sum(self._open.values()), STAKE_DECIMALS)

    @property
    def available(self) -> float:
        return round(max(0.0, self.balance - self.committed), STAKE_DECIMALS)

    @property
    def open_trades(self) -> int:
        return len(self._open)

    def update_balance(self, balance: float) -> None:
        """Apply a balance figure reported by the broker."""
        if balance < 0:
            logger.warning("ignoring negative balance report %s", balance)
            return
        self.balance = float(balance)

    # --------------------------------------------------------------- sizing
    def stake_for_next_trade(self) -> float:
        """Stake for one more trade, or 0.0 when the account cannot support it."""
        if self.balance <= 0:
            return 0.0
        stake = floor_to_cents(self.available * self.trade_percentage / 100.0)
        if stake < self.min_trade_amount:
            return 0.0
        return stake

    def reserve(self, reference: str) -> float:
        """Size and lock a stake. Returns 0.0 if the trade must be skipped."""
        if reference in self._open:
            raise RiskError(f"trade reference {reference!r} is already open")
        stake = self.stake_for_next_trade()
        if stake <= 0:
            return 0.0
        self._open[reference] = stake
        logger.info(
            "reserved %.2f for %s (%.2f%% of %.2f available; %s trade(s) open, %.2f committed)",
            stake,
            reference,
            self.trade_percentage,
            self.available + stake,
            self.open_trades,
            self.committed,
        )
        return stake

    def release(self, reference: str) -> float:
        """Free a stake once the trade has settled. Returns the freed amount."""
        stake = self._open.pop(reference, 0.0)
        if stake:
            logger.info(
                "released %.2f from %s (%s trade(s) still open)",
                stake,
                reference,
                self.open_trades,
            )
        return stake

    def snapshot(self) -> dict:
        """Loggable state, safe to serialise."""
        return {
            "balance": round(self.balance, STAKE_DECIMALS),
            "committed": self.committed,
            "available": self.available,
            "open_trades": self.open_trades,
            "trade_percentage": self.trade_percentage,
        }
