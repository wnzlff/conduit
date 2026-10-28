"""A workspace is the content of one tab: up to four session panes snapped into a grid.

Layouts (like Windows Snap):

    1 pane            2 panes           3 panes             4 panes
    +---------+       +----+----+       +----+----+         +----+----+
    |         |       |    |    |       |    | 2  |         | 1  | 2  |
    |    1    |       | 1  | 2  |       | 1  +----+         +----+----+
    |         |       |    |    |       |    | 3  |         | 4  | 3  |
    +---------+       +----+----+       +----+----+         +----+----+

Every split is a QSplitter, so pane sizes stay adjustable with the mouse.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from conduit.ui.icons import icon
from conduit.ui.session_tab import SessionTab

MAX_PANES = 4
STATE_COLORS = {"connecting": "#e0af68", "connected": "#9ece6a", "disconnected": "#8a93a6"}


class PaneFrame(QFrame):
    """A session with a small header (name, status, pop-out and close buttons)."""

    close_requested = Signal(object)  # PaneFrame
    popout_requested = Signal(object)

    def __init__(self, session: SessionTab, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("pane")
        self.setProperty("active", False)
        self._icon_color = "#8a93a6"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("paneHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_layout.setSpacing(6)
        self.dot = QLabel()
        self.name_label = QLabel(session.connection.name)
        self.name_label.setObjectName("paneTitle")
        self.target_label = QLabel(session.connection.display_target)
        self.target_label.setObjectName("muted")
        header_layout.addWidget(self.dot)
        header_layout.addWidget(self.name_label)
        header_layout.addWidget(self.target_label, 1)
        self.popout_button = QToolButton()
        self.popout_button.setObjectName("flat")
        self.popout_button.setToolTip("Move this pane to its own tab")
        self.popout_button.clicked.connect(lambda: self.popout_requested.emit(self))
        self.close_button = QToolButton()
        self.close_button.setObjectName("flat")
        self.close_button.setToolTip("Close this pane")
        self.close_button.clicked.connect(lambda: self.close_requested.emit(self))
        header_layout.addWidget(self.popout_button)
        header_layout.addWidget(self.close_button)
        layout.addWidget(self.header)
        layout.addWidget(session, 1)
        session.setVisible(True)  # may have been hidden by setParent(None) when moved between tabs

        session.state_changed.connect(lambda _s: self.refresh())
        self.refresh()

    def set_icon_color(self, color: str) -> None:
        self._icon_color = color
        self.refresh()

    def refresh(self) -> None:
        conn = self.session.connection
        self.name_label.setText(conn.name)
        self.target_label.setText(conn.display_target)
        state = self.session.state
        color = conn.color if (state == "connected" and conn.color) else STATE_COLORS[state]
        self.dot.setPixmap(icon("circle", color, 10).pixmap(10, 10))
        self.popout_button.setIcon(icon("popout", self._icon_color, 14))
        self.close_button.setIcon(icon("close", self._icon_color, 14))

    def set_active(self, active: bool) -> None:
        if self.property("active") == active:
            return
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.header.style().unpolish(self.header)
        self.header.style().polish(self.header)

    def set_header_visible(self, visible: bool) -> None:
        self.header.setVisible(visible)
        margin = 1 if visible else 0
        self.layout().setContentsMargins(margin, margin, margin, margin)

    def mousePressEvent(self, event) -> None:
        self.session.terminal.setFocus()
        super().mousePressEvent(event)


class Workspace(QWidget):
    changed = Signal()  # panes added/removed or state changed: refresh tab title/icon
    became_empty = Signal()
    close_pane_requested = Signal(object)  # SessionTab
    popout_requested = Signal(object)  # SessionTab

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frames: list[PaneFrame] = []
        self._active: PaneFrame | None = None
        self._root: QWidget | None = None
        self._icon_color = "#8a93a6"
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._focus_changed)

    # Queries ---------------------------------------------------------------

    def panes(self) -> list[SessionTab]:
        return [f.session for f in self._frames]

    def active_pane(self) -> SessionTab | None:
        if self._active is not None:
            return self._active.session
        return self._frames[0].session if self._frames else None

    def is_full(self) -> bool:
        return len(self._frames) >= MAX_PANES

    def free_slots(self) -> int:
        return MAX_PANES - len(self._frames)

    def contains(self, session: SessionTab) -> bool:
        return any(f.session is session for f in self._frames)

    def title(self) -> str:
        names = [f.session.connection.name for f in self._frames]
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        text = " | ".join(names)
        return text if len(text) <= 48 else f"{names[0]} +{len(names) - 1}"

    def tooltip(self) -> str:
        return "\n".join(f.session.tooltip() for f in self._frames)

    def state(self) -> str:
        """Aggregate state for the tab icon: the active pane's state."""
        pane = self.active_pane()
        return pane.state if pane is not None else "disconnected"

    def state_color(self) -> str:
        pane = self.active_pane()
        if pane is None:
            return STATE_COLORS["disconnected"]
        if pane.state == "connected" and pane.connection.color:
            return pane.connection.color
        return STATE_COLORS[pane.state]

    @staticmethod
    def of(widget: QWidget | None) -> "Workspace | None":
        while widget is not None:
            if isinstance(widget, Workspace):
                return widget
            widget = widget.parentWidget()
        return None

    # Mutations -------------------------------------------------------------

    def add_pane(self, session: SessionTab) -> bool:
        if self.is_full() or self.contains(session):
            return False
        frame = PaneFrame(session)
        frame.set_icon_color(self._icon_color)
        frame.close_requested.connect(lambda f: self.close_pane_requested.emit(f.session))
        frame.popout_requested.connect(lambda f: self.popout_requested.emit(f.session))
        session.state_changed.connect(self._pane_state_changed)
        self._frames.append(frame)
        self._rebuild()
        self._set_active(frame)
        self.changed.emit()
        return True

    def take_pane(self, session: SessionTab) -> SessionTab | None:
        """Detaches a pane without closing its session (for moving it elsewhere)."""
        frame = next((f for f in self._frames if f.session is session), None)
        if frame is None:
            return None
        self._frames.remove(frame)
        try:
            session.state_changed.disconnect(self._pane_state_changed)
        except (RuntimeError, TypeError):
            pass
        session.setParent(None)
        frame.setParent(None)
        frame.deleteLater()
        if self._active is frame:
            self._active = None
        self._rebuild()
        if self._frames:
            self._set_active(self._frames[0])
            self._frames[0].session.terminal.setFocus()
            self.changed.emit()
        else:
            self.became_empty.emit()
        return session

    def set_icon_color(self, color: str) -> None:
        self._icon_color = color
        for frame in self._frames:
            frame.set_icon_color(color)

    def focus_pane(self, session: SessionTab) -> None:
        for frame in self._frames:
            if frame.session is session:
                self._set_active(frame)
                session.terminal.setFocus()
                return

    def focus_direction(self, dx: int, dy: int) -> None:
        """Moves focus to the neighbouring pane in the given direction (like window managers do)."""
        if self._active is None or len(self._frames) < 2:
            return
        # Frames live in different splitters, so compare centres in workspace coordinates.
        origin = self._active.mapTo(self, self._active.rect().center())
        best, best_score = None, None
        for frame in self._frames:
            if frame is self._active:
                continue
            delta = frame.mapTo(self, frame.rect().center()) - origin
            along = delta.x() * dx + delta.y() * dy
            if along <= 0:
                continue
            across = abs(delta.y()) if dx else abs(delta.x())
            score = along + across * 2
            if best_score is None or score < best_score:
                best, best_score = frame, score
        if best is not None:
            self._set_active(best)
            best.session.terminal.setFocus()

    def focus_next(self, step: int = 1) -> None:
        if not self._frames:
            return
        index = self._frames.index(self._active) if self._active in self._frames else 0
        frame = self._frames[(index + step) % len(self._frames)]
        self._set_active(frame)
        frame.session.terminal.setFocus()

    # Internals -------------------------------------------------------------

    def _pane_state_changed(self, _state: str) -> None:
        self.changed.emit()

    def _focus_changed(self, _old: QWidget | None, now: QWidget | None) -> None:
        if now is None:
            return
        for frame in self._frames:
            if frame is now or frame.isAncestorOf(now):
                self._set_active(frame)
                return

    def _set_active(self, frame: PaneFrame | None) -> None:
        if frame is self._active:
            return
        self._active = frame
        multi = len(self._frames) > 1
        for f in self._frames:
            f.set_active(multi and f is frame)
        self.changed.emit()

    def _rebuild(self) -> None:
        # Detach frames from the old splitter tree, then drop the tree.
        for frame in self._frames:
            frame.setParent(self)
        if self._root is not None:
            self._layout.removeWidget(self._root)
            if isinstance(self._root, QSplitter):  # a lone frame is the root for a single pane
                self._root.setParent(None)
                self._root.deleteLater()
            self._root = None
        n = len(self._frames)
        f = self._frames
        multi = n > 1
        for frame in f:
            frame.set_header_visible(multi)
            frame.setVisible(True)
        if n == 0:
            return
        if n == 1:
            root: QWidget = f[0]
        elif n == 2:
            root = self._split(Qt.Orientation.Horizontal, f[0], f[1])
        elif n == 3:
            right = self._split(Qt.Orientation.Vertical, f[1], f[2])
            root = self._split(Qt.Orientation.Horizontal, f[0], right)
        else:
            left = self._split(Qt.Orientation.Vertical, f[0], f[3])
            right = self._split(Qt.Orientation.Vertical, f[1], f[2])
            root = self._split(Qt.Orientation.Horizontal, left, right)
        self._root = root
        self._layout.addWidget(root)
        root.show()  # reparenting hid the frames; show the tree explicitly instead of waiting for the event loop
        if self._active not in self._frames:
            self._active = None
        for frame in self._frames:
            frame.set_active(multi and frame is self._active)

    @staticmethod
    def _split(orientation: Qt.Orientation, first: QWidget, second: QWidget) -> QSplitter:
        splitter = QSplitter(orientation)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        splitter.addWidget(first)
        splitter.addWidget(second)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1000, 1000])
        return splitter
