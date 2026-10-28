"""Main window: sidebar with saved connections and tabbed sessions."""

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
from conduit.core.models import Connection
from conduit.core.paths import connections_file, settings_file
from conduit.core.secrets import SecretStore
from conduit.core.settings import Settings
from conduit.core.ssh_session import SessionCredentials
from conduit.core.storage import ConnectionStore
from conduit.ui.connection_dialog import ConnectionDialog
from conduit.ui.dialogs import SettingsDialog
from conduit.ui.icons import app_icon, icon, logo_pixmap
from conduit.ui.putty_import_dialog import PuttyImportDialog
from conduit.ui.session_tab import SessionTab
from conduit.ui.sidebar import Sidebar
from conduit.ui.theme import Theme, apply_theme, get_theme

STATE_COLORS = {"connecting": "#e0af68", "connected": "#9ece6a", "disconnected": "#8a93a6"}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load(settings_file())
        self.theme: Theme = get_theme(self.settings.theme)
        self.store = ConnectionStore(connections_file())
        self.store.load()
        self.secrets = SecretStore()

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
        self.sidebar.connect_requested.connect(self.open_session)
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
        self.tabs.currentChanged.connect(lambda _i: self._update_actions())
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
        hint = QLabel("Ctrl+Shift+N new  ·  Ctrl+Shift+K search  ·  Ctrl+Shift+F SFTP  ·  Ctrl+Shift+C/V copy & paste")
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
        self.act_close_tab = action("&Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+Shift+W", "close")
        self.act_sftp = action("SFTP &browser", self.toggle_sftp, "Ctrl+Shift+F", "sftp")
        self.act_sftp.setCheckable(True)
        self.act_edit_current = action("&Edit this connection...", self.edit_current, None, "edit")

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
        session_menu.addActions([self.act_reconnect, self.act_disconnect, self.act_close_tab])
        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addActions([self.act_copy, self.act_paste, self.act_clear])
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_settings)
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
        self.toolbar.addAction(self.act_reconnect)
        self.toolbar.addAction(self.act_disconnect)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.toolbar.addAction(self.act_settings)
        self.addToolBar(self.toolbar)
        for act in (self.act_search, self.act_next_tab, self.act_prev_tab, self.act_copy, self.act_paste):
            self.addAction(act)

    # Helpers ---------------------------------------------------------------

    def current_tab(self) -> SessionTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, SessionTab) else None

    def _all_tabs(self) -> list[SessionTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), SessionTab)]

    def _update_actions(self) -> None:
        tab = self.current_tab()
        has_tab = tab is not None
        self.stack.setCurrentWidget(self.tabs if self.tabs.count() else self.welcome)
        self.act_disconnect.setEnabled(has_tab and tab.state != "disconnected")
        self.act_reconnect.setEnabled(has_tab and tab.state == "disconnected")
        self.act_close_tab.setEnabled(has_tab)
        self.act_sftp.setEnabled(has_tab)
        self.act_edit_current.setEnabled(has_tab)
        self.act_copy.setEnabled(has_tab)
        self.act_paste.setEnabled(has_tab)
        self.act_clear.setEnabled(has_tab)
        self.act_sftp.blockSignals(True)
        self.act_sftp.setChecked(has_tab and tab.sftp_visible())
        self.act_sftp.blockSignals(False)
        if tab is not None:
            self.setWindowTitle(f"{tab.connection.name} — {APP_NAME}")
            self.state_label.setText({"connecting": "Connecting", "connected": "Connected", "disconnected": "Disconnected"}[tab.state])
            tab.terminal.setFocus()
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
            for tab in self._all_tabs():
                if tab.connection.id == conn.id:
                    tab.connection = self.store.get(conn.id) or tab.connection
                    self.tabs.setTabText(self.tabs.indexOf(tab), tab.display_title())

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

    # Sessions --------------------------------------------------------------

    def open_session(self, conn: Connection, credentials: SessionCredentials | None = None) -> SessionTab:
        tab = SessionTab(conn, self.settings, self.secrets, self.theme, credentials=credentials)
        tab.state_changed.connect(lambda state, t=tab: self._tab_state_changed(t, state))
        tab.title_changed.connect(lambda title, t=tab: self.tabs.setTabText(self.tabs.indexOf(t), title))
        tab.status_message.connect(lambda text: self.status_bar.showMessage(text, 6000))
        tab.connection_updated.connect(self._tab_connection_updated)
        index = self.tabs.addTab(tab, tab.display_title())
        self.tabs.setTabToolTip(index, tab.tooltip())
        self.tabs.setCurrentIndex(index)
        self._update_actions()
        QTimer.singleShot(0, tab.start)  # let the terminal get its real size first
        return tab

    def _tab_connection_updated(self) -> None:
        tab = self.sender()
        if isinstance(tab, SessionTab) and self.store.get(tab.connection.id) is not None:
            self.store.upsert(tab.connection)
            self.save_store()

    def _tab_state_changed(self, tab: SessionTab, state: str) -> None:
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        color = tab.connection.color if (state == "connected" and tab.connection.color) else STATE_COLORS[state]
        self.tabs.setTabIcon(index, icon("circle", color, 12))
        self.tabs.setTabToolTip(index, tab.tooltip())
        if tab is self.current_tab():
            self._update_actions()

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if not isinstance(widget, SessionTab):
            return
        if widget.is_connected() and self.settings.confirm_close_session:
            answer = QMessageBox.question(
                self,
                "Close session",
                f"Close the connection to {widget.connection.display_target}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        widget.close_session()
        self.tabs.removeTab(index)
        widget.deleteLater()
        self._update_actions()

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
        for tab in self._all_tabs():
            tab.apply_settings(self.settings, self.theme)
        self._retint_actions()
        self.save_settings()

    def _retint_actions(self) -> None:
        names = {
            self.act_new: "plus", self.act_quick: "plug", self.act_import: "import", self.act_search: "search",
            self.act_disconnect: "disconnect", self.act_reconnect: "refresh", self.act_close_tab: "close",
            self.act_sftp: "sftp", self.act_edit_current: "edit", self.act_copy: "copy",
            self.act_settings: "settings", self.act_sidebar: "menu",
        }
        for act, name in names.items():
            act.setIcon(icon(name, self.theme.text_muted))

    def about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br>A modern SSH client with saved connections, tabs and SFTP.<br><br>"
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
        self.settings.window_geometry = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        self.settings.window_state = base64.b64encode(bytes(self.saveState())).decode("ascii")
        sizes = self.splitter.sizes()
        if sizes and sizes[0] > 0:
            self.settings.sidebar_width = sizes[0]
        self.save_settings()
        event.accept()
