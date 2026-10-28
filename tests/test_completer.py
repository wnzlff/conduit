"""Suggestion popup wiring and the Ctrl+C / Ctrl+V behaviour (offscreen, no SSH)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent

from conduit.core.history import CommandHistory
from conduit.core.snippets import Snippet, SnippetStore
from conduit.ui.completer import TerminalCompleter
from conduit.ui.terminal_widget import TerminalWidget


def _press(widget, key, text="", mods=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QKeyEvent.Type.KeyPress, key, mods, text)
    QGuiApplication.sendEvent(widget, event)
    return event


def _setup(tmp_path: Path):
    term = TerminalWidget()
    term.resize(800, 400)
    term.show()
    term.activateWindow()  # offscreen platform only hands out focus to the active window
    QGuiApplication.processEvents()
    term.setFocus()
    history = CommandHistory(tmp_path / "h.json")
    history.add("docker compose logs -f app")
    snippets = SnippetStore(tmp_path / "s.json")
    snippets.snippets = [Snippet(name="ports", body="sudo ss -tulpn", run=True)]
    completer = TerminalCompleter(term, history, snippets, lambda: None, enabled=True, min_chars=2)
    completer.set_connected(True)
    sent: list[bytes] = []
    term.data_to_send.connect(sent.append)
    return term, completer, sent


def test_popup_shows_and_right_arrow_inserts(qt_app, tmp_path: Path) -> None:
    term, completer, sent = _setup(tmp_path)
    term.feed(b"root@web:/srv$ ")
    # The user types "doc"; the server echoes it.
    _press(term, Qt.Key.Key_D, "d")
    _press(term, Qt.Key.Key_O, "o")
    _press(term, Qt.Key.Key_C, "c")
    term.feed(b"doc")
    completer.refresh()
    assert completer.popup.isVisible()
    texts = [completer.popup.list.item(i).text() for i in range(completer.popup.list.count())]
    assert texts[0].startswith("docker compose logs -f app")  # history first
    sent.clear()
    _press(term, Qt.Key.Key_Right)
    assert sent == [b"ker compose logs -f app "]
    assert not completer.popup.isVisible()


def test_tab_always_goes_to_the_shell(qt_app, tmp_path: Path) -> None:
    term, completer, sent = _setup(tmp_path)
    term.feed(b"root@web:/srv$ doc")
    completer.refresh()
    assert completer.popup.isVisible()
    sent.clear()
    _press(term, Qt.Key.Key_Tab, "\t")
    assert sent == [b"\t"]  # shell completion, not the suggestion
    assert not completer.popup.isVisible()
    # Ctrl+Space opens the popup again and, pressed once more, inserts.
    _press(term, Qt.Key.Key_Space, " ", Qt.KeyboardModifier.ControlModifier)
    assert completer.popup.isVisible()
    sent.clear()
    _press(term, Qt.Key.Key_Space, " ", Qt.KeyboardModifier.ControlModifier)
    assert sent and sent[0] != b" " and not completer.popup.isVisible()


def test_snippet_suggestion_replaces_token_and_runs(qt_app, tmp_path: Path) -> None:
    term, completer, sent = _setup(tmp_path)
    term.feed(b"$ por")
    completer._last_key_time = 1e12  # pretend the user just typed
    completer.refresh()
    assert completer.popup.isVisible()
    items = [completer.popup.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(completer.popup.list.count())]
    snippet = next(s for s in items if s.kind == "snippet")
    completer.accept(snippet)
    assert sent[0] == b"\x7f\x7f\x7f"
    assert b"sudo ss -tulpn" in sent[1]
    assert sent[-1] == b"\r"


def test_escape_hides_and_enter_records_history(qt_app, tmp_path: Path) -> None:
    term, completer, sent = _setup(tmp_path)
    term.feed(b"$ sys")
    completer.refresh()
    assert completer.popup.isVisible()
    _press(term, Qt.Key.Key_Escape)
    assert not completer.popup.isVisible()
    term.feed(b"temctl status nginx")
    _press(term, Qt.Key.Key_Return, "\r")
    assert completer.history.items[0] == "systemctl status nginx"
    assert sent[-1] == b"\r"


def test_no_popup_when_disabled_or_in_alt_screen(qt_app, tmp_path: Path) -> None:
    term, completer, _sent = _setup(tmp_path)
    term.feed(b"$ sys")
    completer.set_enabled(False)
    completer.refresh()
    assert not completer.popup.isVisible()
    completer.set_enabled(True)
    term.feed(b"\x1b[?1049h$ sys")
    completer.refresh()
    assert not completer.popup.isVisible()


def test_ctrl_c_copies_selection_else_sends_sigint(qt_app, tmp_path: Path) -> None:
    term = TerminalWidget(clipboard_ctrl_keys=True)
    term.resize(800, 400)
    term.show()
    sent: list[bytes] = []
    term.data_to_send.connect(sent.append)
    term.feed(b"hello world")
    _press(term, Qt.Key.Key_C, "\x03", Qt.KeyboardModifier.ControlModifier)
    assert sent == [b"\x03"]
    term._selection = ((0, 0), (4, 0))
    _press(term, Qt.Key.Key_C, "\x03", Qt.KeyboardModifier.ControlModifier)
    assert sent == [b"\x03"]  # copied instead of sending ^C
    assert QGuiApplication.clipboard().text() == "hello"
    assert term._selection is None
    QGuiApplication.clipboard().setText("pasted")
    _press(term, Qt.Key.Key_V, "\x16", Qt.KeyboardModifier.ControlModifier)
    assert sent[-1] == b"pasted"
    term.clipboard_ctrl_keys = False
    _press(term, Qt.Key.Key_V, "\x16", Qt.KeyboardModifier.ControlModifier)
    assert sent[-1] == b"\x16"
