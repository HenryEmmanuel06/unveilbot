"""Telegram notification service with duplicate prevention (README 52, 56)."""

from __future__ import annotations

from datetime import datetime

from src.signals.models import Setup, SignalKind
from src.telegram.bot import TelegramBot
from src.telegram.formatter import (
    format_execution_signal,
    format_invalidated_signal,
    format_pattern_signal,
    format_pullback_signal,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class Notifier:
    def __init__(
        self,
        bot: TelegramBot,
        display_timezone: str = "UTC",
        notify_invalidations: bool = False,
        enabled: bool = True,
    ) -> None:
        self.bot = bot
        self.tz = display_timezone
        self.notify_invalidations = notify_invalidations
        self.enabled = enabled and bot.configured
        self._sent: set[tuple[str, str]] = set()
        self.outbox: list[tuple[str, str]] = []

    def _dispatch(self, setup: Setup, kind: SignalKind, text: str) -> bool:
        key = (setup.setup_id, kind.value)
        if key in self._sent:
            logger.debug("duplicate %s suppressed for %s", kind.value, setup.setup_id)
            return False
        self._sent.add(key)
        self.outbox.append((setup.setup_id, text))
        if not self.enabled:
            logger.info("[telegram disabled] %s\n%s", kind.value, text)
            return False
        ok = self.bot.try_send_message(text)
        if ok:
            logger.info("Telegram %s sent (%s)", kind.value, setup.setup_id)
        return ok

    def send_pattern_signal(self, setup: Setup, timestamp: datetime) -> bool:
        return self._dispatch(
            setup, SignalKind.PHASE1_PATTERN, format_pattern_signal(setup, timestamp, self.tz)
        )

    def send_pullback_signal(self, setup: Setup, timestamp: datetime) -> bool:
        return self._dispatch(
            setup, SignalKind.PHASE2_PULLBACK, format_pullback_signal(setup, timestamp, self.tz)
        )

    def send_execution_signal(self, setup: Setup, timestamp: datetime) -> bool:
        return self._dispatch(
            setup, SignalKind.PHASE3_EXECUTION, format_execution_signal(setup, timestamp, self.tz)
        )

    def send_invalidated_signal(self, setup: Setup, timestamp: datetime) -> bool:
        if not self.notify_invalidations:
            logger.info(
                "setup %s %s: %s",
                setup.setup_id,
                setup.status.value,
                setup.invalidation_reason,
            )
            return False
        return self._dispatch(
            setup, SignalKind.INVALIDATED, format_invalidated_signal(setup, timestamp, self.tz)
        )
