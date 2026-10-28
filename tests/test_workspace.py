"""Layout logic of the split-view workspace (offscreen Qt, no SSH sessions started)."""

from __future__ import annotations

from PySide6.QtWidgets import QSplitter  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from conduit.core.models import Connection  # noqa: E402
from conduit.core.settings import Settings  # noqa: E402
from conduit.ui.session_tab import SessionTab  # noqa: E402
from conduit.ui.theme import get_theme  # noqa: E402
from conduit.ui.workspace import MAX_PANES, PaneFrame, Workspace  # noqa: E402


class _NoSecrets:
    def get_password(self, _id):
        return None

    def get_passphrase(self, _id):
        return None


def _pane(name: str) -> SessionTab:
    return SessionTab(Connection(name=name, host=f"{name}.example"), Settings(), _NoSecrets(), get_theme("dark"))


def _tree(widget) -> object:
    """Describes the splitter tree: ('H'|'V', children...) or the pane name."""
    if isinstance(widget, QSplitter):
        tag = "H" if widget.orientation() == Qt.Orientation.Horizontal else "V"
        return (tag, *[_tree(widget.widget(i)) for i in range(widget.count())])
    assert isinstance(widget, PaneFrame)
    return widget.session.connection.name


def test_snap_layouts(qt_app) -> None:
    ws = Workspace()
    ws.resize(800, 600)
    ws.show()
    names = ["a", "b", "c", "d"]
    panes = [_pane(n) for n in names]
    expected = {
        1: "a",
        2: ("H", "a", "b"),
        3: ("H", "a", ("V", "b", "c")),
        4: ("H", ("V", "a", "d"), ("V", "b", "c")),
    }
    for i, pane in enumerate(panes, start=1):
        assert ws.add_pane(pane)
        assert _tree(ws._root) == expected[i]
        assert ws.title() == (names[0] if i == 1 else " | ".join(names[:i]))
    assert ws.is_full() and ws.free_slots() == 0
    assert not ws.add_pane(_pane("e"))
    # headers only when more than one pane
    assert all(f.header.isVisibleTo(ws) for f in ws._frames)

    # Removing the middle pane re-snaps the remaining three.
    taken = ws.take_pane(panes[1])
    assert taken is panes[1]
    assert _tree(ws._root) == ("H", "a", ("V", "c", "d"))
    assert ws.free_slots() == 1

    ws.take_pane(panes[2])
    ws.take_pane(panes[3])
    assert _tree(ws._root) == "a"
    assert not ws._frames[0].header.isVisibleTo(ws)

    empties: list[bool] = []
    ws.became_empty.connect(lambda: empties.append(True))
    ws.take_pane(panes[0])
    assert empties == [True]
    assert ws.panes() == []
    assert ws.title() == ""
    for pane in panes:
        pane.deleteLater()


def test_active_pane_and_focus_cycling(qt_app) -> None:
    ws = Workspace()
    ws.resize(800, 600)
    ws.show()
    a, b, c = _pane("a"), _pane("b"), _pane("c")
    for pane in (a, b, c):
        ws.add_pane(pane)
    assert ws.active_pane() is c  # last added becomes active
    ws.focus_pane(a)
    assert ws.active_pane() is a
    ws.focus_next()
    assert ws.active_pane() is b
    ws.focus_next(-1)
    assert ws.active_pane() is a
    qt_app.processEvents()
    # a is on the left; b (top right) and c (bottom right) are to its right
    ws.focus_direction(1, 0)
    assert ws.active_pane() in (b, c)
    ws.focus_pane(b)
    ws.focus_direction(0, 1)
    assert ws.active_pane() is c
    ws.focus_direction(-1, 0)
    assert ws.active_pane() is a
    assert ws.state() == "disconnected"
    for pane in (a, b, c):
        ws.take_pane(pane)
        pane.deleteLater()


def test_max_panes_constant() -> None:
    assert MAX_PANES == 4
