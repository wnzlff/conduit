"""Sidebar with grouped, searchable saved connections."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from conduit.core.models import Connection
from conduit.core.storage import ConnectionStore
from conduit.ui.icons import icon

ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_ID = Qt.ItemDataRole.UserRole + 1


class Sidebar(QWidget):
    connect_requested = Signal(object)  # Connection
    split_requested = Signal(object)  # Connection: open as a pane in the current tab
    edit_requested = Signal(object)  # Connection
    new_requested = Signal(str)  # group name
    delete_requested = Signal(object)  # Connection
    import_requested = Signal()
    store_changed = Signal()

    def __init__(self, store: ConnectionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("sidebar")
        self._icon_color = "#8a93a6"
        self._expanded: dict[str, bool] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Connections")
        title.setObjectName("subtitle")
        header.addWidget(title, 1)
        self.add_button = QToolButton()
        self.add_button.setObjectName("flat")
        self.add_button.setToolTip("New connection (Ctrl+Shift+N)")
        self.add_button.clicked.connect(lambda: self.new_requested.emit(self._current_group()))
        self.group_button = QToolButton()
        self.group_button.setObjectName("flat")
        self.group_button.setToolTip("New group")
        self.group_button.clicked.connect(self._new_group)
        header.addWidget(self.add_button)
        header.addWidget(self.group_button)
        layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search  (Ctrl+Shift+K)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemActivated.connect(self._activated)
        self.tree.itemExpanded.connect(lambda item: self._remember(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._remember(item, False))
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.model().rowsInserted.connect(self._after_drop)
        layout.addWidget(self.tree, 1)

        self.empty_hint = QLabel("No connections yet.\nClick + or import your PuTTY sessions.")
        self.empty_hint.setObjectName("muted")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint)

        self.set_icon_color(self._icon_color)
        self.refresh()

    # Public ----------------------------------------------------------------

    def set_icon_color(self, color: str) -> None:
        self._icon_color = color
        self.add_button.setIcon(icon("plus", color))
        self.group_button.setIcon(icon("folder-plus", color))
        self.refresh()

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def refresh(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        needle = self.search.text().strip().lower()
        matched = {c.id for c in self.store.search(needle)} if needle else None
        grouped = self.store.by_group()
        for group, conns in grouped.items():
            visible = [c for c in conns if matched is None or c.id in matched]
            if group == "":
                for conn in visible:
                    self.tree.addTopLevelItem(self._connection_item(conn))
                continue
            if not visible and needle:
                continue
            group_item = QTreeWidgetItem([group])
            group_item.setData(0, ROLE_KIND, "group")
            group_item.setData(0, ROLE_ID, group)
            group_item.setIcon(0, icon("folder", self._icon_color))
            group_item.setFlags(group_item.flags() | Qt.ItemFlag.ItemIsDropEnabled)
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            for conn in visible:
                group_item.addChild(self._connection_item(conn))
            self.tree.addTopLevelItem(group_item)
            group_item.setExpanded(self._expanded.get(group, True) or bool(needle))
        self.tree.blockSignals(False)
        self.empty_hint.setVisible(not self.store.connections)

    def _connection_item(self, conn: Connection) -> QTreeWidgetItem:
        item = QTreeWidgetItem([conn.name])
        item.setData(0, ROLE_KIND, "connection")
        item.setData(0, ROLE_ID, conn.id)
        tooltip = conn.display_target
        if conn.notes:
            tooltip += "\n" + conn.notes
        item.setToolTip(0, tooltip)
        item.setIcon(0, icon("circle" if conn.color else "server", conn.color or self._icon_color))
        item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
        return item

    # Helpers ---------------------------------------------------------------

    def _remember(self, item: QTreeWidgetItem, expanded: bool) -> None:
        if item.data(0, ROLE_KIND) == "group":
            self._expanded[item.data(0, ROLE_ID)] = expanded

    def _current_group(self) -> str:
        item = self.tree.currentItem()
        if item is None:
            return ""
        if item.data(0, ROLE_KIND) == "group":
            return item.data(0, ROLE_ID)
        parent = item.parent()
        return parent.data(0, ROLE_ID) if parent is not None else ""

    def _connection_for(self, item: QTreeWidgetItem | None) -> Connection | None:
        if item is None or item.data(0, ROLE_KIND) != "connection":
            return None
        return self.store.get(item.data(0, ROLE_ID))

    def _activated(self, item: QTreeWidgetItem, _column: int) -> None:
        conn = self._connection_for(item)
        if conn is not None:
            from PySide6.QtWidgets import QApplication

            if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.split_requested.emit(conn)
            else:
                self.connect_requested.emit(conn)
        else:
            item.setExpanded(not item.isExpanded())

    def _after_drop(self, parent_index, first: int, last: int) -> None:
        # Called after an internal drag & drop moved an item. Sync the model with the tree.
        if self.tree.signalsBlocked():
            return
        parent_item = self.tree.itemFromIndex(parent_index) if parent_index.isValid() else None
        group = parent_item.data(0, ROLE_ID) if parent_item is not None and parent_item.data(0, ROLE_KIND) == "group" else ""
        for row in range(first, last + 1):
            item = parent_item.child(row) if parent_item is not None else self.tree.topLevelItem(row)
            conn = self._connection_for(item)
            if conn is not None and conn.group != group:
                self.store.move_to_group(conn.id, group)
                self.store_changed.emit()

    # Context menu ----------------------------------------------------------

    def _context_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        conn = self._connection_for(item)
        if conn is not None:
            connect_action = menu.addAction(icon("plug", self._icon_color), "Connect in new tab")
            connect_action.triggered.connect(lambda: self.connect_requested.emit(conn))
            split_action = menu.addAction(icon("split", self._icon_color), "Connect in split pane  (Shift+double-click)")
            split_action.triggered.connect(lambda: self.split_requested.emit(conn))
            menu.addSeparator()
            edit_action = menu.addAction(icon("edit", self._icon_color), "Edit...")
            edit_action.triggered.connect(lambda: self.edit_requested.emit(conn))
            dup_action = menu.addAction(icon("copy", self._icon_color), "Duplicate")
            dup_action.triggered.connect(lambda: self._duplicate(conn))
            move_menu = menu.addMenu(icon("folder", self._icon_color), "Move to group")
            none_action = move_menu.addAction("(no group)")
            none_action.triggered.connect(lambda: self._move(conn, ""))
            for group in self.store.groups:
                action = move_menu.addAction(group)
                action.setEnabled(group != conn.group)
                action.triggered.connect(lambda _=False, g=group: self._move(conn, g))
            menu.addSeparator()
            delete_action = menu.addAction(icon("trash", "#f7768e"), "Delete")
            delete_action.triggered.connect(lambda: self.delete_requested.emit(conn))
        elif item is not None and item.data(0, ROLE_KIND) == "group":
            group = item.data(0, ROLE_ID)
            new_action = menu.addAction(icon("plus", self._icon_color), "New connection here")
            new_action.triggered.connect(lambda: self.new_requested.emit(group))
            rename_action = menu.addAction(icon("edit", self._icon_color), "Rename group...")
            rename_action.triggered.connect(lambda: self._rename_group(group))
            menu.addSeparator()
            delete_action = menu.addAction(icon("trash", "#f7768e"), "Delete group")
            delete_action.triggered.connect(lambda: self._delete_group(group))
        else:
            new_action = menu.addAction(icon("plus", self._icon_color), "New connection")
            new_action.triggered.connect(lambda: self.new_requested.emit(""))
            group_action = menu.addAction(icon("folder-plus", self._icon_color), "New group...")
            group_action.triggered.connect(self._new_group)
            import_action = menu.addAction(icon("import", self._icon_color), "Import PuTTY sessions...")
            import_action.triggered.connect(self.import_requested.emit)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _duplicate(self, conn: Connection) -> None:
        copy = conn.copy()
        copy.name = f"{conn.name} (copy)"
        copy.save_password = False
        copy.save_passphrase = False
        self.store.upsert(copy)
        self.store_changed.emit()
        self.refresh()

    def _move(self, conn: Connection, group: str) -> None:
        self.store.move_to_group(conn.id, group)
        self.store_changed.emit()
        self.refresh()

    def _new_group(self) -> None:
        name, ok = QInputDialog.getText(self, "New group", "Group name:")
        if ok and name.strip():
            self.store.add_group(name.strip())
            self.store_changed.emit()
            self.refresh()

    def _rename_group(self, group: str) -> None:
        name, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=group)
        if ok and name.strip() and name.strip() != group:
            self.store.rename_group(group, name.strip())
            self.store_changed.emit()
            self.refresh()

    def _delete_group(self, group: str) -> None:
        count = sum(1 for c in self.store.connections if c.group == group)
        text = f"Delete group “{group}”?"
        if count:
            text += f"\n\nThe {count} connection(s) inside will be kept and become ungrouped."
        if QMessageBox.question(self, "Delete group", text) == QMessageBox.StandardButton.Yes:
            self.store.remove_group(group)
            self.store_changed.emit()
            self.refresh()
