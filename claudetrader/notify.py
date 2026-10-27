"""Optional Telegram notifications. Silent no-op when not configured."""

from __future__ import annotations

import json
import urllib.request


class Notifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None, echo: bool = True):
        self.token, self.chat_id, self.echo = token, chat_id, echo
        self.sent: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        self.sent.append(text)
        if self.echo:
            print(f"[notify] {text}")
        if not self.enabled:
            return
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data=json.dumps({"chat_id": self.chat_id, "text": text}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # never let a notification failure stop trading logic
            print(f"[notify] telegram failed: {type(e).__name__}: {e}")
