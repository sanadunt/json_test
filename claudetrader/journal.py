"""Append-only JSONL journal of every decision the engine makes."""

from __future__ import annotations

import json
from pathlib import Path


class Journal:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.entries: list[dict] = []

    def log(self, entry: dict) -> None:
        self.entries.append(entry)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
