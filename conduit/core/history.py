"""Command history shared by all sessions (used for suggestions)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class CommandHistory:
    def __init__(self, path: Path, limit: int = 1000) -> None:
        self.path = Path(path)
        self.limit = limit
        self.items: list[str] = []  # most recent first
        self._dirty = False

    def load(self) -> None:
        self.items = []
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read history %s: %s", self.path, exc)
            return
        self.items = [s for s in data.get("items", []) if isinstance(s, str)][: self.limit]

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"version": 1, "items": self.items}, ensure_ascii=False), encoding="utf-8")
            self._dirty = False
        except OSError as exc:
            log.warning("Could not save history: %s", exc)

    def add(self, command: str) -> None:
        command = command.strip()
        if len(command) < 2 or command.startswith(" "):
            return
        if command in self.items:
            self.items.remove(command)
        self.items.insert(0, command)
        del self.items[self.limit:]
        self._dirty = True

    def clear(self) -> None:
        self.items = []
        self._dirty = True
