"""Signal generation engine: candles in, signal events out."""

from __future__ import annotations

from config.strategy import flat_candle_timeframes, higher_timeframes
from src.data.models import Candle
from src.signals.models import SignalEvent
from src.strategy.flat_candle import FlatCandleGuard
from src.strategy.mtf_filter import MtfFilter
from src.strategy.state_machine import SetupStateMachine
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SignalEngine:
    """Owns one state machine per (asset, analysis timeframe)."""

    def __init__(
        self,
        assets: list[str],
        timeframes: list[str],
        source: str = "unknown",
        history_size: int = 600,
        required_source: str | None = None,
    ) -> None:
        self.assets = list(assets)
        self.analysis_timeframes = list(timeframes)
        self.source = source
        self.history_size = history_size
        #: When set, candles from any other data source are rejected.
        self.required_source = required_source
        self.rejected_candles = 0
        self.history: dict[tuple[str, str], list[Candle]] = {}
        self.mtf = MtfFilter(self._candles)
        self.machines: dict[tuple[str, str], SetupStateMachine] = {
            (asset, tf): SetupStateMachine(asset, tf, source=source, mtf_filter=self.mtf,
                                           history_size=history_size)
            for asset in self.assets
            for tf in self.analysis_timeframes
        }

    # ------------------------------------------------------------- helpers
    @property
    def required_timeframes(self) -> list[str]:
        needed: list[str] = list(self.analysis_timeframes)
        for tf in self.analysis_timeframes:
            # Bias timeframes plus the timeframes whose forming candle must be
            # flat before an execution - both need a live candle stream.
            for htf in list(higher_timeframes(tf)) + flat_candle_timeframes(tf):
                if htf not in needed:
                    needed.append(htf)
        return needed

    def _candles(self, asset: str, timeframe: str) -> list[Candle]:
        return self.history.get((asset, timeframe), [])

    def set_partial_source(self, partial_source) -> None:
        """Enable the flat-candle guard and the intrabar entry trigger.

        `partial_source(asset, timeframe) -> Candle | None`. Without this the
        guard stays off, because the check is meaningless on closed candles,
        and execution falls back to the close of the confirmation candle.
        """
        guard = FlatCandleGuard(self._candles, partial_source)
        for machine in self.machines.values():
            machine.flat_guard = guard
            machine.partial_source = partial_source

    def _store(self, candle: Candle) -> bool:
        key = (candle.asset, candle.timeframe)
        bucket = self.history.setdefault(key, [])
        if bucket and candle.timestamp <= bucket[-1].timestamp:
            return False
        bucket.append(candle)
        if len(bucket) > self.history_size:
            del bucket[: len(bucket) - self.history_size]
        return True

    # ---------------------------------------------------------------- API
    def _is_source_allowed(self, candle: Candle) -> bool:
        if self.required_source is None or candle.source == self.required_source:
            return True
        self.rejected_candles += 1
        logger.error(
            "Rejected %s %s candle from source %r (only %r is allowed)",
            candle.asset,
            candle.timeframe,
            candle.source,
            self.required_source,
        )
        return False

    def seed(self, candles: list[Candle]) -> None:
        candles = [c for c in candles if self._is_source_allowed(c)]
        for candle in candles:
            self._store(candle)
        if candles:
            key = (candles[0].asset, candles[0].timeframe)
            machine = self.machines.get(key)
            if machine is not None:
                machine.seed(self.history[key])

    def mark_unreliable(self, asset: str, timeframe: str, unreliable: bool) -> None:
        machine = self.machines.get((asset, timeframe))
        if machine is not None and machine.data_unreliable != unreliable:
            machine.data_unreliable = unreliable
            logger.warning(
                "%s %s data stream marked %s",
                asset,
                timeframe,
                "DATA_UNRELIABLE" if unreliable else "HEALTHY",
            )

    def on_closed_candle(self, candle: Candle) -> list[SignalEvent]:
        if not self._is_source_allowed(candle):
            return []
        if not self._store(candle):
            return []
        machine = self.machines.get((candle.asset, candle.timeframe))
        if machine is None:
            return []
        return machine.on_closed_candle(candle)

    def on_tick(self, tick) -> list[SignalEvent]:
        """Poll the entry level of every live setup on this asset.

        Called after the tick has been folded into the candle builders, so the
        forming candle each machine reads already includes this price.
        """
        events: list[SignalEvent] = []
        for tf in self.analysis_timeframes:
            machine = self.machines.get((tick.asset, tf))
            if machine is None or machine.active is None:
                continue
            events.extend(machine.on_tick(tick.price, tick.timestamp))
        return events

    def active_setups(self) -> list:
        return [m.active for m in self.machines.values() if m.active is not None]
