"""Dialog to create or edit a saved connection."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from conduit.core.keys import key_needs_passphrase
from conduit.core.models import AuthMethod, Connection
from conduit.core.secrets import SecretStore
from conduit.core.storage import ConnectionStore

COLORS = [
    ("None", ""),
    ("Blue", "#7aa2f7"),
    ("Green", "#9ece6a"),
    ("Yellow", "#e0af68"),
    ("Red", "#f7768e"),
    ("Purple", "#bb9af7"),
    ("Cyan", "#7dcfff"),
]


class ConnectionDialog(QDialog):
    def __init__(
        self,
        store: ConnectionStore,
        secrets: SecretStore,
        connection: Connection | None = None,
        *,
        default_group: str = "",
        connect_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.secrets = secrets
        self.editing = connection is not None
        self.connect_mode = connect_mode
        self.connection = connection.copy(new_id=False) if connection else Connection(name="", host="", group=default_group)
        if connect_mode:
            self.setWindowTitle("Quick connect")
        else:
            self.setWindowTitle("Edit connection" if self.editing else "New connection")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build()
        self._load()

    # Layout ----------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel(self.windowTitle())
        title.setObjectName("title")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Production web server")
        form.addRow("Name", self.name)

        host_row = QHBoxLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP address or host name")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.port.setFixedWidth(90)
        host_row.addWidget(self.host, 1)
        host_row.addWidget(QLabel("Port"))
        host_row.addWidget(self.port)
        form.addRow("Host", host_row)

        self.username = QLineEdit()
        self.username.setPlaceholderText("root")
        form.addRow("Username", self.username)

        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItem("")
        for g in self.store.groups:
            self.group.addItem(g)
        self.group.lineEdit().setPlaceholderText("Optional, e.g. Customer A")
        form.addRow("Group", self.group)

        self.color = QComboBox()
        for label, value in COLORS:
            self.color.addItem(label, value)
        form.addRow("Tab colour", self.color)
        layout.addLayout(form)

        # Authentication
        auth_label = QLabel("Authentication")
        auth_label.setObjectName("subtitle")
        layout.addWidget(auth_label)
        self.auth = QComboBox()
        self.auth.addItem("Password", AuthMethod.PASSWORD.value)
        self.auth.addItem("Private key file", AuthMethod.KEY.value)
        self.auth.addItem("SSH agent (Pageant / ssh-agent)", AuthMethod.AGENT.value)
        layout.addWidget(self.auth)

        self.auth_pages = QStackedWidget()
        self.auth_pages.addWidget(self._password_page())
        self.auth_pages.addWidget(self._key_page())
        self.auth_pages.addWidget(self._agent_page())
        layout.addWidget(self.auth_pages)
        self.auth.currentIndexChanged.connect(self._auth_changed)

        self.startup = QLineEdit()
        self.startup.setPlaceholderText("Optional command to run after login, e.g. cd /var/www && ls")
        form2 = QFormLayout()
        form2.addRow("Startup command", self.startup)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes (shown as tooltip in the sidebar)")
        self.notes.setFixedHeight(60)
        form2.addRow("Notes", self.notes)
        layout.addLayout(form2)

        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)

        self.save_box = QCheckBox("Save this connection in the sidebar")
        self.save_box.setChecked(False)
        self.save_box.setVisible(self.connect_mode)
        layout.addWidget(self.save_box)

        buttons = QDialogButtonBox()
        label = "Connect" if self.connect_mode else ("Save" if self.editing else "Save and connect")
        self.save_button = buttons.addButton(label, QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setObjectName("primary")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        (self.host if self.connect_mode else self.name).setFocus()

    def _auth_changed(self, index: int) -> None:
        self.auth_pages.setCurrentIndex(index)
        page = self.auth_pages.currentWidget()
        if page is not None:
            self.auth_pages.setFixedHeight(page.sizeHint().height())
        self.adjustSize()

    def _password_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 4, 0, 0)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Leave empty to be asked on connect")
        form.addRow("Password", self.password)
        self.save_password = QCheckBox("Save password in the system credential store")
        self.save_password.setChecked(True)
        form.addRow("", self.save_password)
        return page

    def _key_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        self.key_path = QLineEdit()
        self.key_path.setPlaceholderText("OpenSSH (.key/.pem/id_*) or PuTTY (.ppk) private key")
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_key)
        row.addWidget(self.key_path, 1)
        row.addWidget(browse)
        form.addRow("Key file", row)
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase.setPlaceholderText("Only if the key is encrypted")
        form.addRow("Passphrase", self.passphrase)
        self.save_passphrase = QCheckBox("Save passphrase in the system credential store")
        self.save_passphrase.setChecked(True)
        form.addRow("", self.save_passphrase)
        self.key_hint = QLabel("")
        self.key_hint.setObjectName("muted")
        form.addRow("", self.key_hint)
        self.key_path.textChanged.connect(self._update_key_hint)
        return page

    def _agent_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        label = QLabel(
            "Keys are taken from a running SSH agent (Pageant on Windows, ssh-agent otherwise) "
            "and from the default OpenSSH key locations."
        )
        label.setWordWrap(True)
        label.setObjectName("muted")
        layout.addWidget(label)
        return page

    # Data ------------------------------------------------------------------

    def _load(self) -> None:
        c = self.connection
        self.name.setText(c.name)
        self.host.setText(c.host)
        self.port.setValue(c.port)
        self.username.setText(c.username)
        self.group.setCurrentText(c.group)
        self.color.setCurrentIndex(max(0, self.color.findData(c.color)))
        self.auth.setCurrentIndex(max(0, self.auth.findData(c.auth_method.value)))
        self._auth_changed(self.auth.currentIndex())
        self.key_path.setText(c.key_path)
        self.startup.setText(c.startup_command)
        self.notes.setPlainText(c.notes)
        self.save_password.setChecked(c.save_password or not self.editing)
        self.save_passphrase.setChecked(c.save_passphrase or not self.editing)
        if self.editing:
            if c.save_password:
                self.password.setText(self.secrets.get_password(c.id) or "")
            if c.save_passphrase:
                self.passphrase.setText(self.secrets.get_passphrase(c.id) or "")
        self._update_key_hint()

    def _browse_key(self) -> None:
        start = str(Path(self.key_path.text()).parent) if self.key_path.text() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select private key",
            start,
            "Private keys (*.ppk *.pem *.key id_* *);;All files (*)",
        )
        if path:
            self.key_path.setText(path)

    def _update_key_hint(self) -> None:
        path = self.key_path.text().strip()
        if not path:
            self.key_hint.setText("")
            return
        p = Path(path).expanduser()
        if not p.is_file():
            self.key_hint.setText("File not found.")
            return
        kind = "PuTTY key (.ppk)" if p.suffix.lower() == ".ppk" else "OpenSSH key"
        enc = "passphrase protected" if key_needs_passphrase(p) else "not encrypted"
        self.key_hint.setText(f"{kind}, {enc}.")

    def accept(self) -> None:
        name = self.name.text().strip()
        host = self.host.text().strip()
        if not host:
            self.error.setText("Please enter a host name or IP address.")
            self.host.setFocus()
            return
        if not name:
            name = host
        method = AuthMethod(self.auth.currentData())
        if method == AuthMethod.KEY and not self.key_path.text().strip():
            self.error.setText("Please choose a private key file.")
            self.key_path.setFocus()
            return
        c = self.connection
        c.name = name
        c.host = host
        c.port = self.port.value()
        c.username = self.username.text().strip()
        c.group = self.group.currentText().strip()
        c.color = self.color.currentData() or ""
        c.auth_method = method
        c.key_path = self.key_path.text().strip() if method == AuthMethod.KEY else ""
        c.startup_command = self.startup.text().strip()
        c.notes = self.notes.toPlainText().strip()
        c.save_password = method == AuthMethod.PASSWORD and self.save_password.isChecked() and bool(self.password.text())
        c.save_passphrase = method == AuthMethod.KEY and self.save_passphrase.isChecked() and bool(self.passphrase.text())
        if self.should_save():
            self.secrets.set_password(c.id, self.password.text() if c.save_password else None)
            self.secrets.set_passphrase(c.id, self.passphrase.text() if c.save_passphrase else None)
        else:
            c.save_password = c.save_passphrase = False
        super().accept()

    def should_save(self) -> bool:
        return not self.connect_mode or self.save_box.isChecked()

    def result_connection(self) -> Connection:
        return self.connection

    # Convenience: secrets entered but not saved are still used for this session.
    def entered_password(self) -> str | None:
        return self.password.text() or None

    def entered_passphrase(self) -> str | None:
        return self.passphrase.text() or None
