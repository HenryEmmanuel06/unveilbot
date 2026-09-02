"""Verifies the Telegram configuration and the signal message pipeline.

Usage:
    python scripts/test_telegram.py
    python scripts/test_telegram.py --signals
    python scripts/test_telegram.py --signals --asset "EUR/USD" --timeframe 1m

`--signals` sends the three real strategy messages (pattern -> pullback ->
execution) built from a simulated setup that uses the LIVE Pocket Option price,
so the exact Telegram output can be reviewed before real signals appear.
No trade is ever placed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings  # noqa: E402
from src.signals.models import Pattern, Phase, Setup, SetupStatus, build_setup_id  # noqa: E402
from src.telegram.bot import TelegramError, build_bot  # noqa: E402
from src.telegram.notifier import Notifier  # noqa: E402
from src.utils.logging import setup_logging  # noqa: E402
from src.utils.time import now_utc  # noqa: E402


def pip_size(asset: str) -> float:
    return 0.01 if "JPY" in asset.upper() else 0.0001


async def live_price(settings, asset: str) -> tuple[float, str]:
    """One real Pocket Option price. Falls back to a static price if offline."""
    from src.data.base import ProviderError
    from src.data.pocket_option import POCKET_OPTION_SOURCE, PocketOptionDataProvider

    if not settings.po_ssid:
        return (1.16717, "SIMULATED (PO_SSID not set)")
    provider = PocketOptionDataProvider(settings.po_ssid, settings.po_region)
    try:
        await provider.connect()
        await provider.subscribe([asset])
        stream = provider.stream_prices()
        try:
            tick = await stream.__anext__()
        finally:
            await stream.aclose()
        return (tick.price, POCKET_OPTION_SOURCE)
    except ProviderError as exc:
        print(f"Warning: live price unavailable ({exc}); using a simulated price")
    finally:
        await provider.disconnect()
    return (1.16717, "SIMULATED (Pocket Option unavailable)")


def build_demo_setup(asset: str, timeframe: str, price: float, source: str) -> Setup:
    """A realistic W setup around `price` (structure only, nothing is traded)."""
    pip = pip_size(asset)
    moment = now_utc()
    level_1 = round(price - 12 * pip, 5)
    level_mid = round(price + 4 * pip, 5)
    level_2 = round(price - 11 * pip, 5)
    setup = Setup(
        setup_id=build_setup_id(asset, timeframe, Pattern.W, moment),
        asset=asset,
        timeframe=timeframe,
        pattern=Pattern.W,
        level_1=level_1,
        level_mid=level_mid,
        level_2=level_2,
        retracement_level=round(level_1 + (level_mid - level_1) * 0.5, 5),
        retracement_fraction=0.62,
        structure_checks={
            "retracement_50": True,
            "l1_protection": True,
            "l2_tolerance": True,
        },
        source=source,
    )
    setup.status = SetupStatus.PULLBACK
    setup.phase = Phase.PULLBACK_SENT
    setup.pattern_confirmation_time = moment
    setup.pullback_start = moment
    setup.pullback_candle_count = 2
    setup.reference_price = round(price + 2 * pip, 5)
    setup.entry_confirmation_time = moment
    setup.entry_price = round(price + 3 * pip, 5)
    setup.set_expiry(moment)
    setup.mtf_bias = {"5m": "BULLISH", "15m": "NEUTRAL"}
    return setup


def send_signal_sequence(settings, bot, asset: str, timeframe: str) -> int:
    price, source = asyncio.run(live_price(settings, asset))
    print(f"Reference price: {price} (source: {source})")

    setup = build_demo_setup(asset, timeframe, price, source)
    notifier = Notifier(
        bot=bot,
        display_timezone=settings.display_timezone,
        notify_invalidations=settings.notify_invalidations,
        enabled=True,
    )

    bot.try_send_message(
        "🧪 TEST SEQUENCE\n\n"
        "The next three messages show the real signal formats\n"
        "(pattern -> pullback -> execution) using a simulated setup.\n\n"
        f"Asset: {asset}\nTimeframe: {timeframe.upper()}\nPrice source: {source}\n\n"
        "These are NOT live signals and no trade is executed."
    )

    moment = now_utc()
    results = {
        "PHASE 1 pattern": notifier.send_pattern_signal(setup, moment),
        "PHASE 2 pullback": notifier.send_pullback_signal(setup, moment),
        "PHASE 3 execution": notifier.send_execution_signal(setup, moment),
    }
    duplicate_blocked = not notifier.send_pattern_signal(setup, moment)

    print("\n--- SIGNAL DELIVERY ---")
    for label, ok in results.items():
        print(f"{label}: {'sent' if ok else 'FAILED'}")
    print(f"Duplicate suppression: {'working' if duplicate_blocked else 'NOT WORKING'}")
    print(f"Setup ID: {setup.setup_id}")
    return 0 if all(results.values()) and duplicate_blocked else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram delivery test")
    parser.add_argument(
        "--signals",
        action="store_true",
        help="also send the 3-phase strategy messages using a live Pocket Option price",
    )
    parser.add_argument("--asset", default="EUR/USD")
    parser.add_argument("--timeframe", default="1m", choices=["15s", "1m", "5m"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    bot = build_bot(settings)

    if not bot.configured:
        print("Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return 1

    try:
        me = bot.get_me()
    except TelegramError as exc:
        print(f"Telegram connection failed: {exc}")
        return 1

    print("Telegram connection successful.")
    print(f"Bot: @{me.get('username')} (id {me.get('id')})")
    print(f"Target chat id: {bot.chat_id}")

    try:
        chat = bot.get_chat()
        print(f"Chat: {chat.get('title') or chat.get('username')} ({chat.get('type')})")
    except TelegramError as exc:
        print(f"Warning: could not read chat info: {exc}")

    try:
        bot.send_message(
            "✅ Pocket Option W/M Signal Bot\n\n"
            "Telegram connection test successful.\n"
            "This bot only sends analysis signals and never executes trades."
        )
    except TelegramError as exc:
        print(f"Test message failed: {exc}")
        return 1

    print("Test message sent successfully.")

    if args.signals:
        print("\n--- SENDING 3-PHASE SIGNAL SEQUENCE ---")
        return send_signal_sequence(settings, bot, args.asset, args.timeframe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
