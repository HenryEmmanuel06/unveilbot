from __future__ import annotations

from datetime import datetime, timezone

from config.settings import normalize_chat_id
from src.signals.models import Pattern, Setup, SignalKind
from src.telegram.formatter import (
    format_execution_signal,
    format_pattern_signal,
    format_pullback_signal,
)
from src.telegram.notifier import Notifier

NOW = datetime(2026, 8, 25, 18, 20, tzinfo=timezone.utc)


class FakeBot:
    configured = True

    def __init__(self) -> None:
        self.sent: list[str] = []

    def try_send_message(self, text: str) -> bool:
        self.sent.append(text)
        return True


def make_setup() -> Setup:
    setup = Setup(
        setup_id="EURUSD-5M-W-20260825-182000",
        asset="EUR/USD",
        timeframe="5m",
        pattern=Pattern.W,
        level_1=1.16000,
        level_mid=1.16500,
        level_2=1.16150,
        retracement_level=1.16250,
        retracement_fraction=0.7,
        structure_checks={
            "retracement_50": True,
            "l1_protection": True,
            "l2_tolerance": True,
        },
        source="test",
    )
    setup.pullback_candle_count = 2
    setup.reference_price = 1.16580
    setup.entry_price = 1.16585
    return setup


def test_pattern_message_contains_required_fields():
    text = format_pattern_signal(make_setup(), NOW)
    assert "W PATTERN FORMED" in text
    assert "EUR/USD" in text
    assert "5M" in text
    assert "AWAITING PULLBACK" in text
    assert "P Breakout: ✓" in text
    assert "EURUSD-5M-W-20260825-182000" in text
    assert "CALL" not in text


def test_pullback_message_contains_status_and_expiration():
    text = format_pullback_signal(make_setup(), NOW)
    assert "W PULLBACK FORMED" in text
    assert "TO REACH ENTRY CONFIRMATION" in text
    assert "10 Minutes" in text
    assert "Red Candle #2" in text
    assert "P Price: 1.16500" in text


def test_execution_message_contains_direction_and_disclaimer():
    text = format_execution_signal(make_setup(), NOW)
    assert "CALL / UP" in text
    assert "1.16585" in text
    assert "Strategy signal only" in text


def test_duplicate_messages_are_suppressed():
    bot = FakeBot()
    notifier = Notifier(bot, display_timezone="UTC")
    setup = make_setup()
    assert notifier.send_pattern_signal(setup, NOW)
    assert not notifier.send_pattern_signal(setup, NOW)
    assert len(bot.sent) == 1


def test_execution_after_pattern_is_not_suppressed():
    bot = FakeBot()
    notifier = Notifier(bot, display_timezone="UTC")
    setup = make_setup()
    notifier.send_pattern_signal(setup, NOW)
    notifier.send_execution_signal(setup, NOW)
    assert len(bot.sent) == 2


def test_invalidations_are_silent_by_default():
    bot = FakeBot()
    notifier = Notifier(bot, display_timezone="UTC", notify_invalidations=False)
    setup = make_setup()
    setup.invalidation_reason = "4th red pullback candle"
    assert not notifier.send_invalidated_signal(setup, NOW)
    assert bot.sent == []


def test_channel_chat_id_is_normalized():
    assert normalize_chat_id("1004313011768") == "-1004313011768"
    assert normalize_chat_id("-1004313011768") == "-1004313011768"
    assert normalize_chat_id("123456789") == "123456789"
    assert normalize_chat_id("@mychannel") == "@mychannel"


def test_phase1_disabled_others_still_send():
    bot = FakeBot()
    notifier = Notifier(bot, display_timezone="UTC", notify_phase1=False)
    setup = make_setup()
    assert not notifier.send_pattern_signal(setup, NOW)
    assert notifier.send_pullback_signal(setup, NOW)
    assert notifier.send_execution_signal(setup, NOW)
    assert len(bot.sent) == 2


def test_phase2_disabled_but_phase1_and_phase3_still_send():
    bot = FakeBot()
    notifier = Notifier(bot, display_timezone="UTC", notify_phase2=False)
    setup = make_setup()
    assert notifier.send_pattern_signal(setup, NOW)
    assert not notifier.send_pullback_signal(setup, NOW)
    assert notifier.send_execution_signal(setup, NOW)
    assert len(bot.sent) == 2


def test_phase3_disabled_but_phase1_and_phase2_still_send():
    bot = FakeBot()
    notifier = Notifier(bot, display_timezone="UTC", notify_phase3=False)
    setup = make_setup()
    assert notifier.send_pattern_signal(setup, NOW)
    assert notifier.send_pullback_signal(setup, NOW)
    assert not notifier.send_execution_signal(setup, NOW)
    assert len(bot.sent) == 2


def test_phase1_is_duplicate_prevented_across_enabled_toggles():
    bot = FakeBot()
    notifier = Notifier(bot, display_timezone="UTC", notify_phase1=False)
    setup = make_setup()
    assert not notifier.send_pattern_signal(setup, NOW)
    assert not notifier.send_pattern_signal(setup, NOW)
    assert bot.sent == []


def test_m_pullback_message_contains_t_price():
    setup = make_setup()
    setup.pattern = Pattern.M
    text = format_pullback_signal(setup, NOW)
    assert "M PULLBACK FORMED" in text
    assert "T Price:" in text
    assert "T Breakout: ✓" in text


def test_signal_kind_values_cover_three_phases():
    assert SignalKind.PHASE1_PATTERN.value == "PHASE1_PATTERN"
    assert SignalKind.PHASE2_PULLBACK.value == "PHASE2_PULLBACK"
    assert SignalKind.PHASE3_EXECUTION.value == "PHASE3_EXECUTION"
