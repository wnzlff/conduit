"""Snippet management dialog and the quick snippet picker."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from conduit.core.snippets import Snippet, SnippetStore
from conduit.ui.icons import icon

ROLE_ID = Qt.ItemDataRole.UserRole


def _mono() -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(10)
    return font


class SnippetDialog(QDialog):
    """Create, edit and delete snippets. Changes are written when Save is pressed."""

    insert_requested = Signal(str, bool)  # body, run

    def __init__(self, store: SnippetStore, *, can_insert: bool, icon_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._icon_color = icon_color
        self._can_insert = can_insert
        self._working: list[Snippet] = [Snippet.from_dict(s.to_dict()) for s in store.sorted()]
        self._current: Snippet | None = None
        self.setWindowTitle("Snippets")
        self.setModal(True)
        self.resize(820, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel("Snippets")
        title.setObjectName("title")
        layout.addWidget(title)
        hint = QLabel(
            "Snippets are inserted at the prompt of the active session: from the Snippets menu, the quick picker "
            "(Ctrl+Shift+Space) or the suggestion popup when you type their name."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Filter")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        left_layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selection_changed)
        left_layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("New")
        self.add_button.setObjectName("flat")
        self.add_button.setIcon(icon("plus", icon_color))
        self.add_button.clicked.connect(self._add)
        self.dup_button = QPushButton("Duplicate")
        self.dup_button.setObjectName("flat")
        self.dup_button.setIcon(icon("copy", icon_color))
        self.dup_button.clicked.connect(self._duplicate)
        self.del_button = QPushButton("Delete")
        self.del_button.setObjectName("flat")
        self.del_button.setIcon(icon("trash", "#f7768e"))
        self.del_button.clicked.connect(self._delete)
        for b in (self.add_button, self.dup_button, self.del_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        left_layout.addLayout(buttons)
        splitter.addWidget(left)

        right = QWidget()
        form = QVBoxLayout(right)
        form.setContentsMargins(8, 0, 0, 0)
        form.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Short name, also used for suggestions while typing")
        self.name.textChanged.connect(self._store_field)
        form.addWidget(self.name)
        form.addWidget(QLabel("Description"))
        self.description = QLineEdit()
        self.description.setPlaceholderText("Optional")
        self.description.textChanged.connect(self._store_field)
        form.addWidget(self.description)
        form.addWidget(QLabel("Command text"))
        self.body = QPlainTextEdit()
        self.body.setFont(_mono())
        self.body.setPlaceholderText("One or more lines. Multi-line text is pasted as a block.")
        self.body.textChanged.connect(self._store_field)
        form.addWidget(self.body, 1)
        self.run = QCheckBox("Press Enter after inserting (run immediately)")
        self.run.toggled.connect(self._store_field)
        form.addWidget(self.run)
        self.insert_button = QPushButton("Insert into active session")
        self.insert_button.setIcon(icon("terminal", icon_color))
        self.insert_button.setEnabled(can_insert)
        self.insert_button.clicked.connect(self._insert)
        form.addWidget(self.insert_button, 0, Qt.AlignmentFlag.AlignLeft)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([260, 560])
        layout.addWidget(splitter, 1)

        box = QDialogButtonBox()
        save = box.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("primary")
        box.addButton(QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self._refill()
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._set_form_enabled(False)

    # List handling ---------------------------------------------------------

    def _refill(self) -> None:
        needle = self.search.text().strip().lower()
        current_id = self._current.id if self._current else None
        self.list.blockSignals(True)
        self.list.clear()
        for snippet in sorted(self._working, key=lambda s: s.name.lower()):
            if needle and needle not in snippet.name.lower() and needle not in snippet.body.lower():
                continue
            item = QListWidgetItem(icon("copy", self._icon_color), snippet.name or "(unnamed)")
            item.setData(ROLE_ID, snippet.id)
            item.setToolTip(snippet.body)
            self.list.addItem(item)
            if snippet.id == current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._selection_changed(self.list.currentItem(), None)

    def _selection_changed(self, current: QListWidgetItem | None, _previous) -> None:
        snippet = next((s for s in self._working if current is not None and s.id == current.data(ROLE_ID)), None)
        self._current = snippet
        self._set_form_enabled(snippet is not None)
        for widget in (self.name, self.description, self.body, self.run):
            widget.blockSignals(True)
        if snippet is not None:
            self.name.setText(snippet.name)
            self.description.setText(snippet.description)
            self.body.setPlainText(snippet.body)
            self.run.setChecked(snippet.run)
        else:
            self.name.clear()
            self.description.clear()
            self.body.clear()
            self.run.setChecked(False)
        for widget in (self.name, self.description, self.body, self.run):
            widget.blockSignals(False)

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (self.name, self.description, self.body, self.run, self.dup_button, self.del_button):
            widget.setEnabled(enabled)
        self.insert_button.setEnabled(enabled and self._can_insert)

    def _store_field(self, *_args) -> None:
        if self._current is None:
            return
        self._current.name = self.name.text().strip()
        self._current.description = self.description.text().strip()
        self._current.body = self.body.toPlainText()
        self._current.run = self.run.isChecked()
        item = self.list.currentItem()
        if item is not None:
            item.setText(self._current.name or "(unnamed)")
            item.setToolTip(self._current.body)

    def _add(self) -> None:
        snippet = Snippet(name="new snippet", body="")
        self._working.append(snippet)
        self._current = snippet
        self.search.clear()
        self._refill()
        self.name.setFocus()
        self.name.selectAll()

    def _duplicate(self) -> None:
        if self._current is None:
            return
        copy = Snippet.from_dict(self._current.to_dict())
        copy.id = Snippet(name="", body="").id
        copy.name = f"{self._current.name} (copy)"
        self._working.append(copy)
        self._current = copy
        self._refill()

    def _delete(self) -> None:
        if self._current is None:
            return
        self._working = [s for s in self._working if s.id != self._current.id]
        self._current = None
        self._refill()

    def _insert(self) -> None:
        if self._current is not None and self._current.body.strip():
            self.insert_requested.emit(self._current.body, self._current.run)

    def accept(self) -> None:
        self.store.snippets = [s for s in self._working if s.name.strip() or s.body.strip()]
        self.store.save()
        super().accept()


class SnippetPicker(QDialog):
    """Type-to-filter picker; Enter inserts the selected snippet."""

    def __init__(self, store: SnippetStore, icon_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("overlay")
        self.store = store
        self.choice: Snippet | None = None
        self.resize(460, 320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Insert snippet... (type to filter, Enter inserts)")
        self.search.textChanged.connect(self._refill)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _i: self._choose())
        layout.addWidget(self.list, 1)
        self._icon_color = icon_color
        self._refill()
        self.search.installEventFilter(self)
        self.search.setFocus()

    def _refill(self) -> None:
        needle = self.search.text().strip().lower()
        self.list.clear()
        for snippet in self.store.sorted():
            if needle and needle not in snippet.name.lower() and needle not in snippet.description.lower() and needle not in snippet.body.lower():
                continue
            label = snippet.name + (f"    {snippet.description}" if snippet.description else "")
            item = QListWidgetItem(icon("copy", self._icon_color), label)
            item.setData(ROLE_ID, snippet.id)
            item.setToolTip(snippet.body)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.search and event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._choose()
                return True
            if key == Qt.Key.Key_Down:
                self.list.setCurrentRow(min(self.list.count() - 1, self.list.currentRow() + 1))
                return True
            if key == Qt.Key.Key_Up:
                self.list.setCurrentRow(max(0, self.list.currentRow() - 1))
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def _choose(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.choice = self.store.get(item.data(ROLE_ID))
        self.accept()
