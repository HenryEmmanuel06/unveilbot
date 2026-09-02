"""Backtester (README 60, 61).

Runs the exact same strategy engine over historical candles.

Usage:
    python scripts/backtest.py --provider csv --timeframes 5m --assets EUR/USD
    python scripts/backtest.py --provider mock --candles 3000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.assets import all_assets  # noqa: E402
from config.settings import load_settings  # noqa: E402
from src.data.models import Candle  # noqa: E402
from src.data.provider import build_provider  # noqa: E402
from src.signals.manager import evaluate_result  # noqa: E402
from src.signals.models import Direction, Result, SignalKind  # noqa: E402
from src.signals.generator import SignalEngine  # noqa: E402
from src.utils.logging import get_logger, setup_logging  # noqa: E402

logger = get_logger("backtest")

SESSIONS = (
    ("SYDNEY/TOKYO", 0, 8),
    ("LONDON", 8, 13),
    ("LONDON/NEW_YORK", 13, 17),
    ("NEW_YORK", 17, 24),
)


def session_of(hour: int) -> str:
    for name, start, end in SESSIONS:
        if start <= hour < end:
            return name
    return "UNKNOWN"


class BacktestStats:
    def __init__(self) -> None:
        self.total_setups = 0
        self.invalid_setups = 0
        self.expired_setups = 0
        self.execution_signals = 0
        self.wins = 0
        self.losses = 0
        self.unknown = 0
        self.max_consecutive_wins = 0
        self.max_consecutive_losses = 0
        self._streak = 0
        self.equity_curve: list[int] = []
        self.by_asset: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.by_timeframe: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.by_pattern: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.by_pullback: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.by_session: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.signal_times: list = []

    def record_setup(self) -> None:
        self.total_setups += 1

    def record_result(self, setup, result: Result) -> None:
        self.execution_signals += 1
        self.signal_times.append(setup.entry_confirmation_time)
        buckets = (
            self.by_asset[setup.asset],
            self.by_timeframe[setup.timeframe],
            self.by_pattern[setup.pattern.value],
            self.by_pullback[setup.pullback_candle_count],
            self.by_session[session_of(setup.entry_confirmation_time.hour)],
        )
        for bucket in buckets:
            bucket["signals"] += 1
            bucket[result.value] += 1

        if result is Result.WIN:
            self.wins += 1
            self._streak = self._streak + 1 if self._streak > 0 else 1
            self.max_consecutive_wins = max(self.max_consecutive_wins, self._streak)
        elif result is Result.LOSS:
            self.losses += 1
            self._streak = self._streak - 1 if self._streak < 0 else -1
            self.max_consecutive_losses = max(self.max_consecutive_losses, -self._streak)
        else:
            self.unknown += 1
        pnl = (self.equity_curve[-1] if self.equity_curve else 0) + (
            1 if result is Result.WIN else -1 if result is Result.LOSS else 0
        )
        self.equity_curve.append(pnl)

    @property
    def max_drawdown(self) -> int:
        peak = 0
        drawdown = 0
        for value in self.equity_curve:
            peak = max(peak, value)
            drawdown = max(drawdown, peak - value)
        return drawdown

    def average_seconds_between_signals(self) -> float:
        times = sorted(t for t in self.signal_times if t)
        if len(times) < 2:
            return 0.0
        deltas = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        return sum(deltas) / len(deltas)

    def report(self, label: str, candle_count: int) -> str:
        decided = self.wins + self.losses
        win_rate = (self.wins / decided * 100) if decided else 0.0
        lines = [
            f"===== {label} =====",
            f"Candles processed:        {candle_count}",
            f"Total setups:             {self.total_setups}",
            f"Invalidated setups:       {self.invalid_setups}",
            f"Expired setups:           {self.expired_setups}",
            f"Execution signals:        {self.execution_signals}",
            f"Wins / Losses / Unknown:  {self.wins} / {self.losses} / {self.unknown}",
            f"Win rate:                 {win_rate:.2f}%",
            f"Loss rate:                {100 - win_rate if decided else 0:.2f}%",
            f"Max consecutive wins:     {self.max_consecutive_wins}",
            f"Max consecutive losses:   {self.max_consecutive_losses}",
            f"Max drawdown (units):     {self.max_drawdown}",
            f"Avg seconds between sigs: {self.average_seconds_between_signals():.0f}",
        ]
        for title, data in (
            ("By asset", self.by_asset),
            ("By timeframe", self.by_timeframe),
            ("By pattern", self.by_pattern),
            ("By pullback length", self.by_pullback),
            ("By session", self.by_session),
        ):
            lines.append(f"-- {title} --")
            for key, bucket in sorted(data.items(), key=lambda kv: str(kv[0])):
                decided_b = bucket["WIN"] + bucket["LOSS"]
                rate = (bucket["WIN"] / decided_b * 100) if decided_b else 0.0
                lines.append(
                    f"   {key}: signals={bucket['signals']} win={bucket['WIN']} "
                    f"loss={bucket['LOSS']} rate={rate:.1f}%"
                )
        return "\n".join(lines)


def run_dataset(
    label: str, dataset: dict[tuple[str, str], list[Candle]], timeframes: list[str]
) -> BacktestStats:
    stats = BacktestStats()
    assets = sorted({asset for asset, _ in dataset})
    engine = SignalEngine(assets=assets, timeframes=timeframes, source="backtest")

    merged: list[Candle] = []
    for candles in dataset.values():
        merged.extend(candles)
    merged.sort(key=lambda c: (c.timestamp, c.timeframe))

    pending: list = []
    processed = 0
    for candle in merged:
        processed += 1
        events = engine.on_closed_candle(candle)

        still: list = []
        for setup in pending:
            if (
                setup.asset == candle.asset
                and setup.timeframe == candle.timeframe
                and setup.expiry_time is not None
                and candle.timestamp >= setup.expiry_time
            ):
                stats.record_result(setup, evaluate_result(setup, candle.close))
            else:
                still.append(setup)
        pending = still

        for event in events:
            if event.kind is SignalKind.PHASE1_PATTERN:
                stats.record_setup()
            elif event.kind is SignalKind.INVALIDATED:
                stats.invalid_setups += 1
            elif event.kind is SignalKind.EXPIRED:
                stats.expired_setups += 1
            elif event.kind is SignalKind.PHASE3_EXECUTION:
                pending.append(event.setup)

    for setup in pending:
        stats.record_result(setup, Result.UNKNOWN)
    print(stats.report(label, processed))
    return stats


def split_dataset(
    dataset: dict[tuple[str, str], list[Candle]], ratios: tuple[float, float, float]
) -> list[dict[tuple[str, str], list[Candle]]]:
    train, validation, out_of_sample = {}, {}, {}
    for key, candles in dataset.items():
        n = len(candles)
        a = int(n * ratios[0])
        b = a + int(n * ratios[1])
        train[key] = candles[:a]
        validation[key] = candles[a:b]
        out_of_sample[key] = candles[b:]
    return [train, validation, out_of_sample]


async def load_dataset(provider_name: str, assets: list[str], timeframes: list[str], limit: int):
    settings = load_settings()
    provider = build_provider(provider_name, settings=settings)
    await provider.connect()
    engine_timeframes = timeframes
    dataset: dict[tuple[str, str], list[Candle]] = {}
    from config.strategy import higher_timeframes

    needed = list(engine_timeframes)
    for tf in engine_timeframes:
        for htf in higher_timeframes(tf):
            if htf not in needed:
                needed.append(htf)

    for asset in assets:
        for tf in needed:
            try:
                dataset[(asset, tf)] = await provider.get_candles(asset, tf, limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("skipping %s %s: %s", asset, tf, exc)
    await provider.disconnect()
    return dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the W/M strategy")
    parser.add_argument(
        "--provider", default="csv", help="csv | mock | pocketoption (offline analysis only)"
    )
    parser.add_argument("--assets", nargs="*", default=None)
    parser.add_argument("--timeframes", nargs="*", default=["5m"])
    parser.add_argument("--candles", type=int, default=2000)
    parser.add_argument("--no-split", action="store_true", help="run one combined dataset")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    assets = args.assets or all_assets()[:3]

    dataset = asyncio.run(load_dataset(args.provider, assets, args.timeframes, args.candles))
    if not dataset:
        print("No historical data available.")
        return

    if args.no_split:
        run_dataset("COMBINED", dataset, args.timeframes)
        return

    train, validation, out_of_sample = split_dataset(dataset, (0.5, 0.25, 0.25))
    run_dataset("TRAINING DATA", train, args.timeframes)
    run_dataset("VALIDATION DATA", validation, args.timeframes)
    run_dataset("OUT-OF-SAMPLE DATA", out_of_sample, args.timeframes)
    print(
        "\nReminder: parameters must be developed on TRAINING data only. "
        "Good performance on a single historical period does not prove an edge."
    )


if __name__ == "__main__":
    main()
