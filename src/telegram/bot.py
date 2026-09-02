"""Minimal Telegram Bot API client (documented HTTPS endpoints only)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import requests

from src.utils.logging import get_logger

logger = get_logger(__name__)

API_BASE = "https://api.telegram.org"
#: Delays between send retries (network hiccups must not lose a signal).
RETRY_DELAYS = (1, 3, 8)


class TelegramError(RuntimeError):
    pass


@dataclass
class TelegramBot:
    token: str
    chat_id: str
    timeout: int = 15

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def _call(self, method: str, payload: dict) -> dict:
        if not self.configured:
            raise TelegramError("Telegram bot token / chat id missing")
        response = requests.post(self._url(method), json=payload, timeout=self.timeout)
        try:
            data = response.json()
        except ValueError as exc:  # noqa: PERF203
            raise TelegramError(f"Invalid Telegram response: {response.text[:200]}") from exc
        if not data.get("ok"):
            raise TelegramError(
                f"Telegram {method} failed ({data.get('error_code')}): {data.get('description')}"
            )
        return data["result"]

    # ---------------------------------------------------------------- sync
    def get_me(self) -> dict:
        return self._call("getMe", {})

    def get_chat(self) -> dict:
        return self._call("getChat", {"chat_id": self.chat_id})

    def send_message(self, text: str, disable_notification: bool = False) -> dict:
        return self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
                "disable_notification": disable_notification,
            },
        )

    def try_send_message(self, text: str) -> bool:
        """Never raises: a Telegram failure must not break signal generation.

        Transient network problems are retried; a rejection by Telegram
        (invalid chat, blocked bot, ...) fails immediately.
        """
        attempts = len(RETRY_DELAYS) + 1
        for attempt in range(attempts):
            try:
                self.send_message(text)
                if attempt:
                    logger.info("Telegram send succeeded on retry %s", attempt)
                return True
            except requests.RequestException as exc:
                if attempt == attempts - 1:
                    logger.error("Telegram send failed after %s attempts: %s", attempts, exc)
                    return False
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "Telegram network error (%s) - retrying in %ss", type(exc).__name__, delay
                )
                time.sleep(delay)
            except TelegramError as exc:
                logger.error("Telegram send rejected: %s", exc)
                return False
        return False

    # --------------------------------------------------------------- async
    async def async_send_message(self, text: str) -> bool:
        return await asyncio.to_thread(self.try_send_message, text)

    async def async_get_me(self) -> dict:
        return await asyncio.to_thread(self.get_me)


def build_bot(settings=None) -> TelegramBot:
    from config.settings import settings as default_settings

    cfg = settings or default_settings
    return TelegramBot(token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id)
