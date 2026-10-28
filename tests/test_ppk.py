"""Tests for conduit.core.ppk using fixtures produced by a test-only PPK writer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import warnings
from pathlib import Path

import paramiko
import pytest
from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from conduit.core.ppk import (
    PPKBadPassphrase,
    PPKError,
    PPKPassphraseRequired,
    is_ppk_file,
    load_ppk,
    ppk_needs_passphrase,
)

ARGON2 = {"memory": 8192, "passes": 2, "parallelism": 1}
PASSPHRASE = "correct horse battery staple"


# --------------------------------------------------------------------------
# Test-only PPK writer (mirrors PuTTY's sshpubk.c)
# --------------------------------------------------------------------------

def _string(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def _mpint(value: int) -> bytes:
    return _string(value.to_bytes(value.bit_length() // 8 + 1, "big", signed=True))


def _blobs(key) -> tuple[str, bytes, bytes]:
    """Return (algorithm, public blob, private blob) for a cryptography key."""
    if isinstance(key, rsa.RSAPrivateKey):
        n = key.private_numbers()
        pub = _string(b"ssh-rsa") + _mpint(n.public_numbers.e) + _mpint(n.public_numbers.n)
        return "ssh-rsa", pub, _mpint(n.d) + _mpint(n.p) + _mpint(n.q) + _mpint(n.iqmp)
    if isinstance(key, dsa.DSAPrivateKey):
        n = key.private_numbers()
        pn = n.public_numbers.parameter_numbers
        pub = (_string(b"ssh-dss") + _mpint(pn.p) + _mpint(pn.q) + _mpint(pn.g)
               + _mpint(n.public_numbers.y))
        return "ssh-dss", pub, _mpint(n.x)
    if isinstance(key, ec.EllipticCurvePrivateKey):
        curve = {"secp256r1": "nistp256", "secp384r1": "nistp384",
                 "secp521r1": "nistp521"}[key.curve.name]
        alg = f"ecdsa-sha2-{curve}"
        point = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        pub = _string(alg.encode()) + _string(curve.encode()) + _string(point)
        return alg, pub, _mpint(key.private_numbers().private_value)
    if isinstance(key, ed25519.Ed25519PrivateKey):
        raw_pub = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        raw_priv = key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        return "ssh-ed25519", _string(b"ssh-ed25519") + _string(raw_pub), _string(raw_priv)
    raise TypeError(key)


def _b64_lines(data: bytes) -> list[str]:
    text = base64.b64encode(data).decode()
    return [text[i:i + 64] for i in range(0, len(text), 64)]


def write_ppk(path: Path, key, *, version: int = 3, passphrase: str | None = None,
              comment: str = "test-key", newline: str = "\n") -> Path:
    """Write *key* as a PPK v2/v3 file, encrypted with aes256-cbc if a passphrase is given."""
    alg, public, private = _blobs(key)
    encryption = "aes256-cbc" if passphrase is not None else "none"
    secret = (passphrase or "").encode()
    kdf_lines: list[str] = []

    if passphrase is not None:
        private += os.urandom(-len(private) % 16)
    if version == 2:
        mac_key = hashlib.sha1(b"putty-private-key-file-mac-key" + secret).digest()
        cipher_key = (hashlib.sha1(b"\0\0\0\0" + secret).digest()
                      + hashlib.sha1(b"\0\0\0\1" + secret).digest())[:32]
        iv, digest = bytes(16), hashlib.sha1
    else:
        digest, mac_key, cipher_key, iv = hashlib.sha256, b"", b"", b""
        if passphrase is not None:
            salt = os.urandom(16)
            out = hash_secret_raw(secret=secret, salt=salt, time_cost=ARGON2["passes"],
                                  memory_cost=ARGON2["memory"],
                                  parallelism=ARGON2["parallelism"], hash_len=80,
                                  type=Type.ID, version=0x13)
            cipher_key, iv, mac_key = out[:32], out[32:48], out[48:]
            kdf_lines = ["Key-Derivation: Argon2id",
                         f"Argon2-Memory: {ARGON2['memory']}",
                         f"Argon2-Passes: {ARGON2['passes']}",
                         f"Argon2-Parallelism: {ARGON2['parallelism']}",
                         f"Argon2-Salt: {salt.hex()}"]

    mac_data = b"".join(_string(x) for x in (
        alg.encode(), encryption.encode(), comment.encode(), public, private))
    mac = hmac.new(mac_key, mac_data, digest).hexdigest()
    if passphrase is not None:
        enc = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).encryptor()
        private = enc.update(private) + enc.finalize()

    pub_lines, priv_lines = _b64_lines(public), _b64_lines(private)
    lines = [f"PuTTY-User-Key-File-{version}: {alg}", f"Encryption: {encryption}",
             f"Comment: {comment}", f"Public-Lines: {len(pub_lines)}", *pub_lines,
             *kdf_lines, f"Private-Lines: {len(priv_lines)}", *priv_lines,
             f"Private-MAC: {mac}"]
    path.write_bytes((newline.join(lines) + newline).encode())
    return path


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------

def _make_dsa():
    return dsa.generate_private_key(1024)


KEY_FACTORIES = {
    "rsa": lambda: rsa.generate_private_key(65537, 2048),
    "ed25519": ed25519.Ed25519PrivateKey.generate,
    "p256": lambda: ec.generate_private_key(ec.SECP256R1()),
    "p384": lambda: ec.generate_private_key(ec.SECP384R1()),
    "p521": lambda: ec.generate_private_key(ec.SECP521R1()),
    "dsa": _make_dsa,
}
HAS_DSS = hasattr(paramiko, "DSSKey")


@pytest.fixture(scope="module")
def keys() -> dict:
    return {name: factory() for name, factory in KEY_FACTORIES.items()}


def openssh_b64(key) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # DSA OpenSSH serialisation is deprecated
        return key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        ).decode().split(" ")[1]


def assert_signs(pkey: paramiko.PKey) -> None:
    sig = pkey.sign_ssh_data(b"hello")
    assert pkey.verify_ssh_sig(b"hello", paramiko.Message(sig.asbytes()))


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("name", ["rsa", "ed25519", "p256", "p384", "p521"])
def test_unencrypted_roundtrip(tmp_path, keys, name, version):
    key = keys[name]
    path = write_ppk(tmp_path / f"{name}.ppk", key, version=version)
    pkey = load_ppk(path)
    assert pkey.get_base64() == openssh_b64(key)
    assert_signs(pkey)


@pytest.mark.parametrize("version", [2, 3])
def test_dsa(tmp_path, keys, version):
    path = write_ppk(tmp_path / "dsa.ppk", keys["dsa"], version=version)
    if not HAS_DSS:
        with pytest.raises(PPKError, match="ssh-dss"):
            load_ppk(path)
        return
    pkey = load_ppk(path)
    assert pkey.get_base64() == openssh_b64(keys["dsa"])
    assert_signs(pkey)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("name", ["rsa", "ed25519", "p384"])
def test_encrypted_roundtrip(tmp_path, keys, name, version):
    key = keys[name]
    path = write_ppk(tmp_path / f"{name}.ppk", key, version=version, passphrase=PASSPHRASE)
    assert ppk_needs_passphrase(path)
    pkey = load_ppk(path, PASSPHRASE)
    assert pkey.get_base64() == openssh_b64(key)
    assert_signs(pkey)


@pytest.mark.parametrize("version", [2, 3])
def test_missing_and_wrong_passphrase(tmp_path, keys, version):
    path = write_ppk(tmp_path / "k.ppk", keys["ed25519"], version=version, passphrase=PASSPHRASE)
    with pytest.raises(PPKPassphraseRequired):
        load_ppk(path)
    with pytest.raises(PPKBadPassphrase):
        load_ppk(path, "wrong")
    with pytest.raises(PPKBadPassphrase):
        load_ppk(path, "")


@pytest.mark.parametrize("version", [2, 3])
def test_tampered_public_blob(tmp_path, keys, version):
    path = write_ppk(tmp_path / "k.ppk", keys["rsa"], version=version)
    lines = path.read_text().splitlines()
    idx = next(i for i, line in enumerate(lines) if line.startswith("Public-Lines")) + 2
    blob = bytearray(base64.b64decode("".join(lines[idx - 1:idx + 1])))
    # Flip a byte inside the modulus and re-encode the first two lines in place.
    blob[40] ^= 0xFF
    text = base64.b64encode(bytes(blob)).decode()
    lines[idx - 1], lines[idx] = text[:64], text[64:128]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(PPKError, match="MAC"):
        load_ppk(path)


def test_is_ppk_file_and_needs_passphrase(tmp_path, keys):
    ppk = write_ppk(tmp_path / "k.ppk", keys["ed25519"])
    pem = tmp_path / "id_ed25519"
    pem.write_bytes(keys["ed25519"].private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()))
    assert is_ppk_file(ppk)
    assert is_ppk_file(str(ppk))
    assert not is_ppk_file(pem)
    assert not is_ppk_file(tmp_path / "does-not-exist.ppk")
    assert not ppk_needs_passphrase(ppk)


@pytest.mark.parametrize("version", [2, 3])
def test_crlf_line_endings(tmp_path, keys, version):
    path = write_ppk(tmp_path / "crlf.ppk", keys["rsa"], version=version,
                     passphrase=PASSPHRASE, newline="\r\n")
    assert b"\r\n" in path.read_bytes()
    pkey = load_ppk(path, PASSPHRASE)
    assert pkey.get_base64() == openssh_b64(keys["rsa"])


def test_comment_with_spaces_and_colons(tmp_path, keys):
    path = write_ppk(tmp_path / "k.ppk", keys["p256"], comment="me@host: work key ")
    assert load_ppk(path).get_base64() == openssh_b64(keys["p256"])


@pytest.mark.parametrize("header, message", [
    ("PuTTY-User-Key-File-1: ssh-rsa", "version"),
    ("PuTTY-User-Key-File-3: ssh-foo", "key type"),
])
def test_unsupported_header(tmp_path, keys, header, message):
    path = write_ppk(tmp_path / "k.ppk", keys["rsa"])
    lines = path.read_text().splitlines()
    lines[0] = header
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(PPKError, match=message):
        load_ppk(path)


def test_unsupported_encryption(tmp_path, keys):
    path = write_ppk(tmp_path / "k.ppk", keys["rsa"])
    path.write_text(path.read_text().replace("Encryption: none", "Encryption: aes128-ctr"))
    with pytest.raises(PPKError, match="encryption"):
        load_ppk(path)
