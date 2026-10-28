"""Loader for PuTTY private key files (.ppk).

Supports PPK format versions 2 and 3 as written by PuTTYgen, following the
format described in PuTTY's ``sshpubk.c`` and the "PPK file format" appendix
of the PuTTY manual:

* key types ``ssh-rsa``, ``ssh-ed25519``, ``ecdsa-sha2-nistp{256,384,521}``
  and ``ssh-dss`` (the latter only if the installed paramiko still ships
  ``DSSKey``; paramiko >= 4 removed DSA support);
* encryption ``none`` and ``aes256-cbc``;
* v2 key derivation via SHA-1 with an HMAC-SHA1 MAC, v3 key derivation via
  Argon2id/i/d with an HMAC-SHA256 MAC.

The key material is rebuilt with ``cryptography`` primitives, serialised to
the OpenSSH private key format and handed to paramiko, so the result is an
ordinary :class:`paramiko.PKey` usable with ``SSHClient.connect(pkey=...)``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import os
from dataclasses import dataclass, field

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

__all__ = [
    "PPKError",
    "PPKPassphraseRequired",
    "PPKBadPassphrase",
    "is_ppk_file",
    "ppk_needs_passphrase",
    "load_ppk",
]

_HEADER_PREFIX = "PuTTY-User-Key-File-"
_ENCRYPTIONS = ("none", "aes256-cbc")
_CURVES: dict[str, ec.EllipticCurve] = {
    "ecdsa-sha2-nistp256": ec.SECP256R1(),
    "ecdsa-sha2-nistp384": ec.SECP384R1(),
    "ecdsa-sha2-nistp521": ec.SECP521R1(),
}
_KEY_TYPES = ("ssh-rsa", "ssh-dss", "ssh-ed25519", *_CURVES)
_ARGON2_HEADERS = ("Key-Derivation", "Argon2-Memory", "Argon2-Passes",
                   "Argon2-Parallelism", "Argon2-Salt")


class PPKError(Exception):
    """The file is not a valid or supported PuTTY private key."""


class PPKPassphraseRequired(PPKError):
    """The key is encrypted and no passphrase was supplied."""


class PPKBadPassphrase(PPKError):
    """The MAC check failed after decrypting with the given passphrase."""


@dataclass
class _PPKFile:
    version: int
    algorithm: str
    encryption: str
    comment: str
    public_blob: bytes
    private_blob: bytes
    private_mac: bytes
    kdf: dict[str, str] = field(default_factory=dict)

    @property
    def encrypted(self) -> bool:
        return self.encryption != "none"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _ssh_string(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


class _Reader:
    """Sequential reader for SSH wire-format strings and mpints."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def string(self) -> bytes:
        end = self._pos + 4
        if end > len(self._data):
            raise PPKError("Truncated key blob")
        length = int.from_bytes(self._data[self._pos:end], "big")
        if end + length > len(self._data):
            raise PPKError("Truncated key blob")
        self._pos = end + length
        return self._data[end:self._pos]

    def mpint(self) -> int:
        return int.from_bytes(self.string(), "big", signed=True)


