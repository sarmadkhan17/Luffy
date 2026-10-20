"""Telegram notifier — alerts + remote control transport (Phase 4 expands)."""
from __future__ import annotations

import logging

import requests

from ..core.config import Env

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self):
        self.token, self.chat_id = Env.telegram()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, silent: bool = False) -> bool:
        if not self.configured:
            log.debug(f"telegram unset; would send: {text[:80]}")
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text,
                      "parse_mode": "HTML", "disable_notification": silent},
                timeout=10)
            # HTTP success alone is not a Bot API delivery acknowledgement.
            return r.ok and r.json().get("ok") is True
        except Exception as e:
            # Request exceptions can include the URL, which embeds the token.
            log.warning("telegram send failed: %s", type(e).__name__)
            return False
