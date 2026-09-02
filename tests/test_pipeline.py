from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

from src.data.models import Candle
from src.data.pocket_option import POCKET_OPTION_SOURCE
from src.signals.generator import SignalEngine
from src.signals.models import Pattern, SetupStatus, build_setup_id
from src.signals.models import Setup
from src.telegram import bot as bot_module
from src.telegram.bot import TelegramBot, TelegramError
from src.telegram.notifier import Notifier

START = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def candle(index: int, source: str, timeframe: str = "1m") -> Candle:
    return Candle(
        asset="EUR/USD",
        timeframe=timeframe,
        timestamp=START + timedelta(minutes=index),
        open=1.16,
        high=1.1601,
        low=1.1599,
        close=1.16005,
        volume=5.0,
        source=source,
    )


def make_setup() -> Setup:
    return Setup(
        setup_id=build_setup_id("EUR/USD", "1m", Pattern.W, START),
        asset="EUR/USD",
        timeframe="1m",
        pattern=Pattern.W,
        level_1=1.1590,
        level_mid=1.1612,
        level_2=1.1594,
        retracement_level=1.1601,
        retracement_fraction=0.6,
        structure_checks={"retracement_50": True},
        source=POCKET_OPTION_SOURCE,
        status=SetupStatus.ACTIVE,
    )


class FakeBot:
    configured = True

    def __init__(self) -> None:
        self.sent: list[str] = []

    def try_send_message(self, text: str) -> bool:
        self.sent.append(text)
        return True


# ------------------------------------------------------- source enforcement
def test_engine_rejects_candles_from_another_source():
    engine = SignalEngine(
        assets=["EUR/USD"],
        timeframes=["1m"],
        source=POCKET_OPTION_SOURCE,
        required_source=POCKET_OPTION_SOURCE,
    )
    assert engine.on_closed_candle(candle(0, "MOCK")) == []
    assert engine.rejected_candles == 1
    assert engine.history == {}


def test_engine_accepts_pocket_option_candles():
    engine = SignalEngine(
        assets=["EUR/USD"],
        timeframes=["1m"],
        source=POCKET_OPTION_SOURCE,
        required_source=POCKET_OPTION_SOURCE,
    )
    engine.on_closed_candle(candle(0, POCKET_OPTION_SOURCE))
    assert engine.rejected_candles == 0
    assert len(engine.history[("EUR/USD", "1m")]) == 1


def test_seeding_drops_foreign_candles():
    engine = SignalEngine(
        assets=["EUR/USD"],
        timeframes=["1m"],
        source=POCKET_OPTION_SOURCE,
        required_source=POCKET_OPTION_SOURCE,
    )
    engine.seed([candle(i, "CSV_HISTORICAL") for i in range(5)])
    assert engine.rejected_candles == 5
    assert engine.history == {}


def test_no_required_source_allows_any_candle():
    engine = SignalEngine(assets=["EUR/USD"], timeframes=["1m"], source="MOCK")
    engine.on_closed_candle(candle(0, "MOCK"))
    assert engine.rejected_candles == 0


# ------------------------------------------------------------ notifications
def test_each_phase_is_sent_once_per_setup():
    fake = FakeBot()
    notifier = Notifier(bot=fake, display_timezone="UTC", enabled=True)
    setup = make_setup()

    assert notifier.send_pattern_signal(setup, START)
    assert notifier.send_pullback_signal(setup, START)
    assert notifier.send_execution_signal(setup, START)
    assert len(fake.sent) == 3

    # Re-sending the same phase for the same setup must be suppressed.
    assert not notifier.send_pattern_signal(setup, START)
    assert len(fake.sent) == 3


def test_payout_is_reported_in_every_phase():
    fake = FakeBot()
    notifier = Notifier(bot=fake, display_timezone="UTC", enabled=True)
    setup = make_setup()
    setup.payout = 92

    notifier.send_pattern_signal(setup, START)
    notifier.send_pullback_signal(setup, START)
    notifier.send_execution_signal(setup, START)
    assert len(fake.sent) == 3
    for text in fake.sent:
        assert "Payout: +92%" in text


def test_payout_line_is_omitted_when_unknown():
    fake = FakeBot()
    notifier = Notifier(bot=fake, display_timezone="UTC", enabled=True)
    setup = make_setup()
    assert setup.payout is None

    notifier.send_pattern_signal(setup, START)
    assert "Payout:" not in fake.sent[0]


def test_messages_state_that_no_trade_is_executed():
    fake = FakeBot()
    notifier = Notifier(bot=fake, display_timezone="UTC", enabled=True)
    notifier.send_execution_signal(make_setup(), START)
    assert "No trade is executed" in fake.sent[-1]


def test_disabled_notifier_still_records_the_outbox():
    fake = FakeBot()
    notifier = Notifier(bot=fake, display_timezone="UTC", enabled=False)
    assert not notifier.send_pattern_signal(make_setup(), START)
    assert fake.sent == []
    assert len(notifier.outbox) == 1


# ------------------------------------------------------------ send retries
def test_transient_network_error_is_retried(monkeypatch):
    monkeypatch.setattr(bot_module.time, "sleep", lambda _s: None)
    bot = TelegramBot(token="t", chat_id="c")
    calls = {"n": 0}

    def flaky(text: str, disable_notification: bool = False) -> dict:
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectTimeout("timeout")
        return {"message_id": 1}

    monkeypatch.setattr(bot, "send_message", flaky)
    assert bot.try_send_message("hello") is True
    assert calls["n"] == 3


def test_retries_give_up_and_never_raise(monkeypatch):
    monkeypatch.setattr(bot_module.time, "sleep", lambda _s: None)
    bot = TelegramBot(token="t", chat_id="c")

    def always_fail(text: str, disable_notification: bool = False) -> dict:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(bot, "send_message", always_fail)
    assert bot.try_send_message("hello") is False


def test_telegram_rejection_is_not_retried(monkeypatch):
    monkeypatch.setattr(bot_module.time, "sleep", lambda _s: None)
    bot = TelegramBot(token="t", chat_id="c")
    calls = {"n": 0}

    def rejected(text: str, disable_notification: bool = False) -> dict:
        calls["n"] += 1
        raise TelegramError("chat not found")

    monkeypatch.setattr(bot, "send_message", rejected)
    assert bot.try_send_message("hello") is False
    assert calls["n"] == 1


def test_unconfigured_bot_reports_not_configured():
    assert TelegramBot(token="", chat_id="").configured is False
    with pytest.raises(TelegramError):
        TelegramBot(token="", chat_id="")._call("getMe", {})
