"""Builds timeframe candles from a tick/price stream and tracks data health."""

from __future__ import annotations

from datetime import datetime, timedelta

from config.strategy import STRATEGY_CONFIG, timeframe_seconds
from src.data.models import Candle, Tick
from src.utils.logging import get_logger
from src.utils.time import floor_to_timeframe

logger = get_logger(__name__)


class CandleBuilder:
    """Aggregates ticks of one asset/timeframe into closed candles."""

    def __init__(self, asset: str, timeframe: str, source: str = "unknown") -> None:
        self.asset = asset
        self.timeframe = timeframe
        self.source = source
        self.seconds = timeframe_seconds(timeframe)
        self._bucket: datetime | None = None
        self._open = 0.0
        self._high = 0.0
        self._low = 0.0
        self._close = 0.0
        self._ticks = 0
        self.missing_candles = 0

    @property
    def data_unreliable(self) -> bool:
        return self.missing_candles > int(STRATEGY_CONFIG["max_missing_candles"])

    def add_tick(self, tick: Tick) -> Candle | None:
        """Feed a tick. Returns the previous candle when it just closed."""
        bucket = floor_to_timeframe(tick.timestamp, self.seconds)
        closed: Candle | None = None

        if self._bucket is None:
            self._start(bucket, tick.price)
            return None

        if bucket < self._bucket:
            logger.debug("%s %s late tick ignored", self.asset, self.timeframe)
            return None

        if bucket > self._bucket:
            closed = self._close_current()
            expected = self._bucket + timedelta(seconds=self.seconds)
            gap = int((bucket - expected).total_seconds() // self.seconds)
            if gap > 0:
                self.missing_candles += gap
                logger.warning(
                    "%s %s missing %s candle(s) - stream health degraded",
                    self.asset,
                    self.timeframe,
                    gap,
                )
            else:
                self.missing_candles = 0
            self._start(bucket, tick.price)
            return closed

        self._high = max(self._high, tick.price)
        self._low = min(self._low, tick.price)
        self._close = tick.price
        self._ticks += 1
        return None

    def _start(self, bucket: datetime, price: float) -> None:
        self._bucket = bucket
        self._open = self._high = self._low = self._close = price
        self._ticks = 1

    def _close_current(self) -> Candle | None:
        if self._bucket is None or self._ticks == 0:
            return None
        return Candle(
            asset=self.asset,
            timeframe=self.timeframe,
            timestamp=self._bucket,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=float(self._ticks),
            source=self.source,
        )

    def current_partial(self) -> Candle | None:
        """The in-progress candle. Never use it for strategy decisions."""
        return self._close_current()


class MultiTimeframeCandleBuilder:
    """One builder per (asset, timeframe)."""

    def __init__(self, assets: list[str], timeframes: list[str], source: str = "unknown") -> None:
        self.builders: dict[tuple[str, str], CandleBuilder] = {
            (asset, tf): CandleBuilder(asset, tf, source) for asset in assets for tf in timeframes
        }

    def add_tick(self, tick: Tick) -> list[Candle]:
        closed: list[Candle] = []
        for (asset, _tf), builder in self.builders.items():
            if asset != tick.asset:
                continue
            candle = builder.add_tick(tick)
            if candle is not None:
                closed.append(candle)
        return closed

    def is_unreliable(self, asset: str, timeframe: str) -> bool:
        builder = self.builders.get((asset, timeframe))
        return bool(builder and builder.data_unreliable)

    def current_partial(self, asset: str, timeframe: str) -> Candle | None:
        """The candle still forming on that timeframe, or None.

        Only the flat-candle execution guard may read this: it is deliberately
        an unfinished bar and must never feed pattern or pullback logic.
        """
        builder = self.builders.get((asset, timeframe))
        return builder.current_partial() if builder is not None else None

    def reset(self) -> None:
        """Drop all partially built candles (used after a reconnect)."""
        for (asset, timeframe), builder in list(self.builders.items()):
            self.builders[(asset, timeframe)] = CandleBuilder(
                asset, timeframe, builder.source
            )
