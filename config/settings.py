"""Environment driven application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

VALID_MODES = ("BACKTEST", "PAPER", "LIVE_SIGNAL")


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    """Read a numeric env var, tolerating a trailing '%' and stray quotes."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    cleaned = raw.strip().strip("\"'").rstrip("%").strip()
    try:
        return float(cleaned)
    except ValueError:
        return default


def normalize_chat_id(raw: str) -> str:
    """Telegram channel/supergroup ids are negative and start with -100.

    Users frequently copy the id without the leading minus sign, which makes
    the Bot API reject the request. Restore it when the value is clearly a
    channel id.
    """
    value = raw.strip()
    if not value:
        return value
    if value.startswith("@") or value.startswith("-"):
        return value
    if value.isdigit() and value.startswith("100") and len(value) >= 13:
        return "-" + value
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    log_level: str
    app_mode: str
    enabled_timeframes: tuple[str, ...]
    market_data_provider: str
    display_timezone: str
    notify_invalidations: bool
    #: When True the bot places real orders on the PO_SSID account.
    take_trade: bool
    #: Telegram signal phase switches.
    notify_phase1: bool
    notify_phase2: bool
    notify_phase3: bool
    #: Stake per trade as a percentage of the currently available balance.
    trade_percentage: float
    #: Broker minimum stake. A trade sized below this is skipped, not rounded up.
    min_trade_amount: float
    database_path: Path
    po_ssid: str
    po_region: str
    historical_dir: Path
    log_dir: Path
    extra: dict = field(default_factory=dict)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def is_live_data_mode(self) -> bool:
        return self.app_mode in ("PAPER", "LIVE_SIGNAL")

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.app_mode not in VALID_MODES:
            problems.append(f"APP_MODE must be one of {VALID_MODES}, got {self.app_mode!r}")
        if not self.enabled_timeframes:
            problems.append("No timeframe enabled (ENABLE_15S / ENABLE_30S / ENABLE_1M / ENABLE_5M)")
        if self.app_mode == "LIVE_SIGNAL" and not self.telegram_configured:
            problems.append("LIVE_SIGNAL mode requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        if self.is_live_data_mode and self.market_data_provider != "pocketoption":
            problems.append(
                "Live market data must come from Pocket Option. "
                "Set MARKET_DATA_PROVIDER=pocketoption"
            )
        if self.market_data_provider == "pocketoption" and not self.po_ssid:
            problems.append(
                "PO_SSID is missing. Add PO_SSID=<your Pocket Option session> to your .env file"
            )
        if self.take_trade:
            if not 0 < self.trade_percentage <= 100:
                problems.append(
                    f"TRADE_PERCENTAGE must be between 0 and 100, got {self.trade_percentage}"
                )
            if self.min_trade_amount <= 0:
                problems.append(
                    f"MIN_TRADE_AMOUNT must be positive, got {self.min_trade_amount}"
                )
            if self.market_data_provider != "pocketoption":
                problems.append(
                    "TAKE_TRADE=true requires MARKET_DATA_PROVIDER=pocketoption"
                )
            if self.app_mode == "BACKTEST":
                problems.append("TAKE_TRADE=true is not compatible with APP_MODE=BACKTEST")
        return problems


def load_settings() -> Settings:
    timeframes: list[str] = []
    if _env_bool("ENABLE_15S", True):
        timeframes.append("15s")
    if _env_bool("ENABLE_30S", True):
        timeframes.append("30s")
    if _env_bool("ENABLE_1M", True):
        timeframes.append("1m")
    if _env_bool("ENABLE_5M", True):
        timeframes.append("5m")

    database_path = Path(_env("DATABASE_PATH", default="data/signals.db"))
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    return Settings(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN"),
        telegram_chat_id=normalize_chat_id(_env("TELEGRAM_CHAT_ID", "CHAT_ID")),
        log_level=_env("LOG_LEVEL", default="INFO").upper(),
        app_mode=_env("APP_MODE", default="PAPER").upper(),
        enabled_timeframes=tuple(timeframes),
        market_data_provider=_env("MARKET_DATA_PROVIDER", default="pocketoption").lower(),
        display_timezone=_env("DISPLAY_TIMEZONE", default="UTC"),
        notify_invalidations=_env_bool("NOTIFY_INVALIDATIONS", False),
        take_trade=_env_bool("TAKE_TRADE", False),
        notify_phase1=_env_bool("PHASE_1_SIGNAL", True),
        notify_phase2=_env_bool("PHASE_2_SIGNAL", True),
        notify_phase3=_env_bool("PHASE_3_SIGNAL", True),
        trade_percentage=_env_float("TRADE_PERCENTAGE", 5.0),
        min_trade_amount=_env_float("MIN_TRADE_AMOUNT", 1.0),
        database_path=database_path,
        po_ssid=_env("PO_SSID"),
        po_region=_env("PO_REGION").upper(),
        historical_dir=PROJECT_ROOT / "data" / "historical",
        log_dir=PROJECT_ROOT / "data" / "logs",
    )


settings = load_settings()
