"""Explicit W/M state machines (README 44, 45, 56, 57).

One machine instance per (asset, timeframe). State is never shared between
assets or timeframes.
"""

from __future__ import annotations

from enum import Enum

from config.strategy import STRATEGY_CONFIG
from src.data.models import Candle
from src.signals.models import (
    Pattern,
    Phase,
    SetupStatus,
    Setup,
    SignalEvent,
    SignalKind,
    build_setup_id,
)
from src.strategy.confirmation import confirm_m_entry, confirm_w_entry
from src.strategy.heikin_ashi import HeikinAshiConverter
from src.strategy.m_pattern import detect_m_structure, t_broken
from src.strategy.pullback import PullbackEvent, PullbackTracker
from src.strategy.swing_detector import SwingDetector
from src.strategy.w_pattern import detect_w_structure, p_broken
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SetupState(str, Enum):
    STRUCTURE_VALID = "STRUCTURE_VALID"
    WAITING_FOR_CENTER_BREAK = "WAITING_FOR_CENTER_BREAK"
    PATTERN_CONFIRMED = "PATTERN_CONFIRMED"
    WAITING_FOR_PULLBACK = "WAITING_FOR_PULLBACK"
    PULLBACK_ACTIVE = "PULLBACK_ACTIVE"
    WAITING_FOR_ENTRY_CANDLE = "WAITING_FOR_ENTRY_CANDLE"
    SIGNAL_SENT = "SIGNAL_SENT"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


