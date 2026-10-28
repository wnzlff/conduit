"""Application themes (Qt style sheets) and the matching terminal palettes."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from conduit.ui.icons import icon_file
from conduit.ui.terminal_widget import DARK_PALETTE, LIGHT_PALETTE, TerminalPalette


@dataclass(frozen=True)
class Theme:
    name: str
    window: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    danger: str
    success: str
    selection: str
    terminal: TerminalPalette


DARK = Theme(
    name="dark",
    window="#0f1218",
    surface="#161a22",
    surface_alt="#1d2230",
    border="#262c3a",
    text="#d6dbe5",
    text_muted="#8a93a6",
    accent="#7aa2f7",
    accent_hover="#92b4ff",
    danger="#f7768e",
    success="#9ece6a",
    selection="#2b3a5c",
    terminal=DARK_PALETTE,
)

LIGHT = Theme(
    name="light",
    window="#f3f4f8",
    surface="#ffffff",
    surface_alt="#eceef4",
    border="#d7dbe6",
    text="#1f2533",
    text_muted="#6b7385",
    accent="#2f6feb",
    accent_hover="#1f5fd8",
    danger="#d7263d",
    success="#2e8b57",
    selection="#d6e4ff",
    terminal=LIGHT_PALETTE,
)

THEMES = {"dark": DARK, "light": LIGHT}


def get_theme(name: str) -> Theme:
    return THEMES.get(name, DARK)


def build_stylesheet(t: Theme) -> str:
    chevron_down = icon_file("chevron-down", t.text_muted)
    chevron_right = icon_file("chevron-right", t.text_muted)
    chevron_up = icon_file("up", t.text_muted)
    check_white = icon_file("check", "#ffffff")
    close_muted = icon_file("close", t.text_muted)
    return f"""
    * {{
        font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
        font-size: 10pt;
    }}
    QMainWindow, QDialog, QWidget#root {{
        background: {t.window};
        color: {t.text};
    }}
    QWidget {{
        color: {t.text};
        selection-background-color: {t.accent};
        selection-color: #ffffff;
    }}
    QToolTip {{
        background: {t.surface_alt};
        color: {t.text};
        border: 1px solid {t.border};
        padding: 4px 6px;
        border-radius: 4px;
    }}
    QMenuBar {{
        background: {t.window};
        color: {t.text};
        padding: 2px 4px;
    }}
    QMenuBar::item {{
        padding: 4px 10px;
        border-radius: 5px;
    }}
    QMenuBar::item:selected {{ background: {t.surface_alt}; }}
    QMenu {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 12px;
        border-radius: 5px;
    }}
    QMenu::item:selected {{ background: {t.accent}; color: #ffffff; }}
    QMenu::item:disabled {{ color: {t.text_muted}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 6px; }}

    QToolBar {{
        background: {t.window};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 4px 6px;
        spacing: 4px;
    }}
    QToolBar QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 5px 9px;
        color: {t.text};
    }}
    QToolBar QToolButton:hover {{ background: {t.surface_alt}; border-color: {t.border}; }}
    QToolBar QToolButton:pressed {{ background: {t.selection}; }}
    QToolBar QToolButton:disabled {{ color: {t.text_muted}; }}

    QStatusBar {{
        background: {t.window};
        color: {t.text_muted};
        border-top: 1px solid {t.border};
    }}
    QStatusBar::item {{ border: none; }}

    QSplitter::handle {{ background: {t.border}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    QTabWidget::pane {{
        border: none;
        background: {t.window};
    }}
    QTabBar {{
        background: {t.window};
        qproperty-drawBase: 0;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {t.text_muted};
        padding: 7px 14px;
        margin-right: 2px;
        border: 1px solid transparent;
        border-bottom: 2px solid transparent;
        min-width: 90px;
    }}
    QTabBar::tab:hover {{ color: {t.text}; background: {t.surface_alt}; border-radius: 6px 6px 0 0; }}
    QTabBar::tab:selected {{
        color: {t.text};
        background: {t.surface};
        border-bottom: 2px solid {t.accent};
        border-radius: 6px 6px 0 0;
    }}
    QTabBar::close-button {{
        image: url("{close_muted}");
        subcontrol-position: right;
        width: 12px;
        height: 12px;
        margin: 2px;
        border-radius: 4px;
    }}
    QTabBar::close-button:hover {{ background: {t.selection}; }}
    QTabBar QToolButton {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 4px;
    }}

    QTreeView, QTreeWidget, QListView, QTableView, QTableWidget {{
        background: {t.surface};
        alternate-background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 4px;
        outline: none;
        show-decoration-selected: 1;
    }}
    QTreeView::item, QTreeWidget::item {{
        padding: 5px 4px;
        border-radius: 5px;
    }}
    QTableView::item {{ padding: 4px 6px; }}
    QTreeView::item:hover, QTreeWidget::item:hover, QTableView::item:hover {{ background: {t.surface_alt}; }}
    QTreeView::item:selected, QTreeWidget::item:selected, QTableView::item:selected {{
        background: {t.selection};
        color: {t.text};
    }}
    QTreeView::branch {{ background: transparent; }}
    QTreeView::branch:has-children:!has-siblings:closed,
    QTreeView::branch:closed:has-children:has-siblings {{
        image: url("{chevron_right}");
        border-image: none;
    }}
    QTreeView::branch:open:has-children:!has-siblings,
    QTreeView::branch:open:has-children:has-siblings {{
        image: url("{chevron_down}");
        border-image: none;
    }}
    QHeaderView::section {{
        background: {t.surface_alt};
        color: {t.text_muted};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 6px 8px;
        font-weight: 600;
    }}

    QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: {t.accent};
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {t.accent};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {t.text_muted}; }}
    QLineEdit#search {{
        padding-left: 10px;
        border-radius: 14px;
        background: {t.surface_alt};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; subcontrol-origin: padding; }}
    QComboBox::down-arrow {{ image: url("{chevron_down}"); width: 12px; height: 12px; margin-right: 6px; }}
    QComboBox QAbstractItemView {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 6px;
        selection-background-color: {t.accent};
        selection-color: #ffffff;
        padding: 4px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
    QSpinBox::up-arrow {{ image: url("{chevron_up}"); width: 10px; height: 10px; }}
    QSpinBox::down-arrow {{ image: url("{chevron_down}"); width: 10px; height: 10px; }}

    QPushButton {{
        background: {t.surface_alt};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 7px 16px;
        min-width: 70px;
    }}
    QPushButton:hover {{ background: {t.selection}; border-color: {t.accent}; }}
    QPushButton:pressed {{ background: {t.border}; }}
    QPushButton:disabled {{ color: {t.text_muted}; background: {t.surface}; }}
    QPushButton#primary {{
        background: {t.accent};
        color: #ffffff;
        border: 1px solid {t.accent};
        font-weight: 600;
    }}
    QPushButton#primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
    QPushButton#primary:disabled {{ background: {t.border}; color: {t.text_muted}; border-color: {t.border}; }}
    QPushButton#danger {{ color: {t.danger}; }}
    QPushButton#danger:hover {{ border-color: {t.danger}; }}
    QPushButton#flat, QToolButton#flat {{
        background: transparent;
        border: 1px solid transparent;
        min-width: 0;
        padding: 5px 8px;
    }}
    QPushButton#flat:hover, QToolButton#flat:hover {{ background: {t.surface_alt}; border-color: {t.border}; }}

    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {t.border};
        border-radius: 4px;
        background: {t.surface};
    }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {t.accent};
        border-color: {t.accent};
    }}
    QCheckBox::indicator:checked {{ image: url("{check_white}"); }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}

    QGroupBox {{
        border: 1px solid {t.border};
        border-radius: 8px;
        margin-top: 14px;
        padding: 12px 8px 6px 8px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {t.text_muted};
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.border};
        min-height: 24px;
        border-radius: 3px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t.border};
        min-width: 24px;
        border-radius: 3px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {t.text_muted}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

    QProgressBar {{
        background: {t.surface_alt};
        border: 1px solid {t.border};
        border-radius: 5px;
        text-align: center;
        height: 14px;
        color: {t.text};
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}

    QLabel#title {{ font-size: 15pt; font-weight: 600; }}
    QLabel#subtitle {{ color: {t.text_muted}; }}
    QLabel#muted {{ color: {t.text_muted}; }}
    QLabel#error {{ color: {t.danger}; }}
    QLabel#mono {{ font-family: "Cascadia Mono", "Consolas", monospace; }}

    QWidget#sidebar {{
        background: {t.window};
        border-right: 1px solid {t.border};
    }}
    QWidget#overlay {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}
    QWidget#sftpPanel {{
        background: {t.window};
        border-left: 1px solid {t.border};
    }}
    QFrame#card {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}
    QDialogButtonBox QPushButton {{ min-width: 84px; }}
    """


def apply_theme(app: QApplication, theme: Theme) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: theme.window,
        QPalette.ColorRole.WindowText: theme.text,
        QPalette.ColorRole.Base: theme.surface,
        QPalette.ColorRole.AlternateBase: theme.surface_alt,
        QPalette.ColorRole.ToolTipBase: theme.surface_alt,
        QPalette.ColorRole.ToolTipText: theme.text,
        QPalette.ColorRole.Text: theme.text,
        QPalette.ColorRole.Button: theme.surface_alt,
        QPalette.ColorRole.ButtonText: theme.text,
        QPalette.ColorRole.Highlight: theme.accent,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: theme.accent,
        QPalette.ColorRole.PlaceholderText: theme.text_muted,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(theme.text_muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(theme.text_muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(theme.text_muted))
    app.setPalette(palette)
    app.setStyleSheet(build_stylesheet(theme))
