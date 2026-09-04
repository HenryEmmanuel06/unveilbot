"""Application entrypoint.

Modes (README 62):
  BACKTEST    - historical candles, no live signals
  PAPER       - live data, real-time signals, hypothetical outcomes
  LIVE_SIGNAL - live data, signals sent to Telegram

Order execution is a separate switch from the mode: orders are only ever placed
when TAKE_TRADE=true, and they go to the account behind PO_SSID.
"""

from __future__ import annotations

import argparse
import asyncio

from config.assets import all_assets
from config.settings import Settings, load_settings
from config.strategy import STRATEGY_CONFIG
from src.data.base import MarketDataProvider, ProviderError
from src.data.candle_builder import MultiTimeframeCandleBuilder
from src.data.pocket_option import POCKET_OPTION_SOURCE, PocketOptionAuthError
from src.data.provider import build_provider
from src.signals.generator import SignalEngine
from src.signals.manager import SignalManager
from src.signals.models import SignalKind
from src.storage.database import Database
from src.telegram.bot import build_bot
from src.telegram.notifier import Notifier
from src.trading.executor import TradeExecutor
from src.trading.risk import RiskManager
from src.utils.logging import get_logger, setup_logging
from src.utils.time import now_utc

logger = get_logger(__name__)

RECONNECT_DELAYS = (5, 10, 20, 30, 60)
#: How often the "still alive and waiting" line is logged.
HEARTBEAT_SECONDS = 300