class SetupStateMachine:
    def __init__(
        self,
        asset: str,
        timeframe: str,
        source: str = "unknown",
        mtf_filter=None,
        history_size: int = 600,
    ) -> None:
        self.asset = asset
        self.timeframe = timeframe
        self.source = source
        self.mtf_filter = mtf_filter
        self.history_size = history_size
        self.candles: list[Candle] = []
        #: Heikin Ashi mirror of `self.candles`, index for index.
        self.ha_candles: list[Candle] = []
        self._ha = HeikinAshiConverter()
        #: Set by `SignalEngine.set_partial_source` when live partial candles
        #: are available; None means the flat-candle gate is skipped.
        self.flat_guard = None
        self.active: Setup | None = None
        self._tracker: PullbackTracker | None = None
        self._detector = SwingDetector()
        self._seen_fingerprints: set[tuple] = set()
        self.data_unreliable = False

    # ------------------------------------------------- candle representation
    @property
    def _ha_enabled(self) -> bool:
        return bool(STRATEGY_CONFIG["use_heikin_ashi"])

    @property
    def _ha_everywhere(self) -> bool:
        return self._ha_enabled and STRATEGY_CONFIG["heikin_ashi_scope"] == "all"

    def _append(self, candle: Candle) -> Candle:
        """Store a closed candle and its Heikin Ashi twin. Returns the twin."""
        self.candles.append(candle)
        ha = self._ha.add(candle)
        self.ha_candles.append(ha)
        return ha

    def _signal_candle(self, ha: Candle, real: Candle) -> Candle:
        """Candle used for colour-based decisions (pullback, entry)."""
        return ha if self._ha_enabled else real

    def _structure_candle(self, ha: Candle, real: Candle) -> Candle:
        """Candle used for level breaks (L1/H1 protection, P/T breakout)."""
        return ha if self._ha_everywhere else real

    @property
    def _structure_series(self) -> list[Candle]:
        return self.ha_candles if self._ha_everywhere else self.candles

    # ------------------------------------------------------------------ API
    def seed(self, candles: list[Candle]) -> None:
        """Load historical closed candles without generating signals."""
        for candle in candles:
            if self.candles and candle.timestamp <= self.candles[-1].timestamp:
                continue
            self._append(candle)
        self._trim()

    def on_closed_candle(self, candle: Candle) -> list[SignalEvent]:
        if self.candles and candle.timestamp <= self.candles[-1].timestamp:
            logger.debug("%s %s duplicate/late candle ignored", self.asset, self.timeframe)
            return []
        ha = self._append(candle)
        self._trim()

        if self.data_unreliable:
            logger.warning(
                "%s %s DATA_UNRELIABLE - signal generation paused", self.asset, self.timeframe
            )
            return []

        events: list[SignalEvent] = []
        if self.active is not None:
            events.extend(self._advance(candle, ha))

        if self.active is None and len(self.candles) >= int(
            STRATEGY_CONFIG["min_candles_for_analysis"]
        ):
            events.extend(self._scan(candle))
        return events

    # ------------------------------------------------------------ detection
    def _scan(self, candle: Candle) -> list[SignalEvent]:
        series = self._structure_series
        w = detect_w_structure(series, self._detector)
        m = detect_m_structure(series, self._detector)

        chosen_pattern: Pattern | None = None
        structure = None
        if w and m:
            if w.l2_index >= m.h2_index:
                chosen_pattern, structure = Pattern.W, w
            else:
                chosen_pattern, structure = Pattern.M, m
        elif w:
            chosen_pattern, structure = Pattern.W, w
        elif m:
            chosen_pattern, structure = Pattern.M, m

        if structure is None or chosen_pattern is None:
            return []

        fingerprint = (self.asset, self.timeframe, *structure.fingerprint())
        if fingerprint in self._seen_fingerprints:
            return []
        self._seen_fingerprints.add(fingerprint)

        if chosen_pattern is Pattern.W:
            level_1, level_mid, level_2 = structure.l1, structure.p, structure.l2
        else:
            level_1, level_mid, level_2 = structure.h1, structure.t, structure.h2

        setup = Setup(
            setup_id=build_setup_id(self.asset, self.timeframe, chosen_pattern, candle.timestamp),
            asset=self.asset,
            timeframe=self.timeframe,
            pattern=chosen_pattern,
            level_1=level_1,
            level_mid=level_mid,
            level_2=level_2,
            retracement_level=structure.retracement_level,
            retracement_fraction=structure.retracement_fraction,
            structure_checks=dict(structure.checks),
            source=self.source,
            state=SetupState.WAITING_FOR_CENTER_BREAK.value,
        )

        if STRATEGY_CONFIG["mtf_filter_stage"] == "pattern":
            allowed, biases = self._check_bias(setup)
            setup.mtf_bias = {k: v.value for k, v in biases.items()}
            if not allowed:
                logger.info(
                    "%s %s %s candidate skipped: higher timeframe bias %s",
                    self.asset,
                    self.timeframe,
                    chosen_pattern.value,
                    setup.mtf_bias,
                )
                return []

        self.active = setup
        self._tracker = PullbackTracker(chosen_pattern.value)
        logger.info(
            "%s %s %s candidate detected (L1/H1=%s P/T=%s L2/H2=%s) id=%s",
            self.asset,
            self.timeframe,
            chosen_pattern.value,
            level_1,
            level_mid,
            level_2,
            setup.setup_id,
        )
        setup.touch(candle.timestamp)
        return []

    # -------------------------------------------------------------- advance
    def _advance(self, candle: Candle, ha: Candle) -> list[SignalEvent]:
        setup = self.active
        assert setup is not None and self._tracker is not None
        setup.candles_seen += 1
        setup.touch(candle.timestamp)

        # Colour-driven rules (pullback, entry) read the Heikin Ashi candle;
        # level breaks follow `heikin_ashi_scope`.
        signal_candle = self._signal_candle(ha, candle)
        structure_candle = self._structure_candle(ha, candle)

        max_age = int(STRATEGY_CONFIG["max_setup_age_candles"].get(self.timeframe, 40))
        if setup.candles_seen > max_age:
            return [self._close_setup(setup, candle, SetupStatus.EXPIRED, "setup expired")]

        is_w = setup.pattern is Pattern.W

        # Protection of L1/H1 applies until the pattern is confirmed.
        if setup.pattern_confirmation_time is None:
            if is_w and structure_candle.low < setup.level_1:
                return [self._invalidate(setup, candle, "L1 broken before W confirmation")]
            if not is_w and structure_candle.high > setup.level_1:
                return [self._invalidate(setup, candle, "H1 broken before M confirmation")]

            broken = (
                p_broken(structure_candle, setup.level_mid)
                if is_w
                else t_broken(structure_candle, setup.level_mid)
            )
            if broken:
                setup.pattern_confirmation_time = candle.timestamp
                setup.status = SetupStatus.CONFIRMED
                setup.state = SetupState.WAITING_FOR_PULLBACK.value
                setup.phase = Phase.PATTERN_SENT
                logger.info(
                    "%s %s %s broken - %s confirmed (%s)",
                    self.asset,
                    self.timeframe,
                    "P" if is_w else "T",
                    setup.pattern.value,
                    setup.setup_id,
                )
                return [
                    SignalEvent(
                        kind=SignalKind.PHASE1_PATTERN,
                        setup=setup,
                        timestamp=candle.timestamp,
                        detail="pattern confirmed, awaiting pullback",
                    )
                ]
            return []

        event = self._tracker.add(signal_candle)

        if event is PullbackEvent.STARTED:
            setup.pullback_candle_count = self._tracker.count
            setup.pullback_start = candle.timestamp
            setup.reference_price = self._tracker.reference_price
            setup.status = SetupStatus.PULLBACK
            setup.state = SetupState.PULLBACK_ACTIVE.value
            logger.info(
                "%s %s pullback started (reference %s) %s",
                self.asset,
                self.timeframe,
                setup.reference_price,
                setup.setup_id,
            )
            return []

        if event in (PullbackEvent.VALID, PullbackEvent.CONTINUED):
            setup.pullback_candle_count = self._tracker.count
            setup.state = SetupState.WAITING_FOR_ENTRY_CANDLE.value
            logger.info(
                "%s %s pullback candle %s/%s %s",
                self.asset,
                self.timeframe,
                self._tracker.count,
                STRATEGY_CONFIG["maximum_pullback_candles"],
                setup.setup_id,
            )
            if event is PullbackEvent.VALID and setup.phase is Phase.PATTERN_SENT:
                setup.phase = Phase.PULLBACK_SENT
                return [
                    SignalEvent(
                        kind=SignalKind.PHASE2_PULLBACK,
                        setup=setup,
                        timestamp=candle.timestamp,
                        detail="pullback reached minimum length",
                    )
                ]
            return []

        if event is PullbackEvent.INVALID_TOO_MANY:
            reason = (
                "4th red pullback candle" if is_w else "4th green pullback candle"
            )
            return [self._invalidate(setup, candle, reason)]

        if event is PullbackEvent.INVALID_TOO_SHORT:
            return [
                self._invalidate(
                    setup,
                    candle,
                    "opposite candle appeared before the minimum pullback length",
                )
            ]

        if event is PullbackEvent.CONFIRMATION_CANDLE:
            reference_candle = self._tracker.reference_candle
            assert reference_candle is not None
            result = (
                confirm_w_entry(signal_candle, reference_candle)
                if is_w
                else confirm_m_entry(signal_candle, reference_candle)
            )
            if result.failed:
                return [self._invalidate(setup, candle, result.reason)]

            setup.reference_price = result.reference_price

            if STRATEGY_CONFIG["mtf_filter_stage"] == "execution":
                allowed, biases = self._check_bias(setup)
                setup.mtf_bias = {k: v.value for k, v in biases.items()}
                if not allowed:
                    return [
                        self._invalidate(
                            setup,
                            candle,
                            f"higher timeframe bias not aligned ({setup.mtf_bias})",
                        )
                    ]

            flat = self._check_flat_candles(setup)
            if flat is not None and not flat.allowed:
                return [self._invalidate(setup, candle, flat.reason)]

            setup.entry_confirmation_time = candle.timestamp
            # The trigger is read from the (Heikin Ashi) signal candle, but the
            # price reported and later scored must be a real market price.
            setup.entry_price = candle.close if self._ha_enabled else result.entry_price
            setup.status = SetupStatus.EXECUTED
            setup.state = SetupState.SIGNAL_SENT.value
            setup.phase = Phase.EXECUTION_SENT
            setup.set_expiry(candle.timestamp)
            logger.info(
                "%s %s %s confirmation - %s",
                self.asset,
                self.timeframe,
                setup.direction.value,
                setup.setup_id,
            )
            self.active = None
            self._tracker = None
            return [
                SignalEvent(
                    kind=SignalKind.PHASE3_EXECUTION,
                    setup=setup,
                    timestamp=candle.timestamp,
                    detail=result.reason,
                )
            ]

        return []

    # --------------------------------------------------------------- helpers
    def _check_bias(self, setup: Setup):
        from src.strategy.mtf_filter import Bias

        if self.mtf_filter is None or not STRATEGY_CONFIG["use_mtf_filter"]:
            return True, {}
        allowed, biases = self.mtf_filter.allows(
            self.asset, self.timeframe, setup.pattern.value
        )
        return allowed, {k: (v if isinstance(v, Bias) else Bias(v)) for k, v in biases.items()}

    def _check_flat_candles(self, setup: Setup):
        """Higher-timeframe forming candles must not wick against the trade."""
        if self.flat_guard is None:
            return None
        result = self.flat_guard.check(self.asset, self.timeframe, setup.pattern.value)
        if result.states:
            setup.flat_candle_states = dict(result.states)
            logger.info(
                "%s %s flat candle check %s: %s",
                self.asset,
                self.timeframe,
                "passed" if result.allowed else "blocked",
                result.states,
            )
        return result

    def _invalidate(self, setup: Setup, candle: Candle, reason: str) -> SignalEvent:
        logger.info(
            "%s %s setup invalidated: %s (%s)", self.asset, self.timeframe, reason, setup.setup_id
        )
        return self._close_setup(setup, candle, SetupStatus.INVALIDATED, reason)

    def _close_setup(
        self, setup: Setup, candle: Candle, status: SetupStatus, reason: str
    ) -> SignalEvent:
        setup.status = status
        setup.state = (
            SetupState.EXPIRED.value if status is SetupStatus.EXPIRED else SetupState.INVALID.value
        )
        setup.invalidation_reason = reason
        setup.touch(candle.timestamp)
        self.active = None
        self._tracker = None
        kind = (
            SignalKind.EXPIRED if status is SetupStatus.EXPIRED else SignalKind.INVALIDATED
        )
        return SignalEvent(kind=kind, setup=setup, timestamp=candle.timestamp, detail=reason)

    def _trim(self) -> None:
        if len(self.candles) > self.history_size:
            excess = len(self.candles) - self.history_size
            del self.candles[:excess]
            del self.ha_candles[:excess]
