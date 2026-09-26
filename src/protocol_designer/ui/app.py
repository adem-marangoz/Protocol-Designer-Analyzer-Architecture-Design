"""Application start-up: logging, first-run setup, main window."""

from __future__ import annotations

import logging
import sys
import traceback
from typing import List, Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, APP_PUBLISHER, APP_SHORT_NAME, __version__, paths
from ..application.logger import setup_logging
from ..application.settings import AppSettings
from .context import AppContext
from .main_window import MainWindow

log = logging.getLogger(__name__)


def _excepthook(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("unhandled exception:\n%s", text)
    app = QApplication.instance()
    if app is not None:
        QMessageBox.critical(None, APP_NAME, f"An unexpected error occurred:\n\n{exc}\n\nDetails were written to the log.")


def create_app(argv: Optional[List[str]] = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_SHORT_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_PUBLISHER)
    app.setStyle("Fusion")
    icon = paths.resource("app.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    return app


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if sys.platform == "win32":
        try:  # separate taskbar identity from python.exe when run from source
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_SHORT_NAME}.{__version__}")
        except Exception:  # noqa: BLE001
            pass
    setup_logging()
    log.info("%s %s starting", APP_NAME, __version__)
    first_run = paths.first_run_setup()
    app = create_app(argv)
    sys.excepthook = _excepthook
    ctx = AppContext(AppSettings.load())
    window = MainWindow(ctx)
    file_arg = next((a for a in argv[1:] if not a.startswith("-")), None)
    window.open_initial(file_arg)
    window.show()
    if first_run:
        window.statusBar().showMessage(f"Welcome! Example protocols were copied to {paths.protocols_dir()}", 15000)
    code = app.exec()
    log.info("exiting with code %s", code)
    return code
