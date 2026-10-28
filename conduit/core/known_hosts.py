"""Host key verification with an application-owned known_hosts file.

Unknown hosts are forwarded to a callback (the UI shows a dialog). Changed host keys are
always rejected and reported, like OpenSSH does.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path
from typing import Callable

import paramiko
from paramiko.hostkeys import HostKeys

log = logging.getLogger(__name__)


class HostKeyChanged(paramiko.SSHException):
    """The server presented a key that differs from the stored one."""


class HostKeyRejected(paramiko.SSHException):
    """The user declined an unknown host key."""


def fingerprint_sha256(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def hostkey_entry_name(hostname: str, port: int) -> str:
    return hostname if port == 22 else f"[{hostname}]:{port}"


# Decision callback: (hostname, key_type, fingerprint) -> "accept_save" | "accept_once" | "reject"
DecisionCallback = Callable[[str, str, str], str]


class PromptingHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, path: Path, decide: DecisionCallback) -> None:
        self.path = Path(path)
        self.decide = decide
        self.host_keys = HostKeys()
        if self.path.exists():
            try:
                self.host_keys.load(str(self.path))
            except OSError as exc:
                log.warning("Could not load known_hosts: %s", exc)

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        # paramiko already consulted the client's host keys (filled from our file in apply()).
        # Reaching this point means the host is unknown, or it has a different key of this type.
        existing = self.host_keys.lookup(hostname)
        if existing is not None and key.get_name() in existing:
            raise HostKeyChanged(
                f"Host key for {hostname} has changed!\n\n"
                f"Stored {key.get_name()} fingerprint: {fingerprint_sha256(existing[key.get_name()])}\n"
                f"Received fingerprint: {fingerprint_sha256(key)}\n\n"
                "This could be a man-in-the-middle attack. If the server was legitimately reinstalled, "
                f"remove the old entry from {self.path}."
            )
        decision = self.decide(hostname, key.get_name(), fingerprint_sha256(key))
        if decision == "reject":
            raise HostKeyRejected(f"Host key for {hostname} was rejected.")
        client._host_keys.add(hostname, key.get_name(), key)
        if decision == "accept_save":
            self.host_keys.add(hostname, key.get_name(), key)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.host_keys.save(str(self.path))
            except OSError as exc:
                log.warning("Could not save known_hosts: %s", exc)

    def apply(self, client: paramiko.SSHClient) -> None:
        """Installs this policy and the stored keys on a client."""
        if self.path.exists():
            try:
                client.load_host_keys(str(self.path))
            except OSError as exc:
                log.warning("Could not load known_hosts into client: %s", exc)
        client.set_missing_host_key_policy(self)
