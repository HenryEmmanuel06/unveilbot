"""Telegram message formatting (README 27, 32, 35, 39, 42, 75, 76, 77).

Messages stay concise: technical detail belongs in the logs/database.
"""

from __future__ import annotations

from datetime import datetime

from src.signals.models import Pattern, Setup
from src.utils.time import format_display


def format_price(asset: str, price: float | None) -> str:
    if price is None:
        return "-"
    decimals = 3 if "JPY" in asset.upper() else 5
    return f"{price:.{decimals}f}"


def _tf(timeframe: str) -> str:
    return timeframe.upper()


def _check(ok: bool) -> str:
    return "✓" if ok else "✗"


def _payout_line(setup: Setup) -> str:
    """Current broker payout for the asset, when Pocket Option reported it."""
    label = setup.payout_label
    return f"Payout: {label}\n" if label else ""


def _footer(setup: Setup, timestamp: datetime, tz: str) -> str:
    return (
        f"Setup ID:\n{setup.setup_id}\n\n"
        f"Data Source: {setup.source}\n"
        f"Time: {format_display(timestamp, tz)}"
    )


def format_pattern_signal(setup: Setup, timestamp: datetime, tz: str = "UTC") -> str:
    is_w = setup.pattern is Pattern.W
    header = "🟢 W PATTERN FORMED" if is_w else "🔴 M PATTERN FORMED"
    labels = ("L1", "P", "L2") if is_w else ("H1", "T", "H2")
    checks = setup.structure_checks
    protection = "L1 Protection" if is_w else "H1 Protection"
    tolerance = "L2 Tolerance" if is_w else "H2 Tolerance"
    breakout = "P" if is_w else "T"
    status = (
        f"{breakout} Breakout: ✓\n"
        "Status: AWAITING PULLBACK\n\n"
        "Pullback limit: 3 candles"
    )

    return (
        f"{header}\n\n"
        f"Asset: {setup.asset}\n"
        f"Timeframe: {_tf(setup.timeframe)}\n"
        f"{_payout_line(setup)}\n"
        f"Pattern: {setup.pattern.label}\n\n"
        f"{labels[0]}: {format_price(setup.asset, setup.level_1)}\n"
        f"{labels[1]}: {format_price(setup.asset, setup.level_mid)}\n"
        f"{labels[2]}: {format_price(setup.asset, setup.level_2)}\n\n"
        f"50% Retracement: {_check(checks.get('retracement_50', False))}\n"
        f"{protection}: {_check(checks.get('l1_protection', checks.get('h1_protection', False)))}\n"
        f"{tolerance}: {_check(checks.get('l2_tolerance', checks.get('h2_tolerance', False)))}\n\n"
        f"{status}\n\n"
        "This is not an execution signal.\n\n"
        f"{_footer(setup, timestamp, tz)}"
    )


def format_pullback_signal(setup: Setup, timestamp: datetime, tz: str = "UTC") -> str:
    is_w = setup.pattern is Pattern.W
    header = "🟡 W PULLBACK FORMED" if is_w else "🟡 M PULLBACK FORMED"
    breakout = "P Breakout" if is_w else "T Breakout"
    dot = "🔴" if is_w else "🟢"
    colour = "Red" if is_w else "Green"
    candles = "\n".join(
        f"{dot} {colour} Candle #{i + 1}" for i in range(setup.pullback_candle_count)
    )
    waiting = "WAITING FOR FIRST OPPOSITE CANDLE\nTO REACH ENTRY CONFIRMATION"

    return (
        f"{header}\n\n"
        f"Asset: {setup.asset}\n"
        f"Timeframe: {_tf(setup.timeframe)}\n"
        f"{_payout_line(setup)}\n"
        f"Pattern: {setup.pattern.label}\n"
        f"Direction: {setup.direction.value}\n\n"
        f"{setup.pattern.value} Confirmed ✓\n"
        f"{breakout}: ✓\n\n"
        f"Pullback:\n{candles}\n\n"
        "Maximum: 3 candles\n\n"
        f"Status:\n{waiting}\n\n"
        f"Expiration: {setup.expiration_label}\n\n"
        f"{_footer(setup, timestamp, tz)}"
    )


def format_execution_signal(setup: Setup, timestamp: datetime, tz: str = "UTC") -> str:
    is_w = setup.pattern is Pattern.W
    header = (
        "🟠 CALL / UP EXECUTION SIGNAL" if is_w else "🟠 PUT / DOWN EXECUTION SIGNAL"
    )
    reference_label = "Entry Reference:"
    entry_label = "Entry Candle High:" if is_w else "Entry Candle Low:"
    bias = (
        "\n".join(f"{tf.upper()} bias: {value}" for tf, value in setup.mtf_bias.items())
        if setup.mtf_bias
        else ""
    )
    bias_block = f"{bias}\n\n" if bias else ""

    return (
        f"{header}\n\n"
        f"Asset: {setup.asset}\n"
        f"Timeframe: {_tf(setup.timeframe)}\n"
        f"{_payout_line(setup)}\n"
        f"Pattern: {setup.pattern.label}\n\n"
        f"{setup.pattern.value} Confirmation: ✓\n"
        "Pullback: ✓\n"
        "Entry Candle: ✓\n\n"
        f"{reference_label}\n{format_price(setup.asset, setup.reference_price)}\n\n"
        f"{entry_label}\n{format_price(setup.asset, setup.entry_price)}\n\n"
        f"➡️ Direction: {setup.direction.value}\n\n"
        f"⏱ Expiration: {setup.expiration_label}\n\n"
        f"{bias_block}"
        f"{_footer(setup, timestamp, tz)}\n\n"
        "⚠️ Strategy signal only. No trade is executed by this bot."
    )


def format_invalidated_signal(setup: Setup, timestamp: datetime, tz: str = "UTC") -> str:
    return (
        f"⚪ SETUP {setup.status.value}\n\n"
        f"Asset: {setup.asset}\n"
        f"Timeframe: {_tf(setup.timeframe)}\n\n"
        f"Pattern: {setup.pattern.label}\n"
        f"Reason: {setup.invalidation_reason}\n\n"
        f"{_footer(setup, timestamp, tz)}"
    )
