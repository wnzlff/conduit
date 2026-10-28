"""Dialog listing PuTTY sessions found on this machine for import."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from conduit.core.models import Connection
from conduit.core.putty_import import PuttySession, list_putty_sessions
from conduit.core.storage import ConnectionStore


class PuttyImportDialog(QDialog):
    def __init__(self, store: ConnectionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.sessions: list[PuttySession] = list_putty_sessions()
        self.setWindowTitle("Import PuTTY sessions")
        self.setModal(True)
        self.resize(680, 460)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel("Import PuTTY sessions")
        title.setObjectName("title")
        layout.addWidget(title)
        hint = QLabel(
            "PuTTY never stores passwords, so only host, port, user name and key file are imported. "
            "Sessions whose name already exists are skipped."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Session", "Host", "User", "Key file"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table, 1)

        existing = {c.name.lower() for c in self.store.connections}
        for session in self.sessions:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(session.name)
            name_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            duplicate = session.name.lower() in existing
            name_item.setCheckState(Qt.CheckState.Unchecked if duplicate else Qt.CheckState.Checked)
            if duplicate:
                name_item.setToolTip("A connection with this name already exists.")
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(f"{session.host}:{session.port}" if session.port != 22 else session.host))
            self.table.setItem(row, 2, QTableWidgetItem(session.username))
            self.table.setItem(row, 3, QTableWidgetItem(session.key_path))

        bottom = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.setObjectName("flat")
        select_none = QPushButton("Select none")
        select_none.setObjectName("flat")
        select_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        select_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        bottom.addWidget(select_all)
        bottom.addWidget(select_none)
        bottom.addStretch(1)
        bottom.addWidget(QLabel("Put into group:"))
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItem("")
        self.group.addItems(self.store.groups)
        self.group.setCurrentText("Imported from PuTTY" if not self.store.groups else "")
        self.group.setMinimumWidth(180)
        bottom.addWidget(self.group)
        layout.addLayout(bottom)

        if not self.sessions:
            empty = QLabel("No PuTTY sessions were found for the current user.")
            empty.setObjectName("muted")
            layout.addWidget(empty)

        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_button.setObjectName("primary")
        self.import_button.setEnabled(bool(self.sessions))
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, state: Qt.CheckState) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def selected_connections(self) -> list[Connection]:
        group = self.group.currentText().strip()
        result = []
        for row, session in enumerate(self.sessions):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                result.append(session.to_connection(group))
        return result
