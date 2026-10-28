"""Import saved sessions from PuTTY.

PuTTY stores sessions in the Windows registry under
``HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\<name>`` with the session name
URL-encoded (``My%20Server``). Passwords are never stored by PuTTY, so they cannot be imported.
On Linux/macOS PuTTY writes ``~/.putty/sessions/<name>`` in a ``Key=Value`` text format, which is
supported too.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from conduit.core.models import AuthMethod, Connection


@dataclass
class PuttySession:
    name: str
    host: str
    port: int = 22
    username: str = ""
    key_path: str = ""
    protocol: str = "ssh"

    def to_connection(self, group: str = "") -> Connection:
        return Connection(
            name=self.name,
            host=self.host,
            port=self.port,
            username=self.username,
            auth_method=AuthMethod.KEY if self.key_path else AuthMethod.PASSWORD,
            key_path=self.key_path,
            group=group,
        )


def _session_from_values(name: str, values: dict[str, object]) -> PuttySession | None:
    host = str(values.get("HostName", "") or "").strip()
    if not host:
        return None
    try:
        port = int(values.get("PortNumber", 22) or 22)
    except (TypeError, ValueError):
        port = 22
    return PuttySession(
        name=unquote(name),
        host=host,
        port=port,
        username=str(values.get("UserName", "") or "").strip(),
        key_path=str(values.get("PublicKeyFile", "") or "").strip(),
        protocol=str(values.get("Protocol", "ssh") or "ssh").lower(),
    )


def _read_registry_sessions() -> list[PuttySession]:
    import winreg  # type: ignore[import-not-found]

    sessions: list[PuttySession] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SimonTatham\PuTTY\Sessions")
    except OSError:
        return sessions
    with root:
        index = 0
        while True:
            try:
                sub_name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            values: dict[str, object] = {}
            try:
                with winreg.OpenKey(root, sub_name) as sub:
                    value_index = 0
                    while True:
                        try:
                            vname, vdata, _vtype = winreg.EnumValue(sub, value_index)
                        except OSError:
                            break
                        value_index += 1
                        values[vname] = vdata
            except OSError:
                continue
            session = _session_from_values(sub_name, values)
            if session is not None:
                sessions.append(session)
    return sessions


def parse_session_file(name: str, text: str) -> PuttySession | None:
    """Parses the ``Key=Value`` format used by PuTTY on Unix (``~/.putty/sessions``)."""
    values: dict[str, object] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return _session_from_values(name, values)


def _read_unix_sessions(directory: Path) -> list[PuttySession]:
    sessions: list[PuttySession] = []
    if not directory.is_dir():
        return sessions
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        try:
            session = parse_session_file(entry.name, entry.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if session is not None:
            sessions.append(session)
    return sessions


def list_putty_sessions() -> list[PuttySession]:
    """Returns all SSH sessions PuTTY has saved for the current user (empty list if none)."""
    if sys.platform == "win32":
        sessions = _read_registry_sessions()
    else:
        sessions = _read_unix_sessions(Path(os.path.expanduser("~/.putty/sessions")))
    sessions = [s for s in sessions if s.protocol == "ssh" and s.name != "Default Settings"]
    sessions.sort(key=lambda s: s.name.lower())
    return sessions
