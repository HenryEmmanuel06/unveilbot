"""Runs the bot in PAPER mode (live data, real-time signals, no execution).

Usage:
    python scripts/paper_trade.py
    python scripts/paper_trade.py --provider mock --assets EUR/USD GBP/USD
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings  # noqa: E402
from src.main import SignalBot  # noqa: E402
from src.utils.logging import setup_logging  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper mode runner")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--assets", nargs="*", default=None)
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    settings = load_settings()
    overrides = {"app_mode": "PAPER"}
    if args.provider:
        overrides["market_data_provider"] = args.provider
    settings = replace(settings, **overrides)
    setup_logging(settings.log_level, settings.log_dir)

    bot = SignalBot(settings, assets=args.assets)
    try:
        await bot.run()
    finally:
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("stopped")
