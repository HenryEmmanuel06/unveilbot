"""Entry confirmation - README 33, 34, 40, 41, 73, 74.

The first opposite-colour candle after the pullback is the ONLY execution
candle. If it fails, the setup is permanently invalidated.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.strategy import STRATEGY_CONFIG
from src.data.models import Candle


@dataclass(frozen=True)
class ConfirmationResult:
    confirmed: bool
    reference_price: float
    entry_price: float
    reason: str

    @property
    def failed(self) -> bool:
        return not self.confirmed


def _required_price(reference_candle: Candle, threshold: float, pattern: str) -> float:
    lo, hi = reference_candle.low, reference_candle.high
    if pattern == "W":
        return round(lo + threshold * (hi - lo), 5)
    return round(hi - threshold * (hi - lo), 5)


def confirm_w_entry(
    green_candle: Candle,
    reference_candle: Candle,
    threshold: float | None = None,
) -> ConfirmationResult:
    if threshold is None:
        threshold = float(STRATEGY_CONFIG["entry_confirmation_threshold"])
    required = _required_price(reference_candle, threshold, "W")
    confirmed = green_candle.high >= required
    return ConfirmationResult(
        confirmed=confirmed,
        reference_price=required,
        entry_price=green_candle.high,
        reason=(
            f"first green candle reached {threshold:.0%} of pullback candle ({required})"
            if confirmed
            else f"first green candle did not reach {threshold:.0%} of pullback candle"
        ),
    )


def confirm_m_entry(
    red_candle: Candle,
    reference_candle: Candle,
    threshold: float | None = None,
) -> ConfirmationResult:
    if threshold is None:
        threshold = float(STRATEGY_CONFIG["entry_confirmation_threshold"])
    required = _required_price(reference_candle, threshold, "M")
    confirmed = red_candle.low <= required
    return ConfirmationResult(
        confirmed=confirmed,
        reference_price=required,
        entry_price=red_candle.low,
        reason=(
            f"first red candle reached {threshold:.0%} of pullback candle ({required})"
            if confirmed
            else f"first red candle did not reach {threshold:.0%} of pullback candle"
        ),
    )
