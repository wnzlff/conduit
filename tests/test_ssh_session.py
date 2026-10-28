"""Integration test: SshSession against an in-process paramiko SSH server."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import paramiko
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, Qt  # noqa: E402

from conduit.core.models import AuthMethod, Connection  # noqa: E402
from conduit.core.ssh_session import SessionCredentials, SshSession  # noqa: E402

USER, PASSWORD = "alice", "s3cret"


class _Server(paramiko.ServerInterface):
    def __init__(self) -> None:
        self.event = threading.Event()
        self.pty = None

    def check_auth_password(self, username: str, password: str) -> int:
        if username == USER and password == PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes) -> bool:
        self.pty = (term, width, height)
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight) -> bool:
        self.pty = (self.pty[0] if self.pty else "?", width, height)
        return True

    def check_channel_shell_request(self, channel) -> bool:
        self.event.set()
        return True


@pytest.fixture
def ssh_server():
    """Starts a one-shot echo SSH server on a random localhost port."""
    host_key = paramiko.Ed25519Key.generate() if hasattr(paramiko.Ed25519Key, "generate") else paramiko.RSAKey.generate(2048)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    state: dict = {"server": None, "received": b"", "stop": False}

    def serve() -> None:
        client, _ = sock.accept()
        transport = paramiko.Transport(client)
        transport.add_server_key(host_key)
        server = _Server()
        state["server"] = server
        transport.start_server(server=server)
        channel = transport.accept(10)
        if channel is None:
            return
        server.event.wait(10)
        channel.send(b"welcome\r\n$ ")
        while not state["stop"]:
            try:
                data = channel.recv(1024)
            except Exception:
                break
            if not data:
                break
            state["received"] += data
            channel.send(data.upper())
            if b"exit" in state["received"]:
                channel.send(b"\r\nbye\r\n")
                channel.close()
                break
        transport.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield port, state, host_key
    state["stop"] = True
    sock.close()


def _pump(app, predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)
    return predicate()


def test_full_session_roundtrip(qt_app, ssh_server, tmp_path: Path) -> None:
    port, state, host_key = ssh_server
    conn = Connection(name="t", host="127.0.0.1", port=port, username=USER, auth_method=AuthMethod.PASSWORD)
    session = SshSession(conn, SessionCredentials(password=PASSWORD), tmp_path / "known_hosts", cols=100, rows=30)

    received = bytearray()
    events: list[str] = []
    host_key_requests: list = []
    session.data_received.connect(lambda d: received.extend(d))
    session.connected.connect(lambda: events.append("connected"))
    session.disconnected.connect(lambda r: events.append(f"disconnected:{r}"))

    def on_host_key(request):
        host_key_requests.append(request)
        request.decision = "accept_save"

    session.host_key_requested.connect(on_host_key, Qt.ConnectionType.BlockingQueuedConnection)
    session.start()

    assert _pump(qt_app, lambda: "connected" in events), events
    assert host_key_requests and host_key_requests[0].fingerprint.startswith("SHA256:")
    assert (tmp_path / "known_hosts").exists()
    term = state["server"].pty[0]
    assert (term.decode() if isinstance(term, bytes) else term) == "xterm-256color"
    assert state["server"].pty[1:] == (100, 30)

    assert _pump(qt_app, lambda: b"$ " in received)
    session.write(b"hello\n")
    assert _pump(qt_app, lambda: b"HELLO" in received)

    session.resize(120, 40)
    assert _pump(qt_app, lambda: state["server"].pty[1:] == (120, 40))

    session.write(b"exit\n")
    assert _pump(qt_app, lambda: any(e.startswith("disconnected") for e in events)), events
    assert events[-1] == "disconnected:"
    assert b"bye" in received
    session.wait(2000)


def test_wrong_password_reports_auth_failure(qt_app, ssh_server, tmp_path: Path) -> None:
    port, _state, _ = ssh_server
    conn = Connection(name="t", host="127.0.0.1", port=port, username=USER, auth_method=AuthMethod.PASSWORD)
    session = SshSession(conn, SessionCredentials(password="wrong"), tmp_path / "known_hosts")
    events: list[str] = []
    prompts: list = []
    session.disconnected.connect(lambda r: events.append(r))
    session.host_key_requested.connect(lambda r: setattr(r, "decision", "accept_once"), Qt.ConnectionType.BlockingQueuedConnection)

    def on_credential(request):
        prompts.append(request.kind)
        request.cancelled = True  # user presses cancel on the retry prompt

    session.credential_requested.connect(on_credential, Qt.ConnectionType.BlockingQueuedConnection)
    session.start()
    assert _pump(qt_app, lambda: bool(events)), events
    assert events[0] == "cancelled"
    assert prompts == ["password"]
    assert not (tmp_path / "known_hosts").exists()


def test_rejected_host_key(qt_app, ssh_server, tmp_path: Path) -> None:
    port, _state, _ = ssh_server
    conn = Connection(name="t", host="127.0.0.1", port=port, username=USER, auth_method=AuthMethod.PASSWORD)
    session = SshSession(conn, SessionCredentials(password=PASSWORD), tmp_path / "known_hosts")
    events: list[str] = []
    session.disconnected.connect(lambda r: events.append(r))
    session.host_key_requested.connect(lambda r: setattr(r, "decision", "reject"), Qt.ConnectionType.BlockingQueuedConnection)
    session.start()
    assert _pump(qt_app, lambda: bool(events)), events
    assert "rejected" in events[0].lower()
