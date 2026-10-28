"""Interactive SSH shell session running on a worker thread.

The session talks to the UI exclusively through Qt signals. Prompts that need an answer from the
user (unknown host key, missing password or key passphrase, keyboard-interactive challenges) are
emitted as request objects; the UI connects those signals with ``BlockingQueuedConnection`` so the
worker thread waits until the dialog has been answered.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException
from PySide6.QtCore import QThread, Signal

from conduit.core.keys import KeyLoadError, KeyPassphraseRequired, load_private_key
from conduit.core.known_hosts import HostKeyChanged, HostKeyRejected, PromptingHostKeyPolicy
from conduit.core.models import AuthMethod, Connection

log = logging.getLogger(__name__)

MAX_AUTH_ATTEMPTS = 3


@dataclass
class HostKeyRequest:
    hostname: str
    key_type: str
    fingerprint: str
    decision: str = "reject"  # filled in by the UI: accept_save | accept_once | reject


@dataclass
class CredentialRequest:
    """A prompt for a secret. ``kind`` is ``password``, ``passphrase`` or ``interactive``."""

    kind: str
    title: str
    prompt: str
    echo: bool = False
    allow_remember: bool = False
    value: str | None = None
    remember: bool = False
    cancelled: bool = False


@dataclass
class SessionCredentials:
    password: str | None = None
    passphrase: str | None = None
    # Set by the session when the user ticked "remember" in a prompt, so the UI can persist it.
    remember_password: bool = False
    remember_passphrase: bool = False
    extra: dict[str, str] = field(default_factory=dict)


class SessionCancelled(Exception):
    pass


class SshSession(QThread):
    connected = Signal()
    data_received = Signal(bytes)
    disconnected = Signal(str)  # human readable reason ("" for normal exit)
    status = Signal(str)
    host_key_requested = Signal(object)  # HostKeyRequest
    credential_requested = Signal(object)  # CredentialRequest

    def __init__(
        self,
        connection: Connection,
        credentials: SessionCredentials,
        known_hosts_path: Path,
        *,
        term: str = "xterm-256color",
        cols: int = 80,
        rows: int = 24,
        keepalive: int = 30,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.connection = connection
        self.credentials = credentials
        self.known_hosts_path = known_hosts_path
        self.term = term
        self.cols = max(2, cols)
        self.rows = max(1, rows)
        self.keepalive = keepalive
        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # Public API (called from the UI thread) ---------------------------------

    @property
    def client(self) -> paramiko.SSHClient | None:
        return self._client

    @property
    def is_open(self) -> bool:
        ch = self._channel
        return ch is not None and not ch.closed and not self._stop.is_set()

    def write(self, data: bytes) -> None:
        ch = self._channel
        if ch is None or ch.closed:
            return
        try:
            ch.sendall(data)
        except (OSError, SSHException) as exc:
            log.debug("write failed: %s", exc)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(2, cols), max(1, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        ch = self._channel
        if ch is not None and not ch.closed:
            try:
                ch.resize_pty(width=cols, height=rows)
            except (OSError, SSHException) as exc:
                log.debug("resize failed: %s", exc)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            ch, client = self._channel, self._client
        for closer in (ch, client):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:  # pragma: no cover - best effort
                pass

    def open_sftp(self) -> paramiko.SFTPClient:
        if self._client is None:
            raise SSHException("Not connected")
        return self._client.open_sftp()

    # Worker thread ---------------------------------------------------------

    def run(self) -> None:  # noqa: C901 - straightforward state machine
        conn = self.connection
        reason = ""
        try:
            pkey = self._resolve_key()
            client = paramiko.SSHClient()
            PromptingHostKeyPolicy(self.known_hosts_path, self._ask_host_key).apply(client)
            with self._lock:
                self._client = client
            self.status.emit(f"Connecting to {conn.host}:{conn.port} ...")
            self._connect_with_auth(client, pkey)
            transport = client.get_transport()
            if transport is None:
                raise SSHException("Transport unavailable after connect")
            if self.keepalive > 0:
                transport.set_keepalive(self.keepalive)
            self.status.emit("Opening shell ...")
            channel = client.invoke_shell(term=self.term, width=self.cols, height=self.rows)
            channel.settimeout(0.25)
            with self._lock:
                self._channel = channel
            self.connected.emit()
            if conn.startup_command.strip():
                channel.sendall((conn.startup_command.strip() + "\n").encode("utf-8"))
            self._pump(channel)
        except SessionCancelled:
            reason = "cancelled"
        except HostKeyRejected:
            reason = "Host key rejected."
        except HostKeyChanged as exc:
            reason = str(exc)
        except AuthenticationException as exc:
            reason = f"Authentication failed: {exc or 'invalid credentials'}"
        except (KeyLoadError, KeyPassphraseRequired) as exc:
            reason = f"Key error: {exc}"
        except (socket.timeout, TimeoutError):
            reason = f"Connection to {conn.host}:{conn.port} timed out."
        except socket.gaierror:
            reason = f"Could not resolve host name '{conn.host}'."
        except ConnectionRefusedError:
            reason = f"Connection refused by {conn.host}:{conn.port}."
        except (OSError, SSHException) as exc:
            reason = f"Connection error: {exc}"
        except Exception as exc:  # pragma: no cover - last resort so the thread never dies silently
            log.exception("Unexpected error in SSH session")
            reason = f"Unexpected error: {exc}"
        finally:
            self.close()
        self.disconnected.emit(reason)

    def _pump(self, channel: paramiko.Channel) -> None:
        while not self._stop.is_set():
            try:
                data = channel.recv(65536)
            except socket.timeout:
                if channel.closed or channel.exit_status_ready() and not channel.recv_ready():
                    break
                continue
            except (OSError, SSHException):
                break
            if not data:
                break
            self.data_received.emit(data)
        # Drain whatever is left so the final output (e.g. "logout") is shown.
        try:
            while channel.recv_ready():
                data = channel.recv(65536)
                if not data:
                    break
                self.data_received.emit(data)
        except Exception:
            pass

    # Authentication helpers ------------------------------------------------

    def _resolve_key(self) -> paramiko.PKey | None:
        conn = self.connection
        if conn.auth_method != AuthMethod.KEY:
            return None
        if not conn.key_path:
            raise KeyLoadError("No key file configured for this connection.")
        passphrase = self.credentials.passphrase
        for attempt in range(MAX_AUTH_ATTEMPTS):
            try:
                self.status.emit("Loading private key ...")
                return load_private_key(conn.key_path, passphrase)
            except KeyPassphraseRequired as exc:
                prompt = "Wrong passphrase, try again." if exc.wrong else f"Passphrase for {Path(conn.key_path).name}:"
                request = CredentialRequest(
                    kind="passphrase",
                    title="Key passphrase",
                    prompt=prompt,
                    allow_remember=True,
                )
                self._ask_credential(request)
                passphrase = request.value or ""
                self.credentials.passphrase = passphrase
                self.credentials.remember_passphrase = request.remember
                if attempt == MAX_AUTH_ATTEMPTS - 1:
                    raise
        raise KeyPassphraseRequired("Too many failed passphrase attempts.")

    def _connect_with_auth(self, client: paramiko.SSHClient, pkey: paramiko.PKey | None) -> None:
        conn = self.connection
        password = self.credentials.password
        use_agent = conn.auth_method == AuthMethod.AGENT
        for attempt in range(MAX_AUTH_ATTEMPTS):
            if conn.auth_method == AuthMethod.PASSWORD and not password:
                request = CredentialRequest(
                    kind="password",
                    title="Password",
                    prompt=f"Password for {conn.username or 'user'}@{conn.host}:",
                    allow_remember=True,
                )
                self._ask_credential(request)
                password = request.value or ""
                self.credentials.password = password
                self.credentials.remember_password = request.remember
            try:
                client.connect(
                    conn.host,
                    port=conn.port,
                    username=conn.username or None,
                    password=password or None,
                    pkey=pkey,
                    allow_agent=use_agent,
                    look_for_keys=use_agent,
                    timeout=20,
                    banner_timeout=30,
                    auth_timeout=60,
                )
                if attempt > 0 or conn.auth_method != AuthMethod.PASSWORD:
                    self.status.emit("Authenticated.")
                return
            except SSHException as exc:
                # AuthenticationException, or "No authentication methods available" when the
                # server only offers keyboard-interactive and we had no password to try.
                transport = client.get_transport()
                if transport is not None and transport.is_active() and not transport.is_authenticated():
                    if self._try_interactive(transport, password):
                        return
                is_auth_error = isinstance(exc, AuthenticationException) or "authentication" in str(exc).lower()
                if not is_auth_error:
                    raise
                if conn.auth_method != AuthMethod.PASSWORD or attempt == MAX_AUTH_ATTEMPTS - 1:
                    raise AuthenticationException(str(exc) or "invalid credentials") from exc
                self.status.emit("Authentication failed, retrying ...")
                password = None
                self.credentials.password = None
                client.close()
                client = self._reset_client(client)
        raise AuthenticationException("Too many failed attempts")

    def _reset_client(self, old: paramiko.SSHClient) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        PromptingHostKeyPolicy(self.known_hosts_path, self._ask_host_key).apply(client)
        with self._lock:
            self._client = client
        return client

    def _try_interactive(self, transport: paramiko.Transport, password: str | None) -> bool:
        conn = self.connection

        def handler(title: str, instructions: str, prompts: list[tuple[str, bool]]) -> list[str]:
            answers: list[str] = []
            for prompt_text, echo in prompts:
                lowered = prompt_text.lower()
                if password and "password" in lowered and not answers:
                    answers.append(password)
                    continue
                request = CredentialRequest(
                    kind="interactive",
                    title=title or f"{conn.host} asks",
                    prompt=(instructions + "\n" if instructions else "") + prompt_text,
                    echo=echo,
                )
                self._ask_credential(request)
                answers.append(request.value or "")
            return answers

        try:
            transport.auth_interactive(conn.username or "", handler)
        except AuthenticationException:
            return False
        except SessionCancelled:
            raise
        except SSHException as exc:
            log.debug("keyboard-interactive failed: %s", exc)
            return False
        return transport.is_authenticated()

    # UI round-trips --------------------------------------------------------

    def _ask_host_key(self, hostname: str, key_type: str, fingerprint: str) -> str:
        request = HostKeyRequest(hostname, key_type, fingerprint)
        self.host_key_requested.emit(request)  # blocking queued connection
        return request.decision

    def _ask_credential(self, request: CredentialRequest) -> None:
        if self._stop.is_set():
            raise SessionCancelled()
        self.credential_requested.emit(request)  # blocking queued connection
        if request.cancelled or self._stop.is_set():
            raise SessionCancelled()


def wait_for(thread: QThread, timeout_ms: int = 3000) -> None:
    """Waits for a session thread to finish, polling so a stuck socket cannot hang the UI forever."""
    deadline = time.monotonic() + timeout_ms / 1000
    while thread.isRunning() and time.monotonic() < deadline:
        thread.wait(50)