class _LineParser:
    """Reads ``Name: value`` headers and base64 blocks from a PPK file."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._pos = 0

    def header(self, expected: str | None = None) -> tuple[str, str]:
        if self._pos >= len(self._lines):
            raise PPKError(f"Unexpected end of file (expected {expected or 'header'})")
        # The value is everything after ": " verbatim (it feeds into the MAC).
        name, sep, value = self._lines[self._pos].partition(": ")
        if not sep or (expected is not None and name != expected):
            raise PPKError(f"Malformed line {self._pos + 1}: expected {expected or 'header'!r}")
        self._pos += 1
        return name, value

    def blob(self, count_header: str) -> bytes:
        value = self.header(count_header)[1]
        if not value.isdigit() or self._pos + int(value) > len(self._lines):
            raise PPKError(f"Invalid {count_header} value {value!r}")
        count = int(value)
        text = "".join(line.strip() for line in self._lines[self._pos:self._pos + count])
        self._pos += count
        try:
            return base64.b64decode(text, validate=True)
        except binascii.Error as exc:
            raise PPKError(f"Invalid base64 data after {count_header}") from exc


def _parse(path: str | os.PathLike) -> _PPKFile:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise PPKError(f"Cannot read key file: {exc}") from exc

    p = _LineParser(lines)
    first, algorithm = p.header()
    if not first.startswith(_HEADER_PREFIX):
        raise PPKError("Not a PuTTY private key file")
    version = first[len(_HEADER_PREFIX):]
    if version not in ("2", "3"):
        raise PPKError(f"Unsupported PPK version {version!r} (only 2 and 3 are supported)")
    if algorithm not in _KEY_TYPES:
        raise PPKError(f"Unsupported key type {algorithm!r}")
    encryption = p.header("Encryption")[1]
    if encryption not in _ENCRYPTIONS:
        raise PPKError(f"Unsupported encryption {encryption!r}")
    comment = p.header("Comment")[1]
    public_blob = p.blob("Public-Lines")
    kdf: dict[str, str] = {}
    if version == "3" and encryption != "none":
        kdf = {name: p.header(name)[1] for name in _ARGON2_HEADERS}
    private_blob = p.blob("Private-Lines")
    try:
        private_mac = bytes.fromhex(p.header("Private-MAC")[1])
    except ValueError as exc:
        raise PPKError("Invalid Private-MAC value") from exc
    return _PPKFile(int(version), algorithm, encryption, comment,
                    public_blob, private_blob, private_mac, kdf)


# --------------------------------------------------------------------------
# Key derivation, decryption and MAC
# --------------------------------------------------------------------------

def _argon2_keys(kdf: dict[str, str], passphrase: bytes) -> tuple[bytes, bytes, bytes]:
    from argon2.low_level import Type, hash_secret_raw

    types = {"Argon2id": Type.ID, "Argon2i": Type.I, "Argon2d": Type.D}
    if kdf["Key-Derivation"] not in types:
        raise PPKError(f"Unsupported key derivation {kdf['Key-Derivation']!r}")
    try:
        out = hash_secret_raw(
            secret=passphrase,
            salt=bytes.fromhex(kdf["Argon2-Salt"]),
            time_cost=int(kdf["Argon2-Passes"]),
            memory_cost=int(kdf["Argon2-Memory"]),
            parallelism=int(kdf["Argon2-Parallelism"]),
            hash_len=80,
            type=types[kdf["Key-Derivation"]],
            version=0x13,
        )
    except ValueError as exc:
        raise PPKError(f"Invalid Argon2 parameters: {exc}") from exc
    return out[:32], out[32:48], out[48:]


def _derive(ppk: _PPKFile, passphrase: bytes) -> tuple[bytes, bytes, bytes]:
    """Return ``(cipher_key, iv, mac_key)``; cipher fields are empty if unencrypted."""
    if ppk.version == 2:
        mac_key = hashlib.sha1(b"putty-private-key-file-mac-key" + passphrase).digest()
        if not ppk.encrypted:
            return b"", b"", mac_key
        key = (hashlib.sha1(b"\x00\x00\x00\x00" + passphrase).digest()
               + hashlib.sha1(b"\x00\x00\x00\x01" + passphrase).digest())[:32]
        return key, bytes(16), mac_key
    if not ppk.encrypted:
        return b"", b"", b""
    return _argon2_keys(ppk.kdf, passphrase)


def _decrypt_and_verify(ppk: _PPKFile, passphrase: str | None) -> bytes:
    """Decrypt the private blob (if needed) and check the file MAC."""
    if ppk.encrypted and passphrase is None:
        raise PPKPassphraseRequired("This key is protected by a passphrase")
    secret = passphrase.encode("utf-8") if ppk.encrypted and passphrase else b""
    key, iv, mac_key = _derive(ppk, secret)

    private = ppk.private_blob
    if ppk.encrypted:
        if len(private) % 16:
            raise PPKError("Encrypted private blob is not a multiple of the block size")
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        private = decryptor.update(private) + decryptor.finalize()

    mac_data = b"".join(_ssh_string(part) for part in (
        ppk.algorithm.encode("ascii"), ppk.encryption.encode("ascii"),
        ppk.comment.encode("utf-8"), ppk.public_blob, private))
    digest = hashlib.sha1 if ppk.version == 2 else hashlib.sha256
    expected = hmac.new(mac_key, mac_data, digest).digest()
    if not hmac.compare_digest(expected, ppk.private_mac):
        if ppk.encrypted:
            raise PPKBadPassphrase("Wrong passphrase")
        raise PPKError("MAC check failed, file may be corrupt")
    return private


# --------------------------------------------------------------------------
# Key construction
# --------------------------------------------------------------------------

def _build_private_key(alg: str, public_blob: bytes, private_blob: bytes):
    """Rebuild a ``cryptography`` private key from the PPK blobs."""
    pub, priv = _Reader(public_blob), _Reader(private_blob)
    if pub.string() != alg.encode("ascii"):
        raise PPKError("Public key type does not match file header")

    if alg == "ssh-rsa":
        e, n = pub.mpint(), pub.mpint()
        d, p, q, iqmp = priv.mpint(), priv.mpint(), priv.mpint(), priv.mpint()
        return rsa.RSAPrivateNumbers(
            p=p, q=q, d=d,
            dmp1=rsa.rsa_crt_dmp1(d, p), dmq1=rsa.rsa_crt_dmq1(d, q), iqmp=iqmp,
            public_numbers=rsa.RSAPublicNumbers(e, n),
        ).private_key()
    if alg == "ssh-dss":
        p, q, g, y = pub.mpint(), pub.mpint(), pub.mpint(), pub.mpint()
        params = dsa.DSAParameterNumbers(p, q, g)
        return dsa.DSAPrivateNumbers(priv.mpint(), dsa.DSAPublicNumbers(y, params)).private_key()
    if alg in _CURVES:
        pub.string()  # curve name, implied by the algorithm
        public = ec.EllipticCurvePublicKey.from_encoded_point(_CURVES[alg], pub.string())
        return ec.EllipticCurvePrivateNumbers(priv.mpint(), public.public_numbers()).private_key()
    if alg == "ssh-ed25519":
        expected_public = pub.string()
        key = ed25519.Ed25519PrivateKey.from_private_bytes(priv.string())
        actual_public = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if actual_public != expected_public:
            raise PPKError("Ed25519 private key does not match public key")
        return key
    raise PPKError(f"Unsupported key type {alg!r}")


def _paramiko_class(alg: str) -> type[paramiko.PKey]:
    if alg == "ssh-rsa":
        return paramiko.RSAKey
    if alg == "ssh-ed25519":
        return paramiko.Ed25519Key
    if alg in _CURVES:
        return paramiko.ECDSAKey
    dss = getattr(paramiko, "DSSKey", None)
    if dss is None:
        raise PPKError("ssh-dss (DSA) keys are not supported by the installed paramiko version")
    return dss


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def is_ppk_file(path: str | os.PathLike) -> bool:
    """Return True if the file at *path* looks like a PuTTY private key."""
    try:
        with open(path, "rb") as fh:
            return fh.readline(256).startswith(_HEADER_PREFIX.encode("ascii"))
    except OSError:
        return False


def ppk_needs_passphrase(path: str | os.PathLike) -> bool:
    """Return True if the PPK file at *path* is encrypted."""
    return _parse(path).encrypted


def load_ppk(path: str | os.PathLike, passphrase: str | None = None) -> paramiko.PKey:
    """Load a PPK v2/v3 private key and return the equivalent paramiko key.

    Raises :class:`PPKPassphraseRequired` if the key is encrypted and no
    passphrase was given, :class:`PPKBadPassphrase` if the passphrase is
    wrong, and :class:`PPKError` for any other format problem.
    """
    ppk = _parse(path)
    private = _decrypt_and_verify(ppk, passphrase)
    key_class = _paramiko_class(ppk.algorithm)
    try:
        crypto_key = _build_private_key(ppk.algorithm, ppk.public_blob, private)
        pem = crypto_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode("ascii")
    except (ValueError, TypeError) as exc:
        raise PPKError(f"Invalid key data: {exc}") from exc
    try:
        pkey = key_class.from_private_key(io.StringIO(pem))
    except paramiko.SSHException as exc:
        raise PPKError(f"paramiko could not load the key: {exc}") from exc
    # paramiko's Ed25519Key loaded from a private key leaves _verifying_key
    # unset, so verify_ssh_sig() would crash; fill it in from the signing key.
    signing = getattr(pkey, "_signing_key", None)
    if getattr(pkey, "_verifying_key", False) is None and signing is not None:
        pkey._verifying_key = signing.verify_key
    return pkey
