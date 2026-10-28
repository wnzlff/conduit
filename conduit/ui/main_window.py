"""Main window: sidebar with saved connections and tabbed workspaces (each up to four panes)."""

from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from conduit import APP_NAME, __version__
from conduit.core.history import CommandHistory
from conduit.core.models import Connection
from conduit.core.paths import connections_file, history_file, settings_file, snippets_file
from conduit.core.secrets import SecretStore
from conduit.core.settings import Settings
from conduit.core.snippets import SnippetStore
from conduit.core.ssh_session import SessionCredentials
from conduit.core.storage import ConnectionStore
from conduit.ui.connection_dialog import ConnectionDialog
from conduit.ui.dialogs import SettingsDialog
from conduit.ui.icons import app_icon, icon, logo_pixmap
from conduit.ui.putty_import_dialog import PuttyImportDialog
from conduit.ui.session_tab import SessionTab
from conduit.ui.sidebar import Sidebar
from conduit.ui.snippet_dialog import SnippetDialog, SnippetPicker
from conduit.ui.theme import Theme, apply_theme, get_theme
from conduit.ui.workspace import MAX_PANES, Workspace


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load(settings_file())
        self.theme: Theme = get_theme(self.settings.theme)
        self.store = ConnectionStore(connections_file())
        self.store.load()
        self.secrets = SecretStore()
        self.snippets = SnippetStore(snippets_file())
        self.snippets.load()
        self.history = CommandHistory(history_file())
        self.history.load()

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1200, 760)
        self._build_ui()
        self._build_actions()
        self._restore_window()
        self._update_actions()
        self.status_bar.showMessage(f"Ready. Credential store: {self.secrets.backend_name()}", 5000)

    # UI construction -------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)

        self.sidebar = Sidebar(self.store)
        self.sidebar.setMinimumWidth(200)
        self.sidebar.connect_requested.connect(lambda c: self.open_session(c))
        self.sidebar.split_requested.connect(lambda c: self.open_session(c, split=True))
        self.sidebar.edit_requested.connect(self.edit_connection)
        self.sidebar.new_requested.connect(self.new_connection)
        self.sidebar.delete_requested.connect(self.delete_connection)
        self.sidebar.import_requested.connect(self.import_putty)
        self.sidebar.store_changed.connect(self.save_store)
        self.splitter.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.welcome = self._build_welcome()
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.tabs)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self.settings.sidebar_width, 900])

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.state_label = QLabel("")
        self.state_label.setObjectName("muted")
        self.status_bar.addPermanentWidget(self.state_label)

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addStretch(1)
        card = QWidget()
        card.setObjectName("overlay")
        card.setMaximumWidth(460)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(32, 28, 32, 28)
        inner.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(72))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(logo)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel("Pick a connection on the left, or create a new one.")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        inner.addWidget(title)
        inner.addWidget(subtitle)
        inner.addSpacing(8)
        new_button = QPushButton("New connection")
        new_button.setObjectName("primary")
        new_button.clicked.connect(lambda: self.new_connection(""))
        quick_button = QPushButton("Quick connect")
        quick_button.clicked.connect(self.quick_connect)
        import_button = QPushButton("Import PuTTY sessions")
        import_button.clicked.connect(self.import_putty)
        for button in (new_button, quick_button, import_button):
            inner.addWidget(button)
        hint = QLabel(
            "Ctrl+Shift+N new  ·  Ctrl+Shift+K search  ·  Ctrl+Shift+F SFTP  ·  "
            "Shift+double-click a connection to snap it next to the current one"
        )
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        inner.addSpacing(6)
        inner.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        return page

    def _build_actions(self) -> None:
        menubar = self.menuBar()
        c = self.theme.text_muted

        def action(text: str, slot, shortcut: str | None = None, icon_name: str | None = None) -> QAction:
            act = QAction(text, self)
            if icon_name:
                act.setIcon(icon(icon_name, c))
                act.setProperty("iconName", icon_name)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            return act

        # Plain Ctrl+<letter> always goes to the remote shell (like PuTTY), so app shortcuts use Ctrl+Shift.
        self.act_new = action("&New connection...", lambda: self.new_connection(""), "Ctrl+Shift+N", "plus")
        self.act_quick = action("&Quick connect...", self.quick_connect, "Ctrl+Shift+Q", "plug")
        self.act_import = action("&Import PuTTY sessions...", self.import_putty, None, "import")
        self.act_quit = action("&Quit", self.close, None)
        self.act_search = action("Search connections", self.sidebar.focus_search, "Ctrl+Shift+K", "search")

        self.act_disconnect = action("&Disconnect", self.disconnect_current, None, "disconnect")
        self.act_reconnect = action("&Reconnect", self.reconnect_current, "Ctrl+Shift+R", "refresh")
        self.act_close_pane = action("&Close pane", self.close_current_pane, "Ctrl+Shift+W", "close")
        self.act_close_tab = action("Close &tab (all panes)", lambda: self.close_tab(self.tabs.currentIndex()), None)
        self.act_sftp = action("SFTP &browser", self.toggle_sftp, "Ctrl+Shift+F", "sftp")
        self.act_sftp.setCheckable(True)
        self.act_edit_current = action("&Edit this connection...", self.edit_current, None, "edit")

        self.act_split_dup = action("&Duplicate session in split pane", self.duplicate_current_split, "Ctrl+Shift+D", "split")
        self.act_popout = action("Move pane to its &own tab", self.popout_current, "Ctrl+Shift+O", "popout")
        self.act_merge = action("Snap this tab into the &previous tab", self.merge_into_previous, "Ctrl+Shift+J", "merge")
        self.act_pane_left = action("Focus pane left", lambda: self._focus_direction(-1, 0), "Alt+Shift+Left")
        self.act_pane_right = action("Focus pane right", lambda: self._focus_direction(1, 0), "Alt+Shift+Right")
        self.act_pane_up = action("Focus pane up", lambda: self._focus_direction(0, -1), "Alt+Shift+Up")
        self.act_pane_down = action("Focus pane down", lambda: self._focus_direction(0, 1), "Alt+Shift+Down")
        self.act_pane_next = action("Focus next pane", self._focus_next, "Ctrl+Shift+E")

        self.act_copy = action("&Copy", self._copy, "Ctrl+Shift+C", "copy")
        self.act_paste = action("&Paste", self._paste, "Ctrl+Shift+V")
        self.act_clear = action("Clear scrollback", self._clear_scrollback)
        self.act_settings = action("&Settings...", self.open_settings, "Ctrl+Shift+S", "settings")

        self.act_sidebar = action("Toggle &sidebar", self.toggle_sidebar, "Ctrl+Shift+B", "menu")
        self.act_sidebar.setCheckable(True)
        self.act_sidebar.setChecked(True)
        self.act_theme = action("Switch dark / light theme", self.toggle_theme, "Ctrl+Shift+T")
        self.act_next_tab = action("Next tab", lambda: self._cycle_tab(1), "Ctrl+Tab")
        self.act_prev_tab = action("Previous tab", lambda: self._cycle_tab(-1), "Ctrl+Shift+Tab")
        self.act_about = action("&About " + APP_NAME, self.about)

        file_menu = menubar.addMenu("&File")
        file_menu.addActions([self.act_new, self.act_quick, self.act_import])
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)
        session_menu = menubar.addMenu("&Session")
        session_menu.addActions([self.act_sftp, self.act_edit_current])
        session_menu.addSeparator()
        session_menu.addActions([self.act_reconnect, self.act_disconnect, self.act_close_pane, self.act_close_tab])
        split_menu = menubar.addMenu("S&plit view")
        split_menu.addActions([self.act_split_dup, self.act_popout, self.act_merge])
        split_menu.addSeparator()
        split_menu.addActions([self.act_pane_left, self.act_pane_right, self.act_pane_up, self.act_pane_down, self.act_pane_next])
        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addActions([self.act_copy, self.act_paste, self.act_clear])
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_settings)
        self.act_manage_snippets = action("&Manage snippets...", self.manage_snippets, "Ctrl+Shift+M", "copy")
        self.act_pick_snippet = action("&Insert snippet...", self.pick_snippet, "Ctrl+Shift+Space", "terminal")
        self.snippet_menu = menubar.addMenu("S&nippets")
        self._rebuild_snippet_menu()
        view_menu = menubar.addMenu("&View")
        view_menu.addActions([self.act_sidebar, self.act_theme, self.act_search])
        view_menu.addSeparator()
        view_menu.addActions([self.act_next_tab, self.act_prev_tab])
        help_menu = menubar.addMenu("&Help")
        help_menu.addAction(self.act_about)

        self.toolbar = QToolBar("Main")
        self.toolbar.setMovable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar.addAction(self.act_new)
        self.toolbar.addAction(self.act_quick)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.act_sftp)
        self.toolbar.addAction(self.act_split_dup)
        self.toolbar.addAction(self.act_pick_snippet)
        self.toolbar.addAction(self.act_reconnect)
        self.toolbar.addAction(self.act_disconnect)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.toolbar.addAction(self.act_settings)
        self.addToolBar(self.toolbar)
        for act in (
            self.act_search, self.act_next_tab, self.act_prev_tab, self.act_copy, self.act_paste,
            self.act_pane_left, self.act_pane_right, self.act_pane_up, self.act_pane_down, self.act_pane_next,
            self.act_popout, self.act_merge, self.act_split_dup,
        ):
            self.addAction(act)

    # Helpers ---------------------------------------------------------------

    def _tab_changed(self, _index: int) -> None:
        self._update_actions()
        tab = self.current_tab()
        if tab is not None:
            tab.terminal.setFocus()

    def current_workspace(self) -> Workspace | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, Workspace) else None

    def current_tab(self) -> SessionTab | None:
        """The active pane of the current tab."""
        ws = self.current_workspace()
        return ws.active_pane() if ws is not None else None

    def _all_workspaces(self) -> list[Workspace]:
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), Workspace)]

    def _all_tabs(self) -> list[SessionTab]:
        return [pane for ws in self._all_workspaces() for pane in ws.panes()]

    def _update_actions(self) -> None:
        ws = self.current_workspace()
        tab = self.current_tab()
        has_tab = tab is not None
        panes = len(ws.panes()) if ws is not None else 0
        self.stack.setCurrentWidget(self.tabs if self.tabs.count() else self.welcome)
        self.act_disconnect.setEnabled(has_tab and tab.state != "disconnected")
        self.act_reconnect.setEnabled(has_tab and tab.state == "disconnected")
        self.act_close_pane.setEnabled(has_tab)
        self.act_close_tab.setEnabled(has_tab)
        self.act_sftp.setEnabled(has_tab)
        self.act_edit_current.setEnabled(has_tab)
        self.act_copy.setEnabled(has_tab)
        self.act_paste.setEnabled(has_tab)
        self.act_clear.setEnabled(has_tab)
        self.act_split_dup.setEnabled(has_tab and ws is not None and not ws.is_full())
        self.act_popout.setEnabled(panes > 1)
        previous = self.tabs.widget(self.tabs.currentIndex() - 1) if self.tabs.currentIndex() > 0 else None
        self.act_merge.setEnabled(
            ws is not None and isinstance(previous, Workspace) and previous.free_slots() >= panes > 0
        )
        for act in (self.act_pane_left, self.act_pane_right, self.act_pane_up, self.act_pane_down, self.act_pane_next):
            act.setEnabled(panes > 1)
        self.act_sftp.blockSignals(True)
        self.act_sftp.setChecked(has_tab and tab.sftp_visible())
        self.act_sftp.blockSignals(False)
        if tab is not None:
            self.setWindowTitle(f"{tab.connection.name} — {APP_NAME}")
            self.state_label.setText({"connecting": "Connecting", "connected": "Connected", "disconnected": "Disconnected"}[tab.state])
        else:
            self.setWindowTitle(APP_NAME)
            self.state_label.setText("")

    def save_store(self) -> None:
        try:
            self.store.save()
        except OSError as exc:
            QMessageBox.warning(self, "Save failed", f"Could not save connections:\n{exc}")
        self.sidebar.refresh()

    def save_settings(self) -> None:
        try:
            self.settings.save(settings_file())
        except OSError as exc:
            self.status_bar.showMessage(f"Could not save settings: {exc}", 5000)

    # Connections -----------------------------------------------------------

    def new_connection(self, group: str = "") -> None:
        dialog = ConnectionDialog(self.store, self.secrets, default_group=group, parent=self)
        if dialog.exec():
            conn = dialog.result_connection()
            self.store.upsert(conn)
            self.save_store()
            self.open_session(conn, SessionCredentials(dialog.entered_password(), dialog.entered_passphrase()))

    def quick_connect(self) -> None:
        dialog = ConnectionDialog(self.store, self.secrets, connect_mode=True, parent=self)
        if dialog.exec():
            conn = dialog.result_connection()
            if dialog.should_save():
                self.store.upsert(conn)
                self.save_store()
            else:
                self.secrets.delete_all(conn.id)
            self.open_session(conn, SessionCredentials(dialog.entered_password(), dialog.entered_passphrase()))

    def edit_connection(self, conn: Connection) -> None:
        dialog = ConnectionDialog(self.store, self.secrets, conn, parent=self)
        if dialog.exec():
            self.store.upsert(dialog.result_connection())
            self.save_store()
            for pane in self._all_tabs():
                if pane.connection.id == conn.id:
                    pane.connection = self.store.get(conn.id) or pane.connection
            for ws in self._all_workspaces():
                self._refresh_tab(ws)

    def edit_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            stored = self.store.get(tab.connection.id)
            if stored is None:
                self.store.upsert(tab.connection)
            self.edit_connection(self.store.get(tab.connection.id) or tab.connection)

    def delete_connection(self, conn: Connection) -> None:
        answer = QMessageBox.question(
            self,
            "Delete connection",
            f"Delete “{conn.name}” ({conn.display_target})?\nStored passwords for it are removed as well.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.remove(conn.id)
            self.secrets.delete_all(conn.id)
            self.save_store()

    def import_putty(self) -> None:
        dialog = PuttyImportDialog(self.store, self)
        if dialog.exec():
            count = self.store.add_many(dialog.selected_connections())
            self.save_store()
            self.status_bar.showMessage(f"Imported {count} PuTTY session(s).", 5000)

    # Sessions and workspaces -----------------------------------------------

    def open_session(
        self, conn: Connection, credentials: SessionCredentials | None = None, *, split: bool = False
    ) -> SessionTab:
        """Opens a session. With ``split`` it is snapped into the current tab if there is room
        (up to four panes), otherwise it gets a tab of its own."""
        pane = SessionTab(
            conn, self.settings, self.secrets, self.theme,
            credentials=credentials, history=self.history, snippets=self.snippets,
        )
        pane.state_changed.connect(lambda _s, p=pane: self._pane_changed(p))
        pane.title_changed.connect(lambda _t, p=pane: self._pane_changed(p))
        pane.status_message.connect(lambda text: self.status_bar.showMessage(text, 6000))
        pane.connection_updated.connect(self._pane_connection_updated)

        ws = self.current_workspace() if split else None
        if ws is None or ws.is_full():
            if split and ws is not None:
                self.status_bar.showMessage(f"This tab already holds {MAX_PANES} panes, opening a new tab.", 4000)
            ws = self._new_workspace()
        ws.add_pane(pane)
        self.tabs.setCurrentWidget(ws)
        self._refresh_tab(ws)
        self._update_actions()
        QTimer.singleShot(0, pane.start)  # let the terminal get its real size first
        return pane

    def _new_workspace(self) -> Workspace:
        ws = Workspace()
        ws.set_icon_color(self.theme.text_muted)
        ws.changed.connect(lambda w=ws: self._refresh_tab(w))
        ws.became_empty.connect(lambda w=ws: self._remove_workspace(w))
        ws.close_pane_requested.connect(self.close_pane)
        ws.popout_requested.connect(self.popout_pane)
        self.tabs.addTab(ws, "")
        return ws

    def _remove_workspace(self, ws: Workspace) -> None:
        index = self.tabs.indexOf(ws)
        if index >= 0:
            self.tabs.removeTab(index)
        ws.deleteLater()
        self._update_actions()

    def _refresh_tab(self, ws: Workspace) -> None:
        index = self.tabs.indexOf(ws)
        if index < 0:
            return
        self.tabs.setTabText(index, ws.title())
        self.tabs.setTabToolTip(index, ws.tooltip())
        self.tabs.setTabIcon(index, icon("circle", ws.state_color(), 12))
        if ws is self.current_workspace():
            self._update_actions()

    def _pane_changed(self, pane: SessionTab) -> None:
        ws = Workspace.of(pane)
        if ws is not None:
            self._refresh_tab(ws)

    def _pane_connection_updated(self) -> None:
        pane = self.sender()
        if isinstance(pane, SessionTab) and self.store.get(pane.connection.id) is not None:
            self.store.upsert(pane.connection)
            self.save_store()

    def _confirm_close(self, panes: list[SessionTab]) -> bool:
        connected = [p for p in panes if p.is_connected()]
        if not connected or not self.settings.confirm_close_session:
            return True
        if len(connected) == 1:
            text = f"Close the connection to {connected[0].connection.display_target}?"
        else:
            text = f"Close {len(connected)} connected sessions?"
        answer = QMessageBox.question(
            self, "Close session", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def close_pane(self, pane: SessionTab) -> None:
        ws = Workspace.of(pane)
        if ws is None or not self._confirm_close([pane]):
            return
        pane.close_session()
        ws.take_pane(pane)  # emits became_empty -> tab removed when it was the last pane
        pane.deleteLater()
        self._update_actions()

    def close_current_pane(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            self.close_pane(tab)

    def close_tab(self, index: int) -> None:
        ws = self.tabs.widget(index)
        if not isinstance(ws, Workspace):
            return
        panes = ws.panes()
        if not self._confirm_close(panes):
            return
        for pane in panes:
            pane.close_session()
            ws.take_pane(pane)
            pane.deleteLater()
        if self.tabs.indexOf(ws) >= 0:  # became_empty normally removed it already
            self._remove_workspace(ws)
        self._update_actions()

    def popout_pane(self, pane: SessionTab) -> None:
        """Moves a pane out of its workspace into a new tab."""
        ws = Workspace.of(pane)
        if ws is None or len(ws.panes()) < 2:
            return
        ws.take_pane(pane)
        target = self._new_workspace()
        target.add_pane(pane)
        self.tabs.setCurrentWidget(target)
        self._refresh_tab(ws)
        self._refresh_tab(target)
        pane.terminal.setFocus()
        self._update_actions()

    def popout_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            self.popout_pane(tab)

    def merge_into_previous(self) -> None:
        """Snaps all panes of the current tab into the tab to its left."""
        index = self.tabs.currentIndex()
        ws = self.current_workspace()
        if ws is None or index <= 0:
            return
        target = self.tabs.widget(index - 1)
        if not isinstance(target, Workspace):
            return
        panes = ws.panes()
        if target.free_slots() < len(panes):
            self.status_bar.showMessage(
                f"Not enough room: the previous tab has {target.free_slots()} free pane(s), this tab has {len(panes)}.", 5000
            )
            return
        for pane in panes:
            ws.take_pane(pane)
            target.add_pane(pane)
        self.tabs.setCurrentWidget(target)
        self._refresh_tab(target)
        self._update_actions()

    def duplicate_current_split(self) -> None:
        """Opens the current pane's connection once more, snapped next to it."""
        tab = self.current_tab()
        if tab is None:
            return
        creds = SessionCredentials(password=tab.credentials.password, passphrase=tab.credentials.passphrase)
        self.open_session(tab.connection, creds, split=True)

    def _focus_direction(self, dx: int, dy: int) -> None:
        ws = self.current_workspace()
        if ws is not None:
            ws.focus_direction(dx, dy)

    def _focus_next(self) -> None:
        ws = self.current_workspace()
        if ws is not None:
            ws.focus_next()

    def disconnect_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.close_session()
            tab._on_disconnected("")  # show banner immediately
            self._update_actions()

    def reconnect_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.reconnect()

    def toggle_sftp(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.toggle_sftp()
            self._update_actions()

    def _cycle_tab(self, delta: int) -> None:
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % count)

    def _copy(self) -> None:
        tab = self.current_tab()
        if tab is not None and not tab.terminal.copy_selection():
            self.status_bar.showMessage("Nothing selected.", 2000)

    def _paste(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.terminal.paste()

    def _clear_scrollback(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.terminal.clear_scrollback()

    # View / settings -------------------------------------------------------

    def toggle_sidebar(self) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisible())
        self.act_sidebar.setChecked(self.sidebar.isVisible())

    def toggle_theme(self) -> None:
        self.settings.theme = "light" if self.settings.theme == "dark" else "dark"
        self.apply_settings()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.apply_settings()

    def apply_settings(self) -> None:
        self.theme = get_theme(self.settings.theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.theme)
        self.sidebar.set_icon_color(self.theme.text_muted)
        for pane in self._all_tabs():
            pane.apply_settings(self.settings, self.theme)
        for ws in self._all_workspaces():
            ws.set_icon_color(self.theme.text_muted)
        self._retint_actions()
        self.save_settings()

    def _retint_actions(self) -> None:
        for act in self.findChildren(QAction):
            name = act.property("iconName")
            if name:
                act.setIcon(icon(name, self.theme.text_muted))

    # Snippets --------------------------------------------------------------

    def _rebuild_snippet_menu(self) -> None:
        menu = self.snippet_menu
        menu.clear()
        menu.addAction(self.act_pick_snippet)
        menu.addAction(self.act_manage_snippets)
        menu.addSeparator()
        snippets = self.snippets.sorted()
        if not snippets:
            empty = menu.addAction("No snippets yet")
            empty.setEnabled(False)
        for snippet in snippets[:40]:
            label = snippet.name + ("  ⏎" if snippet.run else "")
            act = menu.addAction(icon("copy", self.theme.text_muted), label)
            act.setToolTip(snippet.body)
            act.setStatusTip(snippet.body.splitlines()[0] if snippet.body else "")
            act.triggered.connect(lambda _c=False, s=snippet: self.insert_snippet(s.body, s.run))
        menu.setToolTipsVisible(True)

    def manage_snippets(self) -> None:
        dialog = SnippetDialog(
            self.snippets, can_insert=self.current_tab() is not None, icon_color=self.theme.text_muted, parent=self
        )
        dialog.insert_requested.connect(self.insert_snippet)
        dialog.exec()
        self.snippets.load()
        self._rebuild_snippet_menu()

    def pick_snippet(self) -> None:
        tab = self.current_tab()
        if tab is None:
            self.status_bar.showMessage("Open a session first to insert a snippet.", 3000)
            return
        if not self.snippets.snippets:
            self.manage_snippets()
            return
        picker = SnippetPicker(self.snippets, self.theme.text_muted, self)
        anchor = tab.terminal.mapToGlobal(tab.terminal.rect().center())
        picker.move(anchor.x() - picker.width() // 2, anchor.y() - picker.height() // 2)
        if picker.exec() and picker.choice is not None:
            self.insert_snippet(picker.choice.body, picker.choice.run)

    def insert_snippet(self, body: str, run: bool) -> None:
        tab = self.current_tab()
        if tab is None:
            self.status_bar.showMessage("Open a session first to insert a snippet.", 3000)
            return
        if not tab.is_connected():
            self.status_bar.showMessage("This session is not connected.", 3000)
            return
        tab.insert_snippet(body, run)

    def about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br>A modern SSH client with saved connections, tabs, split panes and SFTP.<br><br>"
            "MIT License. Built with Python, PySide6, paramiko and pyte.",
        )

    # Window state ----------------------------------------------------------

    def _restore_window(self) -> None:
        if self.settings.window_geometry:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(self.settings.window_geometry)))
            except Exception:
                pass
        if self.settings.window_state:
            try:
                self.restoreState(QByteArray(base64.b64decode(self.settings.window_state)))
            except Exception:
                pass

    def closeEvent(self, event: QCloseEvent) -> None:
        connected = [t for t in self._all_tabs() if t.is_connected()]
        if connected and self.settings.confirm_close_session:
            answer = QMessageBox.question(
                self,
                "Quit",
                f"{len(connected)} session(s) are still connected. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for tab in self._all_tabs():
            tab.close_session()
        self.history.save()
        self.settings.window_geometry = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        self.settings.window_state = base64.b64encode(bytes(self.saveState())).decode("ascii")
        sizes = self.splitter.sizes()
        if sizes and sizes[0] > 0:
            self.settings.sidebar_width = sizes[0]
        self.save_settings()
        event.accept()