class SignalBot:
    def __init__(self, settings: Settings, assets: list[str] | None = None) -> None:
        self.settings = settings
        self.assets = assets or all_assets()
        self.provider: MarketDataProvider = build_provider(
            settings.market_data_provider, settings=settings
        )
        self.engine = SignalEngine(
            assets=self.assets,
            timeframes=list(settings.enabled_timeframes),
            source=self.provider.source,
            required_source=POCKET_OPTION_SOURCE if settings.is_live_data_mode else None,
        )
        self.builders = MultiTimeframeCandleBuilder(
            self.assets, self.engine.required_timeframes, source=self.provider.source
        )
        # The execution gate inspects the higher-timeframe candle that is still
        # forming, which only the live builders know about.
        self.engine.set_partial_source(self.builders.current_partial)
        self.database = Database(settings.database_path)
        self.notifier = Notifier(
            bot=build_bot(settings),
            display_timezone=settings.display_timezone,
            notify_invalidations=settings.notify_invalidations,
            notify_phase1=settings.notify_phase1,
            notify_phase2=settings.notify_phase2,
            notify_phase3=settings.notify_phase3,
            enabled=settings.app_mode in ("PAPER", "LIVE_SIGNAL"),
        )
        self.risk: RiskManager | None = None
        self.executor: TradeExecutor | None = None
        if settings.take_trade:
            self.risk = RiskManager(
                trade_percentage=settings.trade_percentage,
                min_trade_amount=settings.min_trade_amount,
            )
            self.executor = TradeExecutor(self.provider, self.risk)
            # Pocket Option pushes the balance over the socket; sizing follows it.
            listener = getattr(self.provider, "set_balance_listener", None)
            if listener is not None:
                listener(self.risk.update_balance)

        self.manager = SignalManager(
            self.notifier,
            self.database,
            on_settled=self.executor.on_settled if self.executor else None,
        )
        self._trade_tasks: set[asyncio.Task] = set()
        self._running = False
        self._started_at = now_utc()
        self._ticks = 0
        self._candles = 0
        self._signals = 0
        self._heartbeat: asyncio.Task | None = None

    # ------------------------------------------------------------- startup
    def _validate(self) -> None:
        problems = self.settings.validate()
        if self.settings.is_live_data_mode and self.provider.source != POCKET_OPTION_SOURCE:
            problems.append(
                f"Provider {self.provider.name!r} is not Pocket Option; "
                "live signals require Pocket Option market data"
            )
        unsupported = [
            tf
            for tf in self.engine.required_timeframes
            if self.provider.native_timeframes and tf not in self.provider.native_timeframes
        ]
        if unsupported and not self.provider.supports_streaming:
            problems.append(
                f"Provider {self.provider.name} cannot supply {unsupported}. "
                "Disable those timeframes or use another provider."
            )
        if problems:
            for problem in problems:
                logger.error("configuration problem: %s", problem)
            raise SystemExit(1)

    async def _seed_history(self) -> None:
        limit = int(STRATEGY_CONFIG["min_candles_for_analysis"]) * 3
        for asset in self.assets:
            for timeframe in self.engine.required_timeframes:
                try:
                    candles = await self.provider.get_candles(asset, timeframe, limit)
                except (ProviderError, Exception) as exc:  # noqa: BLE001
                    logger.warning("history unavailable for %s %s: %s", asset, timeframe, exc)
                    continue
                self.engine.seed(candles)
                logger.info("seeded %s %s with %s candles", asset, timeframe, len(candles))

    # ----------------------------------------------------------------- run
    async def run(self) -> None:
        self._validate()
        self.database.connect()
        logger.info(
            "starting in %s mode | provider=%s | assets=%s | timeframes=%s",
            self.settings.app_mode,
            self.provider.name,
            len(self.assets),
            list(self.settings.enabled_timeframes),
        )
        if self.settings.take_trade:
            account = (
                "DEMO" if getattr(self.provider, "is_demo", False) else "REAL"
            )
            logger.warning(
                "TAKE_TRADE=true - this bot WILL place orders on your %s account "
                "at %.2f%% of the available balance per trade (minimum %.2f)",
                account,
                self.settings.trade_percentage,
                self.settings.min_trade_amount,
            )
        else:
            logger.info("TAKE_TRADE is off - signals only, no orders will be placed")
        self._running = True
        self._started_at = now_utc()
        self._heartbeat = asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
        attempt = 0
        while self._running:
            try:
                await self.provider.connect()
                await self.provider.subscribe(self.assets)
                await self._seed_history()
                # History requests switch the active symbol, so re-issue the
                # live subscriptions before consuming the stream.
                await self.provider.resubscribe()
                attempt = 0
                await self._consume()
            except asyncio.CancelledError:
                raise
            except PocketOptionAuthError as exc:
                logger.error("Pocket Option authentication failed: %s", exc)
                logger.error("Refresh PO_SSID in your .env file and restart.")
                self._running = False
                return
            except Exception as exc:  # noqa: BLE001 - resilience requirement
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                attempt += 1
                logger.error(
                    "market data error (DATA_STATUS=DISCONNECTED): %s - reconnecting in %ss",
                    exc,
                    delay,
                )
                for asset in self.assets:
                    for tf in self.settings.enabled_timeframes:
                        self.engine.mark_unreliable(asset, tf, True)
                # Resynchronise candle state: partially built candles from before
                # the disconnect must never reach the strategy.
                self.builders.reset()
                await asyncio.sleep(delay)
            finally:
                await self.provider.disconnect()

    async def _consume(self) -> None:
        for asset in self.assets:
            for tf in self.settings.enabled_timeframes:
                self.engine.mark_unreliable(asset, tf, False)
        async for tick in self.provider.stream_prices():
            self._ticks += 1
            for candle in self.builders.add_tick(tick):
                self._process_candle(candle)

    def _process_candle(self, candle) -> None:
        unreliable = self.builders.is_unreliable(candle.asset, candle.timeframe)
        self.engine.mark_unreliable(candle.asset, candle.timeframe, unreliable)
        self.database.save_candle(candle)
        self._candles += 1
        events = self.engine.on_closed_candle(candle)
        self.manager.on_candle(candle)
        if events:
            self._signals += len(events)
            for event in events:
                event.setup.payout = self._payout(event.setup.asset)
            self.manager.handle(events)
            self._place_trades(events)

    def _place_trades(self, events) -> None:
        """Fire an order per executed setup without blocking the tick stream.

        Sizing happens inside `TradeExecutor` against the shared `RiskManager`,
        so several signals closing on the same candle each take their
        percentage of the balance that is still free.
        """
        if self.executor is None:
            return
        for event in events:
            if event.kind is not SignalKind.PHASE3_EXECUTION:
                continue
            task = asyncio.create_task(
                self.executor.execute(event.setup),
                name=f"trade-{event.setup.setup_id}",
            )
            self._trade_tasks.add(task)
            task.add_done_callback(self._trade_tasks.discard)

    def _payout(self, asset: str) -> int | None:
        """Live broker payout, reported inside every signal message."""
        getter = getattr(self.provider, "payout", None)
        if getter is None:
            return None
        try:
            return getter(asset)
        except Exception as exc:  # noqa: BLE001 - payout must never block a signal
            logger.warning("Could not read payout for %s: %s", asset, exc)
            return None

    # --------------------------------------------------------- heartbeat
    async def _heartbeat_loop(self) -> None:
        """Periodic proof of life: quiet channels are normal, silence is not."""
        while self._running:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not self._running:
                return
            uptime = now_utc() - self._started_at
            active = self.engine.active_setups()
            status = getattr(self.provider, "data_status", "UNKNOWN")
            logger.info(
                "heartbeat | uptime %s | DATA_STATUS=%s | ticks=%s candles=%s "
                "signals=%s%s | active setups=%s%s",
                _duration(uptime),
                status,
                self._ticks,
                self._candles,
                self._signals,
                f" | risk={self.risk.snapshot()}" if self.risk else "",
                len(active),
                "".join(
                    f"\n    {s.asset} {s.timeframe} {s.pattern.value} {s.status.value}"
                    for s in active
                ),
            )

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            self._heartbeat = None
        await self.provider.disconnect()
        self.database.close()


def _duration(delta) -> str:
    total = int(delta.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h{minutes:02d}m{seconds:02d}s"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="W/M strategy Telegram signal bot")
    parser.add_argument("--mode", choices=["BACKTEST", "PAPER", "LIVE_SIGNAL"], default=None)
    parser.add_argument("--provider", default=None, help="pocketoption | mock | csv")
    parser.add_argument("--assets", nargs="*", default=None)
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    settings = load_settings()
    overrides = {}
    if args.mode:
        overrides["app_mode"] = args.mode
    if args.provider:
        overrides["market_data_provider"] = args.provider
    if overrides:
        from dataclasses import replace

        settings = replace(settings, **overrides)

    setup_logging(settings.log_level, settings.log_dir)

    if settings.app_mode == "BACKTEST":
        logger.info("BACKTEST mode: run scripts/backtest.py instead")
        return

    bot = SignalBot(settings, assets=args.assets)
    try:
        await bot.run()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("stopped by user")


if __name__ == "__main__":
    main()
