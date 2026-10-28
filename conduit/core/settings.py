"""User settings (font, theme, scrollback...) stored as JSON."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, asdict, fields
from pathlib import Path

log = logging.getLogger(__name__)


def _default_font() -> str:
    if sys.platform == "win32":
        return "Cascadia Mono"
    if sys.platform == "darwin":
        return "Menlo"
    return "DejaVu Sans Mono"


@dataclass
class Settings:
    theme: str = "dark"  # "dark" or "light"
    font_family: str = ""
    font_size: int = 11
    scrollback_lines: int = 5000
    cursor_blink: bool = True
    copy_on_select: bool = True
    paste_on_right_click: bool = True
    confirm_close_session: bool = True
    keepalive_seconds: int = 30
    terminal_type: str = "xterm-256color"
    window_geometry: str = ""  # base64 of QByteArray
    window_state: str = ""
    sidebar_width: int = 260
    show_sftp_by_default: bool = False

    def resolved_font_family(self) -> str:
        return self.font_family or _default_font()

    @classmethod
    def load(cls, path: Path) -> "Settings":
        settings = cls()
        if not path.exists():
            return settings
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read settings %s: %s", path, exc)
            return settings
        valid = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in valid:
                setattr(settings, key, value)
        return settings

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
