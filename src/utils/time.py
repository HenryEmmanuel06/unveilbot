"""Time helpers. All internal timestamps are timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def floor_to_timeframe(dt: datetime, seconds: int) -> datetime:
    dt = ensure_utc(dt)
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=UTC)


def parse_expiration(expiration: str) -> timedelta:
    """'10m' -> timedelta(minutes=10), '30s' -> timedelta(seconds=30)."""
    value = expiration.strip().lower()
    if value.endswith("ms"):
        raise ValueError(f"Unsupported expiration: {expiration}")
    unit = value[-1]
    amount = float(value[:-1])
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    raise ValueError(f"Unsupported expiration: {expiration}")


def to_display(dt: datetime, tz_name: str) -> datetime:
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = UTC
    return ensure_utc(dt).astimezone(tz)


def format_display(dt: datetime, tz_name: str) -> str:
    local = to_display(dt, tz_name)
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")
