"""Routes signal events to Telegram + storage and tracks signal outcomes."""

from __future__ import annotations

from src.data.models import Candle
from src.signals.models import Direction, Result, Setup, SignalEvent, SignalKind
from src.storage.database import Database
from src.telegram.notifier import Notifier
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SignalManager:
    def __init__(
        self,
        notifier: Notifier,
        database: Database | None = None,
        on_settled=None,
    ) -> None:
        self.notifier = notifier
        self.database = database
        #: Called with a Setup once its expiry has passed, so a TradeExecutor
        #: can free the capital it reserved for that trade.
        self.on_settled = on_settled
        self.pending_outcomes: list[Setup] = []
        self.executed: list[Setup] = []

    def handle(self, events: list[SignalEvent]) -> None:
        for event in events:
            self.handle_event(event)

    def handle_event(self, event: SignalEvent) -> None:
        setup = event.setup

        if self.database is not None:
            self.database.save_setup(setup)
            self.database.save_signal(setup, event.kind, event.detail)

        if event.kind is SignalKind.PHASE1_PATTERN:
            self.notifier.send_pattern_signal(setup, event.timestamp)
        elif event.kind is SignalKind.PHASE2_PULLBACK:
            self.notifier.send_pullback_signal(setup, event.timestamp)
        elif event.kind is SignalKind.PHASE3_EXECUTION:
            self.notifier.send_execution_signal(setup, event.timestamp)
            self.executed.append(setup)
            self.pending_outcomes.append(setup)
            logger.info("signal explanation %s: %s", setup.setup_id, setup.explanation())
        else:
            self.notifier.send_invalidated_signal(setup, event.timestamp)

    # ---------------------------------------------------------- outcomes
    def on_candle(self, candle: Candle) -> None:
        """Resolves WIN/LOSS once a signal's expiration time has passed."""
        still_pending: list[Setup] = []
        for setup in self.pending_outcomes:
            if (
                setup.asset != candle.asset
                or setup.timeframe != candle.timeframe
                or setup.expiry_time is None
                or candle.timestamp < setup.expiry_time
            ):
                still_pending.append(setup)
                continue
            setup.result = evaluate_result(setup, candle.close)
            logger.info(
                "outcome %s %s %s -> %s (expiry close %s vs entry %s)",
                setup.setup_id,
                setup.asset,
                setup.direction.value,
                setup.result.value,
                candle.close,
                setup.entry_price,
            )
            if self.database is not None:
                self.database.update_result(setup.setup_id, setup.result)
                self.database.save_setup(setup)
            if self.on_settled is not None:
                try:
                    self.on_settled(setup)
                except Exception as exc:  # noqa: BLE001 - never break outcome tracking
                    logger.warning("settled listener failed for %s: %s", setup.setup_id, exc)
        self.pending_outcomes = still_pending


def evaluate_result(setup: Setup, price_at_expiry: float) -> Result:
    if setup.entry_price is None:
        return Result.UNKNOWN
    if setup.direction is Direction.CALL:
        if price_at_expiry > setup.entry_price:
            return Result.WIN
        if price_at_expiry < setup.entry_price:
            return Result.LOSS
        return Result.UNKNOWN
    if price_at_expiry < setup.entry_price:
        return Result.WIN
    if price_at_expiry > setup.entry_price:
        return Result.LOSS
    return Result.UNKNOWN
