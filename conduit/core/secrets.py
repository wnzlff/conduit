"""Secret storage via the OS credential store (Windows Credential Manager, macOS Keychain,
Secret Service on Linux) using the ``keyring`` library."""

from __future__ import annotations

import logging

import keyring
import keyring.errors

from conduit import APP_NAME

log = logging.getLogger(__name__)

SERVICE = APP_NAME


class SecretStore:
    """Thin wrapper around keyring. All methods swallow backend errors and log them,
    so a missing/locked credential store never crashes the UI."""

    def __init__(self, service: str = SERVICE) -> None:
        self.service = service

    @staticmethod
    def _account(connection_id: str, kind: str) -> str:
        return f"{connection_id}:{kind}"

    def get(self, connection_id: str, kind: str) -> str | None:
        try:
            return keyring.get_password(self.service, self._account(connection_id, kind))
        except keyring.errors.KeyringError as exc:
            log.warning("keyring get failed: %s", exc)
            return None

    def set(self, connection_id: str, kind: str, value: str | None) -> None:
        account = self._account(connection_id, kind)
        try:
            if value:
                keyring.set_password(self.service, account, value)
            else:
                self.delete(connection_id, kind)
        except keyring.errors.KeyringError as exc:
            log.warning("keyring set failed: %s", exc)

    def delete(self, connection_id: str, kind: str) -> None:
        try:
            keyring.delete_password(self.service, self._account(connection_id, kind))
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError as exc:
            log.warning("keyring delete failed: %s", exc)

    # Convenience accessors -------------------------------------------------

    def get_password(self, connection_id: str) -> str | None:
        return self.get(connection_id, "password")

    def set_password(self, connection_id: str, value: str | None) -> None:
        self.set(connection_id, "password", value)

    def get_passphrase(self, connection_id: str) -> str | None:
        return self.get(connection_id, "passphrase")

    def set_passphrase(self, connection_id: str, value: str | None) -> None:
        self.set(connection_id, "passphrase", value)

    def delete_all(self, connection_id: str) -> None:
        self.delete(connection_id, "password")
        self.delete(connection_id, "passphrase")

    @staticmethod
    def backend_name() -> str:
        try:
            return type(keyring.get_keyring()).__name__
        except Exception:  # pragma: no cover - defensive
            return "unavailable"
