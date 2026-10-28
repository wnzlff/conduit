"""Persistent storage of connections (JSON file) and groups."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Iterable

from conduit.core.models import Connection

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class ConnectionStore:
    """Loads and saves connections from a JSON document.

    The document looks like::

        {"version": 1, "groups": ["Production", "Staging"], "connections": [ {...}, ... ]}

    Writes are atomic (temp file + replace), so a crash mid-write never corrupts the file.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.connections: list[Connection] = []
        self.groups: list[str] = []

    # Loading / saving ------------------------------------------------------

    def load(self) -> None:
        self.connections = []
        self.groups = []
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read %s: %s", self.path, exc)
            return
        for item in data.get("connections", []):
            if not isinstance(item, dict):
                log.warning("Skipping malformed connection entry %r", item)
                continue
            try:
                self.connections.append(Connection.from_dict(item))
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed connection %r: %s", item, exc)
        self.groups = [g for g in data.get("groups", []) if isinstance(g, str)]
        for conn in self.connections:
            if conn.group and conn.group not in self.groups:
                self.groups.append(conn.group)

    def save(self) -> None:
        data = {
            "version": SCHEMA_VERSION,
            "groups": list(self.groups),
            "connections": [c.to_dict() for c in self.connections],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".connections-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # Queries ---------------------------------------------------------------

    def get(self, connection_id: str) -> Connection | None:
        for conn in self.connections:
            if conn.id == connection_id:
                return conn
        return None

    def get_by_name(self, name: str) -> Connection | None:
        for conn in self.connections:
            if conn.name == name:
                return conn
        return None

    def by_group(self) -> dict[str, list[Connection]]:
        """Connections grouped by group name. Ungrouped connections use the key ``""``.
        Group order follows ``self.groups``; connections are sorted by name within a group."""
        result: dict[str, list[Connection]] = {g: [] for g in self.groups}
        result.setdefault("", [])
        for conn in self.connections:
            result.setdefault(conn.group, []).append(conn)
        for conns in result.values():
            conns.sort(key=lambda c: c.name.lower())
        return result

    def search(self, text: str) -> list[Connection]:
        needle = text.strip().lower()
        if not needle:
            return list(self.connections)
        return [
            c
            for c in self.connections
            if needle in c.name.lower()
            or needle in c.host.lower()
            or needle in c.username.lower()
            or needle in c.group.lower()
        ]

    # Mutations -------------------------------------------------------------

    def upsert(self, conn: Connection) -> None:
        if conn.group and conn.group not in self.groups:
            self.groups.append(conn.group)
        for i, existing in enumerate(self.connections):
            if existing.id == conn.id:
                self.connections[i] = conn
                return
        self.connections.append(conn)

    def add_many(self, conns: Iterable[Connection]) -> int:
        count = 0
        for conn in conns:
            self.upsert(conn)
            count += 1
        return count

    def remove(self, connection_id: str) -> Connection | None:
        for i, conn in enumerate(self.connections):
            if conn.id == connection_id:
                return self.connections.pop(i)
        return None

    def add_group(self, name: str) -> None:
        name = name.strip()
        if name and name not in self.groups:
            self.groups.append(name)

    def rename_group(self, old: str, new: str) -> None:
        new = new.strip()
        if not new or old == new:
            return
        self.groups = [new if g == old else g for g in self.groups]
        for conn in self.connections:
            if conn.group == old:
                conn.group = new

    def remove_group(self, name: str) -> None:
        """Removes a group. Its connections become ungrouped."""
        self.groups = [g for g in self.groups if g != name]
        for conn in self.connections:
            if conn.group == name:
                conn.group = ""

    def move_to_group(self, connection_id: str, group: str) -> None:
        conn = self.get(connection_id)
        if conn is None:
            return
        conn.group = group
        if group and group not in self.groups:
            self.groups.append(group)
