"""Data models for saved connections."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class AuthMethod(str, Enum):
    PASSWORD = "password"
    KEY = "key"
    AGENT = "agent"


@dataclass
class Connection:
    """A saved SSH connection. Secrets (password, key passphrase) are never stored here;
    see :mod:`conduit.core.secrets`."""

    name: str
    host: str
    port: int = 22
    username: str = ""
    auth_method: AuthMethod = AuthMethod.PASSWORD
    key_path: str = ""
    group: str = ""
    save_password: bool = False
    save_passphrase: bool = False
    color: str = ""
    notes: str = ""
    startup_command: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["auth_method"] = self.auth_method.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Connection":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        method = clean.get("auth_method", AuthMethod.PASSWORD.value)
        try:
            clean["auth_method"] = AuthMethod(method)
        except ValueError:
            clean["auth_method"] = AuthMethod.PASSWORD
        clean["port"] = int(clean.get("port", 22) or 22)
        return cls(**clean)

    @property
    def display_target(self) -> str:
        user = f"{self.username}@" if self.username else ""
        port = f":{self.port}" if self.port != 22 else ""
        return f"{user}{self.host}{port}"

    def copy(self, *, new_id: bool = True) -> "Connection":
        data = self.to_dict()
        if new_id:
            data["id"] = uuid.uuid4().hex
        return Connection.from_dict(data)
