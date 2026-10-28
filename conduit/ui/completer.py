"""Glue between a terminal, the suggestion engine and the popup.

Watches what the user types, reconstructs the command line from the screen after the server has
echoed it, asks :mod:`conduit.core.completion` for matches (commands, history, snippets and
remote paths for the current directory) and shows them in a popup. Right arrow (or Ctrl+Space)
inserts the selected suggestion, Esc hides the popup, Ctrl+Space opens it on demand, Enter records
the command in the history and runs it. Tab is never intercepted: it stays the shell's own
completion key.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeyEvent

from conduit.core import completion
from conduit.core.history import CommandHistory
from conduit.core.remote_fs import RemotePathLister
from conduit.core.snippets import SnippetStore
from conduit.ui.suggest_popup import SuggestPopup
from conduit.ui.terminal_widget import TerminalWidget

TYPING_WINDOW = 2.0  # seconds after a key press during which output refreshes the popup


class TerminalCompleter(QObject):
    def __init__(
        self,
        terminal: TerminalWidget,
        history: CommandHistory,
        snippets: SnippetStore,
        open_sftp: Callable,
        *,
        enabled: bool = True,
        min_chars: int = 2,
    ) -> None:
        super().__init__(terminal)
        self.terminal = terminal
        self.history = history
        self.snippets = snippets
        self._open_sftp = open_sftp
        self.enabled = enabled
        self.min_chars = max(1, min_chars)
        self.popup = SuggestPopup(terminal)
        self.popup.accepted.connect(self.accept)
        self._lister: RemotePathLister | None = None
        self._home: str | None = None
        self._listings: dict[str, list[tuple[str, bool]]] = {}
        self._pending: set[str] = set()
        self._last_key_time = 0.0
        self._forced = False
        self._connected = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self.refresh)
        terminal.installEventFilter(self)
        terminal.key_typed.connect(self._key_typed)
        terminal.output_received.connect(self._output)

    # Lifecycle -------------------------------------------------------------

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.popup.hide()
            self._stop_lister()

    def set_enabled(self, enabled: bool, min_chars: int | None = None) -> None:
        self.enabled = enabled
        if min_chars is not None:
            self.min_chars = max(1, min_chars)
        if not enabled:
            self.popup.hide()

    def shutdown(self) -> None:
        self.popup.hide()
        self._stop_lister()
        try:
            self.terminal.removeEventFilter(self)
        except RuntimeError:
            pass

    def _stop_lister(self) -> None:
        if self._lister is not None:
            self._lister.shutdown()
            self._lister.wait(1500)
            self._lister = None
        self._listings.clear()
        self._pending.clear()
        self._home = None

    def _ensure_lister(self) -> None:
        if self._lister is not None or not self._connected:
            return
        self._lister = RemotePathLister(self._open_sftp, self)
        self._lister.ready.connect(self._lister_ready)
        self._lister.listed.connect(self._listed)
        self._lister.failed.connect(lambda _msg: None)
        self._lister.start()

    def _lister_ready(self, home: str) -> None:
        self._home = home
        self._schedule()

    def _listed(self, path: str, entries: list) -> None:
        self._listings[path] = entries
        self._pending.discard(path)
        self._schedule()

    def invalidate_listings(self) -> None:
        """Called after Enter: the command may have changed the file system."""
        self._listings.clear()

    # Events ----------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        terminal = getattr(self, "terminal", None)  # may be gone while the objects are torn down
        if terminal is None or obj is not terminal:
            return False
        if event.type() == QEvent.Type.KeyPress:
            key_event: QKeyEvent = event  # type: ignore[assignment]
            if self._handle_key(key_event):
                return True
        elif event.type() in (QEvent.Type.FocusOut, QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.Resize):
            self.popup.hide()
        return False

    def _handle_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_Space and self.enabled:
            if self.popup.isVisible():
                return self.popup.accept_current()
            self._forced = True
            self.refresh()
            return True
        if not self.popup.isVisible():
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods:
                self._record_history()
            return False
        if key == Qt.Key.Key_Escape:
            self.popup.hide()
            return True
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            # Tab always belongs to the shell (file name completion on the server).
            self.popup.hide()
            return False
        if key == Qt.Key.Key_Right and not mods:
            return self.popup.accept_current()
        if key == Qt.Key.Key_Down:
            self.popup.move_selection(1)
            return True
        if key == Qt.Key.Key_Up:
            self.popup.move_selection(-1)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.popup.hide()
            self._record_history()
            return False  # the shell gets the Enter
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.popup.hide()
        return False

    def _key_typed(self, data: bytes) -> None:
        if not self.enabled:
            return
        if data in (b"\r", b"\x03", b"\x04", b"\x1b", b"\x0c"):
            self._forced = False
            self.popup.hide()
            if data == b"\r":
                self.invalidate_listings()
            return
        self._last_key_time = time.monotonic()
        self._schedule()

    def _output(self) -> None:
        if self.enabled and (self.popup.isVisible() or time.monotonic() - self._last_key_time < TYPING_WINDOW):
            self._schedule()

    def _schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    # Suggestions -----------------------------------------------------------

    def current_context(self) -> completion.InputContext:
        return completion.parse_input(self.terminal.cursor_row_text(), self.terminal.screen.title)

    def _record_history(self) -> None:
        ctx = self.current_context()
        if ctx.has_prompt and ctx.input.strip():
            self.history.add(ctx.input)
            self.history.save()

    def refresh(self) -> None:
        term = self.terminal
        if not self.enabled or not term.hasFocus() or term.screen.in_alt_screen or not self._connected:
            self.popup.hide()
            return
        ctx = self.current_context()
        if not ctx.has_prompt and not self._forced:
            self.popup.hide()
            return
        token = ctx.token
        min_chars = 1 if self._forced else self.min_chars
        if len(token) < min_chars and not (ctx.input.endswith(" ") and self._forced):
            self.popup.hide()
            return

        remote_entries = None
        wants_paths = completion.looks_like_path(token) or ctx.command in completion.FILE_COMMANDS or not ctx.at_command_position
        if wants_paths and (ctx.cwd or token.startswith(("/", "~"))):
            self._ensure_lister()
            prefix, _partial = completion.split_path_token(token)
            directory = completion.resolve_directory(prefix, ctx.cwd, self._home)
            if directory is not None:
                if directory in self._listings:
                    remote_entries = self._listings[directory]
                elif self._lister is not None and directory not in self._pending:
                    self._pending.add(directory)
                    self._lister.request(directory)

        items = completion.suggest(
            ctx,
            history=self.history.items,
            snippets=self.snippets.as_tuples(),
            remote_entries=remote_entries,
            min_chars=min_chars,
        )
        if not items:
            self.popup.hide()
            return
        self.popup.show_suggestions(items, term.cursor_global_pos())

    def accept(self, suggestion: completion.Suggestion) -> None:
        self.popup.hide()
        self._forced = False
        payload = b"\x7f" * suggestion.replace
        if suggestion.kind == "snippet":
            self.terminal.data_to_send.emit(payload)
            self.insert_snippet(suggestion.insert, suggestion.run)
            return
        payload += suggestion.insert.encode("utf-8")
        if suggestion.kind == "path" and not suggestion.text.endswith("/"):
            payload += b" "
        elif suggestion.kind in ("command", "history") and not suggestion.insert.endswith(" "):
            payload += b" "
        self.terminal.data_to_send.emit(payload)
        self._last_key_time = time.monotonic()
        self.terminal.setFocus()

    def insert_snippet(self, body: str, run: bool) -> None:
        """Inserts snippet text at the prompt (used by the snippet menu as well)."""
        self.terminal.paste_text(body)
        if run:
            self.terminal.data_to_send.emit(b"\r")
            self.history.add(body.strip().splitlines()[-1] if body.strip() else "")
            self.history.save()
            self.invalidate_listings()
        self.terminal.setFocus()
