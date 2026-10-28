"""A tab holding one SSH session: terminal, status banner and optional SFTP panel."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from conduit.core.models import Connection
from conduit.core.paths import known_hosts_file
from conduit.core.secrets import SecretStore
from conduit.core.settings import Settings
from conduit.core.ssh_session import CredentialRequest, HostKeyRequest, SessionCredentials, SshSession, wait_for
from conduit.ui.dialogs import CredentialDialog, HostKeyDialog
from conduit.ui.sftp_panel import SftpPanel
from conduit.ui.terminal_widget import TerminalWidget
from conduit.ui.theme import Theme


class SessionTab(QWidget):
    title_changed = Signal(str)
    state_changed = Signal(str)  # connecting | connected | disconnected
    status_message = Signal(str)
    connection_updated = Signal()  # save_password/save_passphrase flags changed, store should be saved

    def __init__(
        self,
        connection: Connection,
        settings: Settings,
        secrets: SecretStore,
        theme: Theme,
        *,
        credentials: SessionCredentials | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.connection = connection
        self.settings = settings
        self.secrets = secrets
        self.theme = theme
        self.credentials = credentials or SessionCredentials()
        self.session: SshSession | None = None
        self.state = "disconnected"
        self._remote_title = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.banner = QWidget()
        self.banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 6, 12, 6)
        self.banner_label = QLabel("")
        self.banner_label.setWordWrap(True)
        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.setObjectName("primary")
        self.reconnect_button.clicked.connect(self.reconnect)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.close_session)
        banner_layout.addWidget(self.banner_label, 1)
        banner_layout.addWidget(self.reconnect_button)
        banner_layout.addWidget(self.cancel_button)
        layout.addWidget(self.banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.terminal = TerminalWidget(
            font_family=settings.resolved_font_family(),
            font_size=settings.font_size,
            scrollback=settings.scrollback_lines,
            palette=theme.terminal,
            copy_on_select=settings.copy_on_select,
            paste_on_right_click=settings.paste_on_right_click,
            cursor_blink=settings.cursor_blink,
        )
        self.terminal.data_to_send.connect(self._send)
        self.terminal.size_changed.connect(self._resized)
        self.terminal.title_changed.connect(self._remote_title_changed)
        self.splitter.addWidget(self.terminal)
        self.sftp: SftpPanel | None = None
        layout.addWidget(self.splitter, 1)
        self._set_banner("", None)

    # Public API ------------------------------------------------------------

    @property
    def title(self) -> str:
        return self.connection.name

    def start(self) -> None:
        if self.session is not None and self.session.isRunning():
            return
        conn = self.connection
        if conn.save_password and self.credentials.password is None:
            self.credentials.password = self.secrets.get_password(conn.id)
        if conn.save_passphrase and self.credentials.passphrase is None:
            self.credentials.passphrase = self.secrets.get_passphrase(conn.id)
        self.session = SshSession(
            conn,
            self.credentials,
            known_hosts_file(),
            term=self.settings.terminal_type,
            cols=self.terminal.cols,
            rows=self.terminal.rows,
            keepalive=self.settings.keepalive_seconds,
            parent=self,
        )
        s = self.session
        s.connected.connect(self._on_connected)
        s.data_received.connect(self.terminal.feed)
        s.disconnected.connect(self._on_disconnected)
        s.status.connect(self._on_status)
        s.host_key_requested.connect(self._on_host_key, Qt.ConnectionType.BlockingQueuedConnection)
        s.credential_requested.connect(self._on_credential, Qt.ConnectionType.BlockingQueuedConnection)
        self._set_state("connecting")
        self._set_banner(f"Connecting to {conn.display_target} ...", "cancel")
        self.terminal.set_connected(True)
        s.start()
        self.terminal.setFocus()

    def reconnect(self) -> None:
        self.close_session()
        self.terminal.write_message(f"Reconnecting to {self.connection.display_target} ...", color=8)
        self.start()

    def close_session(self) -> None:
        if self.sftp is not None:
            self.sftp.stop()
        if self.session is not None:
            session = self.session
            session.close()
            wait_for(session, 2000)
            if session.isRunning():
                session.terminate()
            self.session = None
        if self.state != "disconnected":
            self._set_state("disconnected")

    def is_connected(self) -> bool:
        return self.state == "connected"

    def toggle_sftp(self) -> bool:
        if self.sftp is None:
            self.sftp = SftpPanel(self._open_sftp)
            self.sftp.set_icon_color(self.theme.text_muted)
            self.sftp.closed.connect(self.toggle_sftp)
            self.sftp.message.connect(self.status_message.emit)
            self.splitter.addWidget(self.sftp)
            self.splitter.setStretchFactor(0, 3)
            self.splitter.setStretchFactor(1, 2)
            total = max(self.width(), 900)
            self.splitter.setSizes([int(total * 0.6), int(total * 0.4)])
            if self.is_connected():
                self.sftp.start()
            return True
        visible = not self.sftp.isVisible()
        self.sftp.setVisible(visible)
        if visible and self.is_connected() and self.sftp.worker is None:
            self.sftp.start()
        if not visible:
            self.sftp.stop()
        return visible

    def sftp_visible(self) -> bool:
        return self.sftp is not None and self.sftp.isVisible()

    def apply_settings(self, settings: Settings, theme: Theme) -> None:
        self.settings = settings
        self.theme = theme
        self.terminal.set_font(settings.resolved_font_family(), settings.font_size)
        self.terminal.set_palette(theme.terminal)
        self.terminal.copy_on_select = settings.copy_on_select
        self.terminal.paste_on_right_click = settings.paste_on_right_click
        self.terminal.set_cursor_blink(settings.cursor_blink)
        self.terminal.set_scrollback(settings.scrollback_lines)
        if self.sftp is not None:
            self.sftp.set_icon_color(theme.text_muted)
        self._apply_banner_style()

    # Internals -------------------------------------------------------------

    def _open_sftp(self):
        if self.session is None:
            raise RuntimeError("Not connected")
        return self.session.open_sftp()

    def _send(self, data: bytes) -> None:
        if self.session is not None:
            self.session.write(data)

    def _resized(self, cols: int, rows: int) -> None:
        if self.session is not None:
            self.session.resize(cols, rows)

    def _remote_title_changed(self, title: str) -> None:
        self._remote_title = title
        self.title_changed.emit(self.display_title())

    def display_title(self) -> str:
        return self.connection.name

    def tooltip(self) -> str:
        parts = [self.connection.display_target]
        if self._remote_title:
            parts.append(self._remote_title)
        return "\n".join(parts)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def _apply_banner_style(self) -> None:
        t = self.theme
        self.banner.setStyleSheet(
            f"QWidget#banner {{ background: {t.surface_alt}; border-bottom: 1px solid {t.border}; }}"
            f"QWidget#banner QLabel {{ color: {t.text}; }}"
        )

    def _set_banner(self, text: str, mode: str | None) -> None:
        """mode: None (hidden), 'cancel' (while connecting) or 'reconnect' (after disconnect)."""
        self._apply_banner_style()
        self.banner_label.setText(text)
        self.reconnect_button.setVisible(mode == "reconnect")
        self.cancel_button.setVisible(mode == "cancel")
        self.banner.setVisible(mode is not None)

    # Session callbacks (UI thread) -----------------------------------------

    def _on_status(self, text: str) -> None:
        self.banner_label.setText(text)
        self.status_message.emit(text)

    def _on_connected(self) -> None:
        self._set_state("connected")
        self._set_banner("", None)
        self.status_message.emit(f"Connected to {self.connection.display_target}")
        self._persist_remembered_secrets()
        if self.session is not None:
            self.session.resize(self.terminal.cols, self.terminal.rows)
        if self.sftp is not None and self.sftp.isVisible():
            self.sftp.start()
        elif self.settings.show_sftp_by_default and self.sftp is None:
            self.toggle_sftp()
        self.terminal.setFocus()

    def _on_disconnected(self, reason: str) -> None:
        was_connected = self.state == "connected"
        self._set_state("disconnected")
        self.terminal.set_connected(False)
        if self.sftp is not None:
            self.sftp.stop()
        if reason == "cancelled":
            self._set_banner("Connection cancelled.", "reconnect")
            return
        if reason:
            self.terminal.write_message(reason, color=1)
            self._set_banner(reason.splitlines()[0], "reconnect")
            self.status_message.emit(reason.splitlines()[0])
        else:
            self.terminal.write_message("Connection closed.", color=8)
            self._set_banner("Connection closed." if was_connected else "Disconnected.", "reconnect")
            self.status_message.emit(f"Disconnected from {self.connection.display_target}")

    def _on_host_key(self, request: HostKeyRequest) -> None:
        HostKeyDialog.ask(request, self.window())

    def _on_credential(self, request: CredentialRequest) -> None:
        CredentialDialog.ask(request, self.window())

    def _persist_remembered_secrets(self) -> None:
        creds = self.credentials
        conn = self.connection
        changed = False
        if creds.remember_password and creds.password:
            self.secrets.set_password(conn.id, creds.password)
            conn.save_password = True
            creds.remember_password = False
            changed = True
        if creds.remember_passphrase and creds.passphrase:
            self.secrets.set_passphrase(conn.id, creds.passphrase)
            conn.save_passphrase = True
            creds.remember_passphrase = False
            changed = True
        if changed:
            self.connection_updated.emit()
