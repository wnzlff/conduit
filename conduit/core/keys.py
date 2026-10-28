"""Loading private keys in OpenSSH/PEM or PuTTY (.ppk) format."""

from __future__ import annotations

import os
from pathlib import Path

import paramiko
from paramiko.ssh_exception import PasswordRequiredException, SSHException

from conduit.core import ppk


class KeyPassphraseRequired(Exception):
    """The key is encrypted and no (or a wrong) passphrase was supplied."""

    def __init__(self, message: str, *, wrong: bool = False) -> None:
        super().__init__(message)
        self.wrong = wrong


class KeyLoadError(Exception):
    pass


def key_needs_passphrase(path: str | os.PathLike) -> bool:
    """Best-effort check whether a private key file is encrypted."""
    p = Path(path).expanduser()
    if ppk.is_ppk_file(p):
        return ppk.ppk_needs_passphrase(p)
    try:
        head = p.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return False
    if "ENCRYPTED" in head:
        return True
    if "OPENSSH PRIVATE KEY" in head:
        # New OpenSSH format keeps the cipher name inside the binary payload, so just try it.
        try:
            paramiko.PKey.from_path(str(p))
            return False
        except PasswordRequiredException:
            return True
        except Exception:
            return False
    return False


def load_private_key(path: str | os.PathLike, passphrase: str | None = None) -> paramiko.PKey:
    """Loads a private key. Supports OpenSSH (new and PEM) and PuTTY .ppk (v2/v3) files."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise KeyLoadError(f"Key file not found: {p}")
    if ppk.is_ppk_file(p):
        try:
            return ppk.load_ppk(p, passphrase or None)
        except ppk.PPKPassphraseRequired as exc:
            raise KeyPassphraseRequired(str(exc)) from exc
        except ppk.PPKBadPassphrase as exc:
            raise KeyPassphraseRequired(str(exc), wrong=True) from exc
        except ppk.PPKError as exc:
            raise KeyLoadError(str(exc)) from exc
    try:
        return paramiko.PKey.from_path(str(p), passphrase=passphrase or None)
    except PasswordRequiredException as exc:
        raise KeyPassphraseRequired("This key is protected by a passphrase.") from exc
    except SSHException as exc:
        msg = str(exc)
        lowered = msg.lower()
        if passphrase and ("bad password" in lowered or "incorrect" in lowered or "decrypt" in lowered):
            raise KeyPassphraseRequired("Wrong passphrase for this key.", wrong=True) from exc
        raise KeyLoadError(f"Could not load key {p.name}: {msg}") from exc
    except (OSError, ValueError) as exc:
        raise KeyLoadError(f"Could not load key {p.name}: {exc}") from exc
