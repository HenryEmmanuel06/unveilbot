"""Setup and signal models (README 43, 53, 58, 59)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from config.assets import asset_slug
from config.strategy import expiration_for, expiration_label
from src.utils.time import now_utc, parse_expiration


class Pattern(str, Enum):
    W = "W"
    M = "M"

    @property
    def label(self) -> str:
        return "W / Double Bottom" if self is Pattern.W else "M / Double Top"

    @property
    def direction(self) -> "Direction":
        return Direction.CALL if self is Pattern.W else Direction.PUT


class Direction(str, Enum):
    CALL = "CALL / UP"
    PUT = "PUT / DOWN"


class Phase(str, Enum):
    NONE = "NONE"
    PATTERN_SENT = "PATTERN_SENT"
    PULLBACK_SENT = "PULLBACK_SENT"
    EXECUTION_SENT = "EXECUTION_SENT"


class SetupStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    PULLBACK = "PULLBACK"
    EXECUTED = "EXECUTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class SignalKind(str, Enum):
    PHASE1_PATTERN = "PHASE1_PATTERN"
    PHASE2_PULLBACK = "PHASE2_PULLBACK"
    PHASE3_EXECUTION = "PHASE3_EXECUTION"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class Result(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    UNKNOWN = "UNKNOWN"


def build_setup_id(asset: str, timeframe: str, pattern: Pattern, moment: datetime) -> str:
    return (
        f"{asset_slug(asset)}-{timeframe.upper()}-{pattern.value}-"
        f"{moment.strftime('%Y%m%d-%H%M%S')}"
    )


@dataclass
class Setup:
    """A single tracked W/M setup. Unique per (asset, timeframe, setup_id)."""

    setup_id: str
    asset: str
    timeframe: str
    pattern: Pattern
    # L1/H1, P/T, L2/H2
    level_1: float
    level_mid: float
    level_2: float
    retracement_level: float
    retracement_fraction: float
    structure_checks: dict[str, bool]
    source: str
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    state: str = "WAITING_FOR_CENTER_BREAK"
    status: SetupStatus = SetupStatus.ACTIVE
    phase: Phase = Phase.NONE
    pattern_confirmation_time: datetime | None = None
    pullback_start: datetime | None = None
    pullback_candle_count: int = 0
    reference_price: float | None = None
    entry_confirmation_time: datetime | None = None
    entry_price: float | None = None
    expiry_time: datetime | None = None
    invalidation_reason: str | None = None
    result: Result = Result.UNKNOWN
    mtf_bias: dict[str, str] = field(default_factory=dict)
    #: Forming higher-timeframe candle states read by the flat-candle guard,
    #: e.g. {"5m": "green, flat bottom"}.
    flat_candle_states: dict[str, str] = field(default_factory=dict)
    candles_seen: int = 0
    #: Broker payout percentage at signal time, e.g. 92 -> "+92%".
    payout: int | None = None
    #: Amount actually staked when TAKE_TRADE placed a real order.
    stake: float | None = None
    #: Broker order id returned by Pocket Option, when a trade was placed.
    order_id: str | None = None

    @property
    def payout_label(self) -> str | None:
        return f"+{self.payout}%" if self.payout is not None else None

    @property
    def direction(self) -> Direction:
        return self.pattern.direction

    @property
    def expiration(self) -> str:
        return expiration_for(self.timeframe)

    @property
    def expiration_label(self) -> str:
        return expiration_label(self.timeframe)

    @property
    def is_open(self) -> bool:
        return self.status in (SetupStatus.ACTIVE, SetupStatus.CONFIRMED, SetupStatus.PULLBACK)

    def touch(self, moment: datetime | None = None) -> None:
        self.updated_at = moment or now_utc()

    def set_expiry(self, entry_time: datetime) -> None:
        self.expiry_time = entry_time + parse_expiration(self.expiration)

    def explanation(self) -> dict:
        """Answers 'why did this signal occur?' (README 88)."""
        return {
            "pattern": self.pattern.label,
            "L1/H1": self.level_1,
            "P/T": self.level_mid,
            "L2/H2": self.level_2,
            "50% retracement": "PASS" if self.structure_checks.get("retracement_50") else "FAIL",
            "pattern breakout": "PASS" if self.pattern_confirmation_time else "PENDING",
            "pullback candles": self.pullback_candle_count,
            "reference": self.reference_price,
            "entry candle": self.entry_price,
            "entry reference break": "PASS" if self.entry_price is not None else "PENDING",
            "higher timeframe": self.mtf_bias or "n/a",
            "forming candle": self.flat_candle_states or "n/a",
            "expiration": self.expiration_label,
            "payout": self.payout_label or "unknown",
            "data source": self.source,
        }

    def as_row(self) -> dict:
        return {
            "setup_id": self.setup_id,
            "asset": self.asset,
            "timeframe": self.timeframe,
            "pattern": self.pattern.value,
            "level_1": self.level_1,
            "level_mid": self.level_mid,
            "level_2": self.level_2,
            "retracement": self.retracement_fraction,
            "pattern_confirmation_time": _iso(self.pattern_confirmation_time),
            "pullback_start": _iso(self.pullback_start),
            "pullback_candle_count": self.pullback_candle_count,
            "reference_price": self.reference_price,
            "entry_confirmation": _iso(self.entry_confirmation_time),
            "entry_price": self.entry_price,
            "direction": self.direction.value,
            "payout": self.payout,
            "expiration": self.expiration,
            "expiry_time": _iso(self.expiry_time),
            "result": self.result.value,
            "status": self.status.value,
            "phase": self.phase.value,
            "invalidation_reason": self.invalidation_reason,
            "source": self.source,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


@dataclass
class SignalEvent:
    kind: SignalKind
    setup: Setup
    timestamp: datetime
    detail: str = ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
