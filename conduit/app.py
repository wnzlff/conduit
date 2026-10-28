"""Application entry point."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from conduit import APP_NAME, __version__


def _configure_logging() -> None:
    level = logging.DEBUG if "--debug" in sys.argv else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("paramiko").setLevel(logging.WARNING if level != logging.DEBUG else logging.DEBUG)


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    argv = list(sys.argv if argv is None else argv)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if sys.platform == "win32":
        # Give the process its own taskbar identity so Windows shows our icon instead of python.exe's.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.App.{__version__}")
        except Exception:  # pragma: no cover - cosmetic only
            pass
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)

    from conduit.ui.icons import app_icon
    from conduit.ui.main_window import MainWindow
    from conduit.ui.theme import apply_theme, get_theme

    app.setWindowIcon(app_icon())
    window = MainWindow()
    apply_theme(app, get_theme(window.settings.theme))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
