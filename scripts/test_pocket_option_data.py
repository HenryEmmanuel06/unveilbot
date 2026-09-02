"""Pocket Option live market-data proof (PHASE 5-11).

Authenticates with PO_SSID, subscribes to Pocket Option's own price stream and
builds 15-second / 1-minute / 5-minute OHLC candles from those prices.

It NEVER places a trade and NEVER prints the SSID.

Usage:
    python scripts/test_pocket_option_data.py
    python scripts/test_pocket_option_data.py --asset "EUR/USD OTC" --show-ticks 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):  # emoji-safe output on Windows pipes
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config.settings import load_settings  # noqa: E402
from config.strategy import timeframe_seconds  # noqa: E402
from src.data.base import ProviderError  # noqa: E402
from src.data.candle_builder import MultiTimeframeCandleBuilder  # noqa: E402
from src.data.models import Candle  # noqa: E402
from src.data.pocket_option import (  # noqa: E402
    POCKET_OPTION_SOURCE,
    PocketOptionAuthError,
    PocketOptionDataProvider,
    to_pocket_symbol,
)
from src.utils.logging import setup_logging  # noqa: E402

TIMEFRAMES = ["15s", "1m", "5m"]


def print_candle(candle: Candle) -> None:
    decimals = 3 if "JPY" in candle.asset.upper() else 5
    print(f"\n{candle.timeframe.upper()} CANDLE")
    print(f"Asset:     {candle.asset}")
    print(f"Timestamp: {candle.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Open:      {candle.open:.{decimals}f}")
    print(f"High:      {candle.high:.{decimals}f}")
    print(f"Low:       {candle.low:.{decimals}f}")
    print(f"Close:     {candle.close:.{decimals}f}")
    print(f"Ticks:     {int(candle.volume or 0)}")
    print(f"Source:    {candle.source}")


async def run(asset: str, show_ticks: int, history: int) -> int:
    settings = load_settings()
    setup_logging(settings.log_level)

    if not settings.po_ssid:
        print("PO_SSID is missing. Add PO_SSID=<your Pocket Option session> to .env")
        return 1

    try:
        provider = PocketOptionDataProvider(settings.po_ssid, settings.po_region)
    except ProviderError as exc:
        print(f"PO_SSID problem: {exc}")
        return 1

    print("POCKET OPTION CONNECTION")
    print(f"Account:   {provider.ssid.summary()}")
    print(f"Symbol:    {to_pocket_symbol(asset)}")
    print("Status:    connecting...")

    try:
        await provider.connect()
    except PocketOptionAuthError as exc:
        print(f"Status:    AUTH FAILED\n{exc}")
        return 1
    except ProviderError as exc:
        print(f"Status:    FAILED\n{exc}")
        return 1

    print("Status:    Connected")
    print(f"Server:    {provider.client.connected_url.split('//')[-1].split('/')[0]}")

    await provider.subscribe([asset])

    if history:
        print(f"\n--- POCKET OPTION HISTORY (requesting {history} candles) ---")
        for timeframe in TIMEFRAMES:
            try:
                candles = await provider.get_candles(asset, timeframe, history)
            except ProviderError as exc:
                print(f"{timeframe}: unavailable from Pocket Option ({exc})")
                continue
            gaps = sum(
                1
                for previous, current in zip(candles, candles[1:])
                if (current.timestamp - previous.timestamp).total_seconds()
                != timeframe_seconds(timeframe)
            )
            print(
                f"{timeframe}: {len(candles)} candles "
                f"({candles[0].timestamp:%Y-%m-%d %H:%M:%S} -> "
                f"{candles[-1].timestamp:%Y-%m-%d %H:%M:%S} UTC), gaps: {gaps}"
            )
        await provider.resubscribe()

    builders = MultiTimeframeCandleBuilder([asset], TIMEFRAMES, source=POCKET_OPTION_SOURCE)

    print("\n--- LIVE POCKET OPTION PRICES (Ctrl+C to stop) ---")
    ticks_printed = 0
    counts = {tf: 0 for tf in TIMEFRAMES}
    try:
        async for tick in provider.stream_prices():
            if tick.source != POCKET_OPTION_SOURCE:
                raise ProviderError(f"Unexpected tick source: {tick.source}")
            if ticks_printed < show_ticks:
                print(
                    f"TICK {tick.timestamp.strftime('%H:%M:%S.%f')[:-3]} UTC  "
                    f"{tick.asset}  {tick.price}"
                )
                ticks_printed += 1
            for candle in builders.add_tick(tick):
                counts[candle.timeframe] = counts.get(candle.timeframe, 0) + 1
                print_candle(candle)
    except KeyboardInterrupt:
        pass
    finally:
        await provider.disconnect()
        print("\n--- SUMMARY ---")
        print(f"Ticks received:   {provider.client.ticks_received}")
        for timeframe in TIMEFRAMES:
            print(f"{timeframe} candles built: {counts.get(timeframe, 0)}")
        print("Data source:      POCKET_OPTION")
        print("Trades placed:    0 (this script cannot trade)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pocket Option market-data test")
    parser.add_argument("--asset", default="EUR/USD")
    parser.add_argument("--show-ticks", type=int, default=10)
    parser.add_argument(
        "--history",
        type=int,
        default=60,
        help="how many historical candles to request per timeframe (0 to skip)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args.asset, args.show_ticks, args.history)))
    except KeyboardInterrupt:
        print("stopped")
