"""Small modal dialogs: secrets prompt, host key confirmation, settings."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from conduit.core.settings import Settings
from conduit.core.ssh_session import CredentialRequest, HostKeyRequest
from conduit.ui.icons import icon


def _primary(button: QPushButton) -> QPushButton:
    button.setObjectName("primary")
    return button


class CredentialDialog(QDialog):
    """Asks for a password / passphrase / keyboard-interactive answer."""

    def __init__(self, request: CredentialRequest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.request = request
        self.setWindowTitle(request.title)
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(icon("lock", self.palette().windowText().color().name(), 28).pixmap(28, 28))
        header.addWidget(icon_label)
        title = QLabel(request.title)
        title.setObjectName("title")
        header.addWidget(title, 1)
        layout.addLayout(header)

        prompt = QLabel(request.prompt)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        self.edit = QLineEdit()
        if not request.echo:
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.edit)

        self.show_box = QCheckBox("Show")
        self.show_box.toggled.connect(
            lambda on: self.edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        self.remember_box = QCheckBox("Remember in the system credential store")
        row = QHBoxLayout()
        if not request.echo:
            row.addWidget(self.show_box)
        row.addStretch(1)
        layout.addLayout(row)
        if request.allow_remember:
            layout.addWidget(self.remember_box)

        buttons = QDialogButtonBox()
        ok = _primary(buttons.addButton("OK", QDialogButtonBox.ButtonRole.AcceptRole))
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        ok.setDefault(True)
        layout.addWidget(buttons)
        self.edit.setFocus()

    @classmethod
    def ask(cls, request: CredentialRequest, parent: QWidget | None = None) -> None:
        dialog = cls(request, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            request.value = dialog.edit.text()
            request.remember = dialog.remember_box.isChecked()
            request.cancelled = False
        else:
            request.cancelled = True


class HostKeyDialog(QDialog):
    def __init__(self, request: HostKeyRequest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.request = request
        self.setWindowTitle("Unknown host")
        self.setModal(True)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(icon("shield", "#e0af68", 30).pixmap(30, 30))
        header.addWidget(icon_label)
        title = QLabel("The authenticity of this host can't be established")
        title.setObjectName("title")
        title.setWordWrap(True)
        header.addWidget(title, 1)
        layout.addLayout(header)

        text = QLabel(
            f"You are connecting to <b>{request.hostname}</b> for the first time.<br>"
            "Compare the fingerprint with the one your server administrator gave you before continuing."
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(text)

        form = QFormLayout()
        form.addRow("Key type:", QLabel(request.key_type))
        fp = QLineEdit(request.fingerprint)
        fp.setReadOnly(True)
        fp.setObjectName("mono")
        fp.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        form.addRow("Fingerprint:", fp)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        save = _primary(buttons.addButton("Trust and save", QDialogButtonBox.ButtonRole.AcceptRole))
        once = buttons.addButton("Connect once", QDialogButtonBox.ButtonRole.ActionRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        save.clicked.connect(lambda: self._finish("accept_save"))
        once.clicked.connect(lambda: self._finish("accept_once"))
        cancel.clicked.connect(lambda: self._finish("reject"))
        layout.addWidget(buttons)

    def _finish(self, decision: str) -> None:
        self.request.decision = decision
        self.accept() if decision != "reject" else self.reject()

    @classmethod
    def ask(cls, request: HostKeyRequest, parent: QWidget | None = None) -> None:
        dialog = cls(request, parent)
        dialog.exec()
        if dialog.result() != QDialog.DialogCode.Accepted:
            request.decision = "reject"


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("Settings")
        title.setObjectName("title")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        self.theme = QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        form.addRow("Theme", self.theme)

        self.font = QFontComboBox()
        self.font.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        self.font.setCurrentText(settings.resolved_font_family())
        form.addRow("Terminal font", self.font)

        self.font_size = QSpinBox()
        self.font_size.setRange(7, 32)
        self.font_size.setValue(settings.font_size)
        form.addRow("Font size", self.font_size)

        self.scrollback = QSpinBox()
        self.scrollback.setRange(100, 200000)
        self.scrollback.setSingleStep(500)
        self.scrollback.setValue(settings.scrollback_lines)
        form.addRow("Scrollback lines", self.scrollback)

        self.keepalive = QSpinBox()
        self.keepalive.setRange(0, 600)
        self.keepalive.setSuffix(" s")
        self.keepalive.setSpecialValueText("off")
        self.keepalive.setValue(settings.keepalive_seconds)
        form.addRow("Keep-alive interval", self.keepalive)

        self.term_type = QComboBox()
        self.term_type.setEditable(True)
        self.term_type.addItems(["xterm-256color", "xterm", "vt100", "linux"])
        self.term_type.setCurrentText(settings.terminal_type)
        form.addRow("Terminal type ($TERM)", self.term_type)
        layout.addLayout(form)

        self.cursor_blink = QCheckBox("Blinking cursor")
        self.cursor_blink.setChecked(settings.cursor_blink)
        self.copy_on_select = QCheckBox("Copy selection to clipboard automatically")
        self.copy_on_select.setChecked(settings.copy_on_select)
        self.paste_right = QCheckBox("Right click pastes (PuTTY style); otherwise shows a menu")
        self.paste_right.setChecked(settings.paste_on_right_click)
        self.confirm_close = QCheckBox("Ask before closing a connected tab")
        self.confirm_close.setChecked(settings.confirm_close_session)
        self.sftp_default = QCheckBox("Open the SFTP browser with every new session")
        self.sftp_default.setChecked(settings.show_sftp_by_default)
        for box in (self.cursor_blink, self.copy_on_select, self.paste_right, self.confirm_close, self.sftp_default):
            layout.addWidget(box)

        buttons = QDialogButtonBox()
        _primary(buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole))
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        s = self.settings
        s.theme = self.theme.currentData()
        s.font_family = self.font.currentText()
        s.font_size = self.font_size.value()
        s.scrollback_lines = self.scrollback.value()
        s.keepalive_seconds = self.keepalive.value()
        s.terminal_type = self.term_type.currentText().strip() or "xterm-256color"
        s.cursor_blink = self.cursor_blink.isChecked()
        s.copy_on_select = self.copy_on_select.isChecked()
        s.paste_on_right_click = self.paste_right.isChecked()
        s.confirm_close_session = self.confirm_close.isChecked()
        s.show_sftp_by_default = self.sftp_default.isChecked()
        super().accept()
