"""Small popup list that shows command suggestions under the terminal cursor."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from conduit.core.completion import Suggestion
from conduit.ui.icons import icon

KIND_COLORS = {"command": "#7aa2f7", "history": "#bb9af7", "snippet": "#9ece6a", "path": "#e0af68"}
KIND_ICONS = {"command": "terminal", "history": "refresh", "snippet": "copy", "path": "folder"}


class SuggestPopup(QFrame):
    accepted = Signal(object)  # Suggestion

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("suggestPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(0)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(lambda item: self.accepted.emit(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.list)
        self.hint = QLabel("→ inserts  ·  Tab = shell completion  ·  Esc hides")
        self.hint.setObjectName("hint")
        layout.addWidget(self.hint)
        self._items: list[Suggestion] = []

    def show_suggestions(self, items: list[Suggestion], anchor: QPoint) -> None:
        self._items = items
        self.list.clear()
        if not items:
            self.hide()
            return
        text_color = self.palette().text().color().name()
        for s in items:
            label = s.text
            if s.kind == "snippet":
                label = f"{s.text}"
            item = QListWidgetItem(icon(KIND_ICONS.get(s.kind, "terminal"), KIND_COLORS.get(s.kind, text_color), 14), label)
            item.setData(Qt.ItemDataRole.UserRole, s)
            detail = s.detail
            if detail:
                item.setText(f"{label}    {detail}")
                item.setToolTip(detail if s.kind != "snippet" else s.insert)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        rows = min(len(items), 8)
        row_height = self.list.sizeHintForRow(0) or 24
        width = min(560, max(260, self.list.sizeHintForColumn(0) + 48))
        self.resize(QSize(width, rows * row_height + self.hint.sizeHint().height() + 14))
        self._place(anchor)
        if not self.isVisible():
            self.show()

    def _place(self, anchor: QPoint) -> None:
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        x, y = anchor.x(), anchor.y() + 2
        if area is not None:
            if x + self.width() > area.right():
                x = max(area.left(), area.right() - self.width())
            if y + self.height() > area.bottom():
                y = anchor.y() - self.height() - 24  # above the cursor row
        self.move(x, y)

    def move_selection(self, step: int) -> None:
        count = self.list.count()
        if count:
            self.list.setCurrentRow((self.list.currentRow() + step) % count)

    def current(self) -> Suggestion | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def accept_current(self) -> bool:
        current = self.current()
        if current is None:
            return False
        self.accepted.emit(current)
        return True

    def retint(self, muted: str) -> None:
        self.hint.setStyleSheet(f"color: {muted};")
