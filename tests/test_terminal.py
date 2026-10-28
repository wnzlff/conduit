"""Terminal widget behaviour that does not need a display: resize, scrollback, alt screen, keys."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from conduit.ui.terminal_widget import TerminalWidget


def _line(term: TerminalWidget, y: int) -> str:
    return "".join(term.screen.buffer[y].get(x, term.screen.default_char).data for x in range(term.cols)).rstrip()


def _feed_lines(term: TerminalWidget, n: int) -> None:
    for i in range(n):
        term.feed(f"line {i}\r\n".encode())


def test_shrinking_keeps_cursor_lines_and_scrolls_into_history(qt_app) -> None:
    term = TerminalWidget()
    term.screen.resize(10, 40)
    term._rows, term._cols = 10, 40
    _feed_lines(term, 8)  # cursor now on row 8
    assert term.screen.cursor.y == 8
    assert _line(term, 0) == "line 0"
    term.screen.resize(5, 40)
    # 4 top rows went to history, the cursor row is still visible
    assert len(term.screen.history.top) == 4
    assert term.screen.cursor.y == 4
    assert _line(term, 0) == "line 4"
    assert _line(term, 3) == "line 7"
    # growing again pulls the lines back out of history
    term.screen.resize(10, 40)
    assert len(term.screen.history.top) == 0
    assert term.screen.cursor.y == 8
    assert _line(term, 0) == "line 0"
    assert _line(term, 7) == "line 7"


def test_shrinking_with_cursor_near_top_drops_empty_bottom(qt_app) -> None:
    term = TerminalWidget()
    term.screen.resize(10, 40)
    _feed_lines(term, 2)
    term.screen.resize(5, 40)
    assert len(term.screen.history.top) == 0
    assert _line(term, 0) == "line 0"
    assert term.screen.cursor.y == 2


def test_alt_screen_restores_main_buffer(qt_app) -> None:
    term = TerminalWidget()
    _feed_lines(term, 3)
    term.feed(b"\x1b[?1049h\x1b[2J\x1b[HFULLSCREEN")
    assert term.screen.in_alt_screen
    assert _line(term, 0) == "FULLSCREEN"
    term.feed(b"\x1b[?1049l")
    assert not term.screen.in_alt_screen
    assert _line(term, 0) == "line 0"
    assert term.screen.cursor.y == 3


def test_key_encoding(qt_app) -> None:
    term = TerminalWidget()
    sent: list[bytes] = []
    term.data_to_send.connect(sent.append)

    def press(key, text="", mods=Qt.KeyboardModifier.NoModifier):
        term.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, mods, text))

    press(Qt.Key.Key_A, "a")
    press(Qt.Key.Key_Return, "\r")
    press(Qt.Key.Key_Up)
    press(Qt.Key.Key_Up, mods=Qt.KeyboardModifier.ControlModifier)
    press(Qt.Key.Key_C, "\x03", Qt.KeyboardModifier.ControlModifier)
    press(Qt.Key.Key_F5)
    press(Qt.Key.Key_Tab, "\t")
    press(Qt.Key.Key_Tab, "\t", Qt.KeyboardModifier.ControlModifier)  # app shortcut, must not be sent
    press(Qt.Key.Key_X, "x", Qt.KeyboardModifier.AltModifier)
    assert sent == [b"a", b"\r", b"\x1b[A", b"\x1b[1;5A", b"\x03", b"\x1b[15~", b"\t", b"\x1bx"]


def test_paste_uses_bracketed_mode_when_enabled(qt_app) -> None:
    term = TerminalWidget()
    sent: list[bytes] = []
    term.data_to_send.connect(sent.append)
    term.paste_text("ls\npwd\n")
    assert sent == [b"ls\rpwd\r"]
    term.feed(b"\x1b[?2004h")
    term.paste_text("x")
    assert sent[-1] == b"\x1b[200~x\x1b[201~"


def test_selection_text_and_scrollback(qt_app) -> None:
    term = TerminalWidget(scrollback=100)
    term.screen.resize(5, 20)
    term._rows, term._cols = 5, 20
    _feed_lines(term, 12)
    assert len(term.screen.history.top) == 8
    term.select_all()
    lines = term.selected_text().splitlines()
    assert lines[0] == "line 0" and lines[11] == "line 11"
    term.scroll_by(3)
    assert term._scroll_offset == 3
    term.clear_scrollback()
    assert len(term.screen.history.top) == 0 and term._scroll_offset == 0
    QApplication.processEvents()
