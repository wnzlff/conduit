"""A terminal emulator widget: pyte screen model rendered with QPainter.

Supports 16/256/true colours, bold/italic/underline/reverse, scrollback with mouse wheel and a
scroll bar, text selection with copy, paste (Ctrl+Shift+V, Shift+Insert, middle/right click),
alternate screen (vim, htop, less), bracketed paste and window title reports.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyte
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QMenu, QScrollBar, QWidget

ALT_SCREEN_MODES = {47, 1047, 1049}
BRACKETED_PASTE = 2004 << 5

ANSI_NAMES = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]


@dataclass(frozen=True)
class TerminalPalette:
    foreground: str
    background: str
    cursor: str
    selection: str
    ansi: tuple[str, ...]  # 16 colours

    @property
    def is_dark(self) -> bool:
        return QColor(self.background).lightness() < 128


DARK_PALETTE = TerminalPalette(
    foreground="#d6dbe5",
    background="#11141a",
    cursor="#7aa2f7",
    selection="#3b4a6b",
    ansi=(
        "#1c2028", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5",
        "#555d6d", "#ff8ea3", "#b4e08a", "#ffd08a", "#9bb8ff", "#d0b2ff", "#a4e4ff", "#ffffff",
    ),
)

LIGHT_PALETTE = TerminalPalette(
    foreground="#2b3240",
    background="#fbfbfd",
    cursor="#2f6feb",
    selection="#cfe0ff",
    ansi=(
        "#2b3240", "#d7263d", "#2e8b57", "#b7791f", "#2f6feb", "#8a4fff", "#0e8c9c", "#8a93a6",
        "#5c6578", "#ff5470", "#3fae6e", "#d4941b", "#4f8bff", "#a47cff", "#17a9bd", "#2b3240",
    ),
)


class _Screen(pyte.HistoryScreen):
    """pyte screen with alternate-screen support and hooks into the widget."""

    def __init__(self, widget: "TerminalWidget", columns: int, lines: int, history: int) -> None:
        super().__init__(columns, lines, history=history, ratio=0.5)
        self._widget = widget
        self._saved_main: dict | None = None
        self._saved_cursor: tuple[int, int] | None = None
        self.in_alt_screen = False

    # Hooks -----------------------------------------------------------------

    def write_process_input(self, data: str) -> None:
        self._widget.data_to_send.emit(data.encode("utf-8", "replace"))

    def set_title(self, param: str) -> None:
        super().set_title(param)
        self._widget.title_changed.emit(param)

    def bell(self) -> None:
        self._widget.bell.emit()

    # Alternate screen ------------------------------------------------------

    def set_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and any(m in ALT_SCREEN_MODES for m in modes):
            self._enter_alt_screen(save_cursor=1049 in modes)
            modes = tuple(m for m in modes if m not in ALT_SCREEN_MODES)
            if not modes:
                return
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and any(m in ALT_SCREEN_MODES for m in modes):
            self._leave_alt_screen(restore_cursor=1049 in modes)
            modes = tuple(m for m in modes if m not in ALT_SCREEN_MODES)
            if not modes:
                return
        super().reset_mode(*modes, **kwargs)

    def _enter_alt_screen(self, *, save_cursor: bool) -> None:
        if self.in_alt_screen:
            return
        self.in_alt_screen = True
        self._saved_main = {y: dict(line) for y, line in self.buffer.items()}
        self._saved_cursor = (self.cursor.x, self.cursor.y) if save_cursor else None
        self.buffer.clear()
        self.dirty.update(range(self.lines))
        self._widget.scroll_to_bottom()

    def _leave_alt_screen(self, *, restore_cursor: bool) -> None:
        if not self.in_alt_screen:
            return
        self.in_alt_screen = False
        self.buffer.clear()
        if self._saved_main:
            for y, line in self._saved_main.items():
                self.buffer[y].update(line)
        if restore_cursor and self._saved_cursor is not None:
            self.cursor.x, self.cursor.y = self._saved_cursor
        self._saved_main = None
        self.dirty.update(range(self.lines))

    def index(self) -> None:
        # Do not record alternate-screen lines (e.g. vim redraws) into scrollback history.
        if self.in_alt_screen:
            pyte.Screen.index(self)
        else:
            super().index()

    @property
    def bracketed_paste(self) -> bool:
        return BRACKETED_PASTE in self.mode

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        """Resize like a real terminal: when the screen gets shorter, lines scroll into history so
        the cursor stays visible (pyte would clip the top rows); when it gets taller, lines are pulled
        back out of history. Columns are handled like pyte does."""
        lines = lines or self.lines
        columns = columns or self.columns
        if lines == self.lines and columns == self.columns:
            return
        self.dirty.update(range(max(lines, self.lines)))
        if lines < self.lines:
            drop = self.lines - lines
            scroll = min(drop, max(0, self.cursor.y - (lines - 1)))
            if scroll and not self.in_alt_screen:
                for y in range(scroll):
                    self.history.top.append(self.buffer[y])
            if scroll:
                for y in range(scroll, self.lines):
                    self.buffer[y - scroll] = self.buffer[y]
                for y in range(self.lines - scroll, self.lines):
                    self.buffer.pop(y, None)
                self.cursor.y -= scroll
            for y in range(lines, self.lines):
                self.buffer.pop(y, None)
            self.cursor.y = min(self.cursor.y, lines - 1)
        elif lines > self.lines and not self.in_alt_screen and self.history.top:
            pull = min(lines - self.lines, len(self.history.top))
            for y in range(self.lines - 1, -1, -1):
                self.buffer[y + pull] = self.buffer[y]
            for y in range(pull - 1, -1, -1):
                self.buffer[y] = self.history.top.pop()
            self.cursor.y += pull
        if columns < self.columns:
            for line in self.buffer.values():
                for x in range(columns, self.columns):
                    line.pop(x, None)
        self.lines, self.columns = lines, columns
        self.set_margins()
        if self.cursor.x >= columns:
            self.cursor.x = columns - 1


class TerminalWidget(QWidget):
    data_to_send = Signal(bytes)
    size_changed = Signal(int, int)  # cols, rows
    title_changed = Signal(str)
    bell = Signal()
    output_received = Signal()  # screen content changed (after feed)
    key_typed = Signal(bytes)  # bytes produced by a key press (not pastes)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        font_family: str = "Cascadia Mono",
        font_size: int = 11,
        scrollback: int = 5000,
        palette: TerminalPalette = DARK_PALETTE,
        copy_on_select: bool = True,
        paste_on_right_click: bool = True,
        cursor_blink: bool = True,
        clipboard_ctrl_keys: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setMouseTracking(False)

        self.copy_on_select = copy_on_select
        self.paste_on_right_click = paste_on_right_click
        self.clipboard_ctrl_keys = clipboard_ctrl_keys
        self._palette = palette
        self._scrollback = max(0, scrollback)
        self._cols, self._rows = 80, 24
        self.screen = _Screen(self, self._cols, self._rows, self._scrollback)
        self.stream = pyte.ByteStream(self.screen)

        self._scroll_offset = 0  # number of history lines scrolled up from the bottom
        self._selection: tuple[tuple[int, int], tuple[int, int]] | None = None  # (col,row) absolute rows
        self._selecting = False
        self._anchor: tuple[int, int] | None = None
        self._cursor_visible_phase = True
        self._blink_enabled = cursor_blink
        self._connected = True

        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.setRange(0, 0)
        self._scrollbar.valueChanged.connect(self._on_scrollbar)
        self._scrollbar.setCursor(Qt.CursorShape.ArrowCursor)

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(12)
        self._repaint_timer.timeout.connect(self.update)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(530)
        self._blink_timer.timeout.connect(self._toggle_blink)

        self.set_font(font_family, font_size)
        self._build_color_cache()

    # Configuration ---------------------------------------------------------

    def set_font(self, family: str, size: int) -> None:
        font = QFont(family, size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setKerning(False)
        self._font = font
        self._bold_font = QFont(font)
        self._bold_font.setBold(True)
        self._italic_font = QFont(font)
        self._italic_font.setItalic(True)
        self._bold_italic_font = QFont(self._bold_font)
        self._bold_italic_font.setItalic(True)
        metrics = QFontMetricsF(font)
        self._cell_w = max(1.0, metrics.horizontalAdvance("M"))
        self._cell_h = max(1.0, metrics.height() + 1)
        self._ascent = metrics.ascent()
        self.setFont(font)
        self._recompute_grid()
        self.update()

    def set_palette(self, palette: TerminalPalette) -> None:
        self._palette = palette
        self._build_color_cache()
        self.update()

    def set_scrollback(self, lines: int) -> None:
        self._scrollback = max(0, lines)
        self.screen.history = pyte.screens.History(
            pyte.screens.deque(self.screen.history.top, maxlen=self._scrollback),
            pyte.screens.deque(self.screen.history.bottom, maxlen=self._scrollback),
            0.5,
            self._scrollback,
            self._scrollback,
        )
        self._update_scrollbar()

    def set_cursor_blink(self, enabled: bool) -> None:
        self._blink_enabled = enabled
        self._cursor_visible_phase = True
        if enabled and self.hasFocus():
            self._blink_timer.start()
        else:
            self._blink_timer.stop()
        self.update()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.update()

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    # Data ------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        self.stream.feed(data)
        self._update_scrollbar()
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()
        self.output_received.emit()

    def cursor_row_text(self, *, up_to_cursor: bool = True) -> str:
        """Text of the row the cursor is on (used for command suggestions)."""
        y = self.screen.cursor.y
        line = self.screen.buffer[y]
        end = self.screen.cursor.x if up_to_cursor else self._cols
        default_char = self.screen.default_char
        return "".join(line.get(x, default_char).data or " " for x in range(end))

    def cursor_global_pos(self) -> QPoint:
        """Bottom-left screen position of the cursor cell (for popups)."""
        cx = min(self.screen.cursor.x, self._cols - 1)
        cy = self.screen.cursor.y
        return self.mapToGlobal(QPoint(int(4 + cx * self._cell_w), int(2 + (cy + 1) * self._cell_h)))

    def write_message(self, text: str, *, color: int = 8) -> None:
        """Writes a locally generated status line (e.g. connection errors) to the screen."""
        payload = f"\r\n\x1b[{90 + (color - 8) if color >= 8 else 30 + color}m{text}\x1b[0m\r\n"
        self.feed(payload.encode("utf-8"))

    def clear_scrollback(self) -> None:
        self.screen.history.top.clear()
        self.screen.history.bottom.clear()
        self._scroll_offset = 0
        self._update_scrollbar()
        self.update()

    def reset(self) -> None:
        self.screen.reset()
        self.clear_scrollback()

    # Grid / geometry -------------------------------------------------------

    def _scrollbar_width(self) -> int:
        return self._scrollbar.sizeHint().width()

    def _recompute_grid(self) -> None:
        avail_w = max(1, self.width() - self._scrollbar_width() - 8)
        avail_h = max(1, self.height() - 4)
        cols = max(2, int(avail_w // self._cell_w))
        rows = max(1, int(avail_h // self._cell_h))
        if (cols, rows) != (self._cols, self._rows):
            self._cols, self._rows = cols, rows
            self.screen.resize(rows, cols)
            self.screen.dirty.update(range(rows))
            self._selection = None
            self.size_changed.emit(cols, rows)
        self._update_scrollbar()

    def resizeEvent(self, event: QResizeEvent) -> None:
        sb_w = self._scrollbar_width()
        self._scrollbar.setGeometry(self.width() - sb_w, 0, sb_w, self.height())
        self._recompute_grid()
        super().resizeEvent(event)

    def sizeHint(self):  # noqa: D102
        from PySide6.QtCore import QSize

        return QSize(int(80 * self._cell_w) + self._scrollbar_width() + 8, int(24 * self._cell_h) + 4)

    # Scrollback ------------------------------------------------------------

    def _history_len(self) -> int:
        return 0 if self.screen.in_alt_screen else len(self.screen.history.top)

    def _update_scrollbar(self) -> None:
        total = self._history_len()
        self._scroll_offset = min(self._scroll_offset, total)
        self._scrollbar.blockSignals(True)
        self._scrollbar.setRange(0, total)
        self._scrollbar.setPageStep(self._rows)
        self._scrollbar.setValue(total - self._scroll_offset)
        self._scrollbar.blockSignals(False)

    def _on_scrollbar(self, value: int) -> None:
        self._scroll_offset = self._history_len() - value
        self.update()

    def scroll_by(self, lines: int) -> None:
        self._scroll_offset = max(0, min(self._history_len(), self._scroll_offset + lines))
        self._update_scrollbar()
        self.update()

    def scroll_to_bottom(self) -> None:
        if self._scroll_offset:
            self._scroll_offset = 0
            self._update_scrollbar()
            self.update()

    def _visible_lines(self) -> list[tuple[int, dict]]:
        """Returns (absolute_row, line_dict) for every visible row."""
        history = self.screen.history.top if not self.screen.in_alt_screen else ()
        hist_len = len(history)
        start = hist_len - self._scroll_offset  # absolute index of first visible row
        result = []
        for i in range(self._rows):
            absolute = start + i
            if absolute < hist_len:
                result.append((absolute, history[absolute]))
            else:
                result.append((absolute, self.screen.buffer[absolute - hist_len]))
        return result

    def _line_at(self, absolute: int) -> dict | None:
        history = self.screen.history.top if not self.screen.in_alt_screen else ()
        if absolute < 0:
            return None
        if absolute < len(history):
            return history[absolute]
        y = absolute - len(history)
        if 0 <= y < self._rows:
            return self.screen.buffer[y]
        return None

    # Colours ---------------------------------------------------------------

    def _build_color_cache(self) -> None:
        pal = self._palette
        self._fg_default = QColor(pal.foreground)
        self._bg_default = QColor(pal.background)
        self._cursor_color = QColor(pal.cursor)
        self._selection_color = QColor(pal.selection)
        self._color_cache: dict[str, QColor] = {}
        for i, name in enumerate(ANSI_NAMES):
            self._color_cache[name] = QColor(pal.ansi[i])
            self._color_cache["bright" + name] = QColor(pal.ansi[i + 8])
        self._color_cache["default"] = self._fg_default

    def _color(self, value: str, *, bold: bool = False, is_fg: bool = True) -> QColor:
        if value == "default":
            return self._fg_default if is_fg else self._bg_default
        if bold and is_fg and value in ANSI_NAMES:
            value = "bright" + value
        color = self._color_cache.get(value)
        if color is None:
            if len(value) == 6:
                color = QColor("#" + value)
            else:
                color = self._fg_default if is_fg else self._bg_default
            self._color_cache[value] = color
        return color

    # Painting --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: C901
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(self.rect(), self._bg_default)
        painter.setFont(self._font)
        cw, ch = self._cell_w, self._cell_h
        ox, oy = 4.0, 2.0
        default_char = self.screen.default_char
        selection = self._normalized_selection()

        for row, (absolute, line) in enumerate(self._visible_lines()):
            y = oy + row * ch
            # Backgrounds first
            x = 0
            while x < self._cols:
                char = line.get(x, default_char)
                fg_name, bg_name = char.fg, char.bg
                if char.reverse:
                    fg_name, bg_name = (bg_name if bg_name != "default" else "__fg__"), (fg_name if fg_name != "default" else "__fg__")
                run_end = x + 1
                while run_end < self._cols:
                    nxt = line.get(run_end, default_char)
                    nbg = nxt.bg if not nxt.reverse else (nxt.fg if nxt.fg != "default" else "__fg__")
                    if nbg != bg_name:
                        break
                    run_end += 1
                bg = self._resolve_bg(bg_name)
                if bg is not None:
                    painter.fillRect(QRectF(ox + x * cw, y, (run_end - x) * cw, ch), bg)
                x = run_end
            # Selection overlay
            if selection is not None:
                sel_range = self._selection_columns(selection, absolute)
                if sel_range is not None:
                    c0, c1 = sel_range
                    painter.fillRect(QRectF(ox + c0 * cw, y, (c1 - c0 + 1) * cw, ch), self._selection_color)
            # Text runs
            x = 0
            while x < self._cols:
                char = line.get(x, default_char)
                if char.data in ("", " ") and not char.underscore and not char.strikethrough:
                    x += 1
                    continue
                style = (char.fg, char.bg, char.bold, char.italics, char.underscore, char.reverse, char.strikethrough)
                text = char.data
                run_end = x + 1
                if text.isascii():
                    while run_end < self._cols:
                        nxt = line.get(run_end, default_char)
                        if (nxt.fg, nxt.bg, nxt.bold, nxt.italics, nxt.underscore, nxt.reverse, nxt.strikethrough) != style:
                            break
                        if not nxt.data.isascii() or nxt.data == "":
                            break
                        text += nxt.data or " "
                        run_end += 1
                fg_name = char.fg if not char.reverse else (char.bg if char.bg != "default" else "__bg__")
                color = self._resolve_fg(fg_name, char.bold)
                if selection is not None:
                    sel_range = self._selection_columns(selection, absolute)
                    if sel_range is not None and sel_range[0] <= x <= sel_range[1] and not self._palette.is_dark:
                        color = self._fg_default
                painter.setPen(color)
                if char.bold and char.italics:
                    painter.setFont(self._bold_italic_font)
                elif char.bold:
                    painter.setFont(self._bold_font)
                elif char.italics:
                    painter.setFont(self._italic_font)
                else:
                    painter.setFont(self._font)
                painter.drawText(QPointF(ox + x * cw, y + self._ascent), text)
                if char.underscore:
                    uy = y + self._ascent + 2
                    painter.drawLine(QPointF(ox + x * cw, uy), QPointF(ox + run_end * cw, uy))
                if char.strikethrough:
                    sy = y + ch / 2
                    painter.drawLine(QPointF(ox + x * cw, sy), QPointF(ox + run_end * cw, sy))
                x = run_end

        # Cursor
        if self._scroll_offset == 0 and not self.screen.cursor.hidden and self._connected:
            cx, cy = self.screen.cursor.x, self.screen.cursor.y
            if cx >= self._cols:
                cx = self._cols - 1
            rect = QRectF(ox + cx * cw, oy + cy * ch, cw, ch)
            if self.hasFocus():
                if self._cursor_visible_phase or not self._blink_enabled:
                    painter.fillRect(rect, self._cursor_color)
                    line = self.screen.buffer[cy]
                    char = line.get(cx, default_char)
                    if char.data and char.data != " ":
                        painter.setPen(self._bg_default)
                        painter.setFont(self._font)
                        painter.drawText(QPointF(rect.x(), rect.y() + self._ascent), char.data)
            else:
                painter.setPen(QPen(self._cursor_color, 1))
                painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()

    def _resolve_bg(self, name: str) -> QColor | None:
        if name == "default":
            return None
        if name == "__fg__":
            return self._fg_default
        return self._color(name, is_fg=False)

    def _resolve_fg(self, name: str, bold: bool) -> QColor:
        if name == "__bg__":
            return self._bg_default
        return self._color(name, bold=bold, is_fg=True)

    def _toggle_blink(self) -> None:
        self._cursor_visible_phase = not self._cursor_visible_phase
        self.update()

    def focusInEvent(self, event) -> None:
        self._cursor_visible_phase = True
        if self._blink_enabled:
            self._blink_timer.start()
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        self._blink_timer.stop()
        super().focusOutEvent(event)
        self.update()

    # Keyboard --------------------------------------------------------------

    def event(self, event: QEvent) -> bool:
        # Make sure Tab and friends reach keyPressEvent instead of moving focus.
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()  # type: ignore[attr-defined]
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.keyPressEvent(event)  # type: ignore[arg-type]
                return True
        if event.type() == QEvent.Type.ShortcutOverride:
            # Claim ordinary keys so application shortcuts (e.g. Ctrl+W) don't swallow terminal input,
            # except for the few we deliberately keep for the app.
            key_event: QKeyEvent = event  # type: ignore[assignment]
            mods = key_event.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.ShiftModifier:
                return super().event(event)
            if mods & Qt.KeyboardModifier.ControlModifier and key_event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                return super().event(event)
            if mods & Qt.KeyboardModifier.AltModifier and not key_event.text():
                return super().event(event)
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: C901
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        text = event.text()

        # Local shortcuts
        if ctrl and shift and key == Qt.Key.Key_C or (ctrl and key == Qt.Key.Key_Insert):
            self.copy_selection()
            return
        if ctrl and shift and key == Qt.Key.Key_V or (shift and key == Qt.Key.Key_Insert):
            self.paste()
            return
        if self.clipboard_ctrl_keys and ctrl and not shift and not alt:
            # VS Code style: Ctrl+C copies when something is selected, otherwise it is ^C for the
            # shell; Ctrl+V always pastes.
            if key == Qt.Key.Key_C and self._selection is not None:
                self.copy_selection()
                self._selection = None
                self.update()
                return
            if key == Qt.Key.Key_V:
                self.paste()
                return
        if shift and key == Qt.Key.Key_PageUp:
            self.scroll_by(self._rows)
            return
        if shift and key == Qt.Key.Key_PageDown:
            self.scroll_by(-self._rows)
            return
        if ctrl and shift and key == Qt.Key.Key_A:
            self.select_all()
            return
        if ctrl and shift:
            event.ignore()
            return

        data = self._encode_key(key, text, ctrl, shift, alt)
        if data is None:
            event.ignore()
            return
        self._send(data)
        self.key_typed.emit(data)
        self.scroll_to_bottom()
        self._selection = None
        event.accept()

    def _encode_key(self, key: int, text: str, ctrl: bool, shift: bool, alt: bool) -> bytes | None:  # noqa: C901
        K = Qt.Key
        mod_param = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)

        def csi(final: str, number: int | None = None) -> bytes:
            if mod_param > 1:
                if number is None:
                    return f"\x1b[1;{mod_param}{final}".encode()
                return f"\x1b[{number};{mod_param}~".encode()
            if number is None:
                return f"\x1b[{final}".encode()
            return f"\x1b[{number}~".encode()

        special = {
            K.Key_Up: ("A", None),
            K.Key_Down: ("B", None),
            K.Key_Right: ("C", None),
            K.Key_Left: ("D", None),
            K.Key_Home: ("H", None),
            K.Key_End: ("F", None),
            K.Key_Insert: ("~", 2),
            K.Key_Delete: ("~", 3),
            K.Key_PageUp: ("~", 5),
            K.Key_PageDown: ("~", 6),
            K.Key_F5: ("~", 15),
            K.Key_F6: ("~", 17),
            K.Key_F7: ("~", 18),
            K.Key_F8: ("~", 19),
            K.Key_F9: ("~", 20),
            K.Key_F10: ("~", 21),
            K.Key_F11: ("~", 23),
            K.Key_F12: ("~", 24),
        }
        if key in special:
            final, number = special[key]
            return csi(final, number)
        ss3 = {K.Key_F1: "P", K.Key_F2: "Q", K.Key_F3: "R", K.Key_F4: "S"}
        if key in ss3:
            if mod_param > 1:
                return f"\x1b[1;{mod_param}{ss3[key]}".encode()
            return f"\x1bO{ss3[key]}".encode()
        if key in (K.Key_Return, K.Key_Enter):
            return b"\r" if not alt else b"\x1b\r"
        if key == K.Key_Backspace:
            return b"\x08" if ctrl else (b"\x1b\x7f" if alt else b"\x7f")
        if key in (K.Key_Tab, K.Key_Backtab) and ctrl:
            return None  # Ctrl+Tab cycles tabs in the app
        if key == K.Key_Tab:
            return b"\t"
        if key == K.Key_Backtab:
            return b"\x1b[Z"
        if key == K.Key_Escape:
            return b"\x1b"
        if key == K.Key_Space and ctrl:
            return b"\x00"
        if ctrl and not alt:
            if K.Key_A <= key <= K.Key_Z:
                return bytes([key - K.Key_A + 1])
            ctrl_map = {K.Key_BracketLeft: 0x1B, K.Key_Backslash: 0x1C, K.Key_BracketRight: 0x1D, K.Key_AsciiCircum: 0x1E, K.Key_Underscore: 0x1F}
            if key in ctrl_map:
                return bytes([ctrl_map[key]])
            if text and ord(text[0]) < 32:
                return text.encode("utf-8")
            return None
        if not text:
            return None
        payload = text.encode("utf-8")
        if alt and not ctrl:
            return b"\x1b" + payload
        return payload

    def _send(self, data: bytes) -> None:
        self.data_to_send.emit(data)

    def inputMethodEvent(self, event) -> None:
        commit = event.commitString()
        if commit:
            self._send(commit.encode("utf-8"))
            self.scroll_to_bottom()
        event.accept()

    # Clipboard -------------------------------------------------------------

    def paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        self.paste_text(text)

    def paste_text(self, text: str) -> None:
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        payload = text.encode("utf-8")
        if self.screen.bracketed_paste:
            payload = b"\x1b[200~" + payload + b"\x1b[201~"
        self._send(payload)
        self.scroll_to_bottom()

    def copy_selection(self) -> bool:
        text = self.selected_text()
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        return True

    def select_all(self) -> None:
        total = self._history_len() + self._rows
        self._selection = ((0, 0), (self._cols - 1, total - 1))
        self.update()

    def selected_text(self) -> str:
        selection = self._normalized_selection()
        if selection is None:
            return ""
        (c0, r0), (c1, r1) = selection
        lines: list[str] = []
        default_char = self.screen.default_char
        for absolute in range(r0, r1 + 1):
            line = self._line_at(absolute)
            if line is None:
                continue
            start = c0 if absolute == r0 else 0
            end = c1 if absolute == r1 else self._cols - 1
            chars = [line.get(x, default_char).data for x in range(start, end + 1)]
            lines.append("".join(c if c else "" for c in chars).rstrip())
        return "\n".join(lines)

    # Mouse -----------------------------------------------------------------

    def _cell_at(self, pos: QPoint) -> tuple[int, int]:
        col = int((pos.x() - 4) // self._cell_w)
        row = int((pos.y() - 2) // self._cell_h)
        col = max(0, min(self._cols - 1, col))
        row = max(0, min(self._rows - 1, row))
        absolute = self._history_len() - self._scroll_offset + row
        return col, absolute

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        button = event.button()
        if button == Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position().toPoint())
            self._anchor = cell
            self._selection = None
            self._selecting = True
            self.update()
        elif button == Qt.MouseButton.MiddleButton:
            self.paste()
        elif button == Qt.MouseButton.RightButton:
            if self.paste_on_right_click:
                if self._selection is not None:
                    self.copy_selection()
                    self._selection = None
                    self.update()
                else:
                    self.paste()
            else:
                self._context_menu(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._selecting and self._anchor is not None:
            cell = self._cell_at(event.position().toPoint())
            self._selection = (self._anchor, cell)
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            if self._selection is not None and self.copy_on_select:
                self.copy_selection()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        col, absolute = self._cell_at(event.position().toPoint())
        line = self._line_at(absolute)
        if line is None:
            return
        default_char = self.screen.default_char

        def is_word(x: int) -> bool:
            data = line.get(x, default_char).data
            return bool(data) and (data.isalnum() or data in "_-./~:@")

        if not is_word(col):
            return
        start = col
        while start > 0 and is_word(start - 1):
            start -= 1
        end = col
        while end < self._cols - 1 and is_word(end + 1):
            end += 1
        self._selection = ((start, absolute), (end, absolute))
        if self.copy_on_select:
            self.copy_selection()
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        if self.screen.in_alt_screen:
            # Translate wheel to arrow keys for full-screen apps (less, vim, htop).
            key = b"\x1b[A" if delta > 0 else b"\x1b[B"
            for _ in range(3):
                self._send(key)
        else:
            self.scroll_by(3 if delta > 0 else -3)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        if not self.paste_on_right_click:
            return  # handled in mousePressEvent
        self._context_menu(event.globalPos())

    def _context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        copy_action = menu.addAction("Copy")
        copy_action.setEnabled(self._selection is not None)
        paste_action = menu.addAction("Paste")
        menu.addSeparator()
        select_all_action = menu.addAction("Select all")
        clear_action = menu.addAction("Clear scrollback")
        chosen = menu.exec(global_pos)
        if chosen == copy_action:
            self.copy_selection()
        elif chosen == paste_action:
            self.paste()
        elif chosen == select_all_action:
            self.select_all()
        elif chosen == clear_action:
            self.clear_scrollback()

    # Selection helpers -----------------------------------------------------

    def _normalized_selection(self):
        if self._selection is None:
            return None
        (c0, r0), (c1, r1) = self._selection
        if (r0, c0) > (r1, c1):
            return (c1, r1), (c0, r0)
        return (c0, r0), (c1, r1)

    def _selection_columns(self, selection, absolute: int) -> tuple[int, int] | None:
        (c0, r0), (c1, r1) = selection
        if absolute < r0 or absolute > r1:
            return None
        start = c0 if absolute == r0 else 0
        end = c1 if absolute == r1 else self._cols - 1
        return start, end


def is_qt_app_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return True
    return app.palette().window().color().lightness() < 128
