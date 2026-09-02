"""End-to-end proof of the signal pipeline (PHASE 12 verification).

Feeds a scripted candle sequence through the REAL components:

    candles -> SignalEngine -> SetupStateMachine -> SignalManager
            -> Notifier -> Telegram

It shows that the bot only speaks when the strategy fires:
PHASE 1 (W/M pattern formed) -> PHASE 2 (pullback) -> PHASE 3 (entry confirmed).

Dry run (prints the exact Telegram text, sends nothing):
    python scripts/test_signal_pipeline.py

Really send the three messages to the channel:
    python scripts/test_signal_pipeline.py --send

Options:
    --pattern w|m|both        which structure to drive (default: both)
    --timeframe 15s|1m|5m     timeframe label on the candles (default: 1m)
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):  # emoji-safe output on Windows pipes
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config.strategy import higher_timeframes, timeframe_seconds  # noqa: E402
from config.settings import load_settings  # noqa: E402
from src.data.models import Candle  # noqa: E402
from src.data.pocket_option import POCKET_OPTION_SOURCE  # noqa: E402
from src.signals.generator import SignalEngine  # noqa: E402
from src.signals.manager import SignalManager  # noqa: E402
from src.signals.models import SignalKind  # noqa: E402
from src.telegram.bot import build_bot  # noqa: E402
from src.telegram.notifier import Notifier  # noqa: E402
from src.utils.logging import setup_logging  # noqa: E402
from src.utils.time import floor_to_timeframe, now_utc  # noqa: E402

ASSET = "EUR/USD"
PAD = 0.00002

# Swing structure the detector recognises (same shape as the unit tests).
W_CLOSES = [
    1.1600, 1.1598, 1.1590,  # L1
    1.1595, 1.1600, 1.1610, 1.1612,  # P
    1.1608, 1.1600, 1.1594,  # L2
    1.1598, 1.1604,
]
M_CLOSES = [
    1.1600, 1.1602, 1.1610,  # H1
    1.1605, 1.1600, 1.1590, 1.1588,  # T
    1.1592, 1.1600, 1.1606,  # H2
    1.1602, 1.1596,
]

# Higher-timeframe context. The legs are 3 candles long so the fractal swing
# detector (swing_lookback = 2) can actually see the highs and lows:
# higher highs + higher lows = BULLISH, lower highs + lower lows = BEARISH.
UPTREND_CLOSES = [
    1.1560, 1.1568, 1.1576, 1.1570, 1.1564,
    1.1572, 1.1580, 1.1588, 1.1582, 1.1576,
    1.1584, 1.1592, 1.1600, 1.1594, 1.1588,
    1.1596, 1.1604, 1.1612,
]
DOWNTREND_CLOSES = [
    1.1640, 1.1632, 1.1624, 1.1630, 1.1636,
    1.1628, 1.1620, 1.1612, 1.1618, 1.1624,
    1.1616, 1.1608, 1.1600, 1.1606, 1.1612,
    1.1604, 1.1596, 1.1588,
]

# (open, high, low, close) candles that drive breakout -> pullback -> entry.
W_TRIGGERS = [
    ("P breakout", (1.1604, 1.16190, 1.16035, 1.1618)),
    ("pullback red #1", (1.1618, 1.16200, 1.16100, 1.1612)),
    ("pullback red #2", (1.1612, 1.16130, 1.16050, 1.1608)),
    ("entry green (breaks reference high)", (1.1608, 1.16210, 1.16070, 1.1620)),
]
M_TRIGGERS = [
    ("T breakout", (1.1596, 1.15965, 1.15840, 1.1585)),
    ("pullback green #1", (1.1585, 1.15900, 1.15840, 1.1589)),
    ("pullback green #2", (1.1589, 1.15930, 1.15880, 1.1592)),
    ("entry red (breaks reference low)", (1.1592, 1.15925, 1.15830, 1.1584)),
]


class Series:
    """Builds candles on strict timeframe boundaries, tagged POCKET_OPTION."""

    def __init__(self, timeframe: str, start_price: float) -> None:
        self.timeframe = timeframe
        self.seconds = timeframe_seconds(timeframe)
        self.start = floor_to_timeframe(now_utc(), self.seconds) - timedelta(
            seconds=self.seconds * 120
        )
        self.price = start_price
        self.index = 0

    def add(self, open_: float, high: float, low: float, close: float) -> Candle:
        candle = Candle(
            asset=ASSET,
            timeframe=self.timeframe,
            timestamp=self.start + timedelta(seconds=self.seconds * self.index),
            open=round(open_, 5),
            high=round(high, 5),
            low=round(low, 5),
            close=round(close, 5),
            volume=12.0,
            source=POCKET_OPTION_SOURCE,
        )
        self.index += 1
        self.price = close
        return candle

    def add_close(self, close: float) -> Candle:
        open_ = self.price
        return self.add(open_, max(open_, close) + PAD, min(open_, close) - PAD, close)

    def filler(self, count: int) -> list[Candle]:
        base = self.price
        return [
            self.add_close(base + (0.00001 if i % 2 else -0.00001)) for i in range(count)
        ]


PHASE_LABEL = {
    SignalKind.PHASE1_PATTERN: "PHASE 1 - PATTERN FORMED",
    SignalKind.PHASE2_PULLBACK: "PHASE 2 - PULLBACK",
    SignalKind.PHASE3_EXECUTION: "PHASE 3 - ENTRY CONFIRMATION",
    SignalKind.INVALIDATED: "INVALIDATED",
    SignalKind.EXPIRED: "EXPIRED",
}


def run_pattern(pattern: str, timeframe: str, manager: SignalManager, notifier: Notifier) -> bool:
    closes = W_CLOSES if pattern == "w" else M_CLOSES
    triggers = W_TRIGGERS if pattern == "w" else M_TRIGGERS

    engine = SignalEngine(
        assets=[ASSET],
        timeframes=[timeframe],
        source=POCKET_OPTION_SOURCE,
        required_source=POCKET_OPTION_SOURCE,
    )
    series = Series(timeframe, 1.1600)

    # The higher timeframe filter (PHASE 3 gate) needs its own candles.
    htf_closes = UPTREND_CLOSES if pattern == "w" else DOWNTREND_CLOSES
    for htf in higher_timeframes(timeframe):
        htf_series = Series(htf, htf_closes[0])
        for close in htf_closes:
            engine.on_closed_candle(htf_series.add_close(close))
        bias = engine.mtf.biases(ASSET, timeframe).get(htf)
        print(f"{htf.upper()} context: {len(htf_closes)} candles, bias {bias.value}")

    print(f"\n{'=' * 62}")
    print(f"{pattern.upper()} PATTERN RUN - {ASSET} {timeframe.upper()}")
    print(f"{'=' * 62}")

    seen: list[SignalKind] = []

    def feed(candles: list[Candle], label: str = "") -> None:
        for candle in candles:
            events = engine.on_closed_candle(candle)
            manager.on_candle(candle)
            if events:
                for event in events:
                    seen.append(event.kind)
                    print(f"\n>>> {PHASE_LABEL.get(event.kind, event.kind.value)}")
                    if label:
                        print(f"    triggered by: {label}")
                    if event.detail:
                        print(f"    detail: {event.detail}")
                manager.handle(events)
            elif label:
                print(f"    {label}: no signal (as expected)")

    print("\nWarm-up (60 neutral candles) + swing structure...")
    feed(series.filler(60))
    feed([series.add_close(close) for close in closes])

    for label, ohlc in triggers:
        feed([series.add(*ohlc)], label)

    got = {
        "PHASE 1": SignalKind.PHASE1_PATTERN in seen,
        "PHASE 2": SignalKind.PHASE2_PULLBACK in seen,
        "PHASE 3": SignalKind.PHASE3_EXECUTION in seen,
    }
    print(f"\n--- {pattern.upper()} RESULT ---")
    for label, ok in got.items():
        print(f"{label}: {'FIRED' if ok else 'MISSING'}")
    print(f"Rejected non-Pocket-Option candles: {engine.rejected_candles}")

    if not notifier.enabled:
        print("\n--- TELEGRAM MESSAGES THAT WOULD BE SENT ---")
        for _, text in notifier.outbox:
            print(f"\n{'-' * 50}\n{text}")
        notifier.outbox.clear()

    return all(got.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Signal pipeline end-to-end test")
    parser.add_argument("--send", action="store_true", help="really send to Telegram")
    parser.add_argument("--pattern", choices=["w", "m", "both"], default="both")
    parser.add_argument("--timeframe", choices=["15s", "1m", "5m"], default="1m")
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.log_level)

    bot = build_bot(settings)
    if args.send and not bot.configured:
        print("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 1

    notifier = Notifier(
        bot=bot,
        display_timezone=settings.display_timezone,
        notify_invalidations=settings.notify_invalidations,
        enabled=args.send,
    )
    manager = SignalManager(notifier, database=None)

    if args.send:
        bot.try_send_message(
            "🧪 PIPELINE TEST\n\n"
            "Driving the strategy engine with a scripted W/M structure.\n"
            "The next messages are produced by the real signal logic.\n\n"
            "No trade is executed."
        )

    patterns = ["w", "m"] if args.pattern == "both" else [args.pattern]
    ok = all(run_pattern(p, args.timeframe, manager, notifier) for p in patterns)

    print(f"\n{'=' * 62}")
    print(f"PIPELINE: {'ALL PHASES WORKING' if ok else 'INCOMPLETE'}")
    print(f"Telegram delivery: {'live' if args.send else 'dry run (use --send)'}")
    print(f"Executed setups: {len(manager.executed)}")
    print("Trades placed: 0 (this bot never trades)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
