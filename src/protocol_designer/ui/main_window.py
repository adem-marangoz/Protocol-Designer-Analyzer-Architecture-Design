"""Main window: navigation (Section 15), menus, file handling."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from .. import APP_NAME, paths
from ..protocol.errors import ProtocolError
from .about import AboutDialog
from .analyzer_page import AnalyzerPage
from .builder_page import BuilderPage
from .codegen_page import CodegenPage
from .context import AppContext
from .dashboard import DashboardPage
from .logs_page import LogsPage
from .message_editor import MessagePage
from .monitor_page import MonitorPage
from .protocol_editor import ProtocolPage
from .test_page import TestPage
from .widgets import Page

FILE_FILTER = "Protocol files (*.json *.pdproj);;All files (*)"

PAGES = [DashboardPage, ProtocolPage, MessagePage, BuilderPage, AnalyzerPage, MonitorPage, TestPage, CodegenPage, LogsPage]
NAV_TEXT = {
    "dashboard": "Dashboard",
    "protocols": "Protocols",
    "messages": "Messages",
    "builder": "Packet Builder",
    "analyzer": "Analyzer",
    "monitor": "Monitor",
    "tests": "Test",
    "codegen": "Code Generator",
    "logs": "Logs",
}


class MainWindow(QMainWindow):
    def __init__(self, ctx: Optional[AppContext] = None):
        super().__init__()
        self.ctx = ctx or AppContext()
        self.setMinimumSize(QSize(1000, 680))
        icon = paths.resource("app.png")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(170)
        self.nav.setSpacing(2)
        self.nav.setStyleSheet(
            "QListWidget { border: none; background: palette(window); padding-top: 8px; }"
            "QListWidget::item { padding: 8px 12px; border-radius: 4px; }"
            "QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
        )
        self.stack = QStackedWidget()
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages: Dict[str, Page] = {}
        for cls in PAGES:
            page = cls(self.ctx)
            self.pages[page.key] = page
            self.stack.addWidget(page)
            item = QListWidgetItem(NAV_TEXT[page.key])
            item.setData(Qt.ItemDataRole.UserRole, page.key)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._nav_changed)

        dash: DashboardPage = self.pages["dashboard"]  # type: ignore[assignment]
        dash.new_requested.connect(self.new_protocol)
        dash.open_requested.connect(self.open_dialog)
        dash.open_path_requested.connect(self.open_file)

        self.conn_status = QLabel()
        self.statusBar().addPermanentWidget(self.conn_status)
        self._build_menus()

        self.ctx.navigate.connect(self.show_page)
        self.ctx.status_message.connect(lambda text: self.statusBar().showMessage(text, 8000))
        self.ctx.connection_changed.connect(self._on_connection)
        for signal in (self.ctx.protocol_changed, self.ctx.protocol_edited):
            signal.connect(self._update_title)
            signal.connect(lambda: self._on_connection(self.ctx.connected, ""))
        self._pending_argument = None
        self.nav.setCurrentRow(0)
        self._update_title()
        self._on_connection(False, "")
        self._restore_geometry()

    # ------------------------------------------------------------- menus ---

    def _action(self, menu, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        bar = self.menuBar()
        m = bar.addMenu("&File")
        self._action(m, "&New Protocol", self.new_protocol, QKeySequence.StandardKey.New)
        self._action(m, "&Open…", self.open_dialog, QKeySequence.StandardKey.Open)
        self.recent_menu = m.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        self._action(m, "&Save", self.save, QKeySequence.StandardKey.Save)
        self._action(m, "Save &As…", self.save_as, QKeySequence.StandardKey.SaveAs)
        m.addSeparator()
        self._action(m, "Open My Protocols Folder", lambda: self._open_folder(paths.protocols_dir()))
        self._action(m, "Restore Example Protocols", self._restore_examples)
        m.addSeparator()
        self._action(m, "E&xit", self.close, QKeySequence.StandardKey.Quit)

        m = bar.addMenu("&Connection")
        self.connect_action = self._action(m, "&Connect", self.toggle_connection, "F5")

        m = bar.addMenu("&View")
        for i, cls in enumerate(PAGES):
            self._action(m, NAV_TEXT[cls.key], lambda _=False, k=cls.key: self.show_page(k), f"Ctrl+{i + 1}")

        m = bar.addMenu("&Help")
        self._action(m, "&About", lambda: AboutDialog(self, "about").exec())
        self._action(m, "&License", lambda: AboutDialog(self, "license").exec())
        self._action(m, "Third-party Licenses", lambda: AboutDialog(self, "third_party").exec())
        m.addSeparator()
        self._action(m, "Open User Data Folder", lambda: self._open_folder(paths.documents_dir()))
        self._action(m, "Open Logs Folder", lambda: self._open_folder(paths.logs_dir()))

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        files = self.ctx.settings.recent_files
        if not files:
            a = self.recent_menu.addAction("(empty)")
            a.setEnabled(False)
            return
        for path in files:
            a = self.recent_menu.addAction(path)
            a.triggered.connect(lambda _=False, p=path: self.open_file(p))

    # -------------------------------------------------------- navigation ---

    def show_page(self, key: str, argument=None) -> None:
        keys = [cls.key for cls in PAGES]
        if key not in keys:
            return
        self._pending_argument = argument
        row = keys.index(key)
        if self.nav.currentRow() == row:
            self._nav_changed(row)
        else:
            self.nav.setCurrentRow(row)

    def _nav_changed(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        argument, self._pending_argument = self._pending_argument, None
        page.on_show(argument)

    @property
    def current_page(self) -> Page:
        return self.stack.currentWidget()

    # -------------------------------------------------------------- files ---

    def _update_title(self) -> None:
        self.setWindowTitle(f"{self.ctx.manager.title} — {self.ctx.protocol.name} — {APP_NAME}")

    def maybe_save(self) -> bool:
        """Ask to save unsaved changes. Returns False if the user cancelled."""
        if not self.ctx.manager.modified:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to {self.ctx.manager.title.rstrip(' *')}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def new_protocol(self) -> None:
        if self.maybe_save():
            self.ctx.manager.new()
            self.ctx.manager.add_frame("READ_SENSOR")
            self.ctx.manager.modified = False
            self.ctx.protocol_edited.emit()
            self.show_page("protocols")

    def open_dialog(self) -> None:
        if not self.maybe_save():
            return
        start = str(paths.protocols_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Open protocol", start, FILE_FILTER)
        if path:
            self.open_file(path, ask=False)

    def open_file(self, path: str, ask: bool = True) -> bool:
        if ask and not self.maybe_save():
            return False
        try:
            self.ctx.manager.open(path)
        except (ProtocolError, OSError) as exc:
            QMessageBox.warning(self, "Cannot open protocol", str(exc))
            self.ctx.settings.remove_recent(path)
            return False
        self.ctx.settings.add_recent(path)
        self._save_settings()
        self.statusBar().showMessage(f"Opened {path}", 5000)
        return True

    def save(self) -> bool:
        if self.ctx.manager.path is None:
            return self.save_as()
        return self._save_to(self.ctx.manager.path)

    def save_as(self) -> bool:
        from ..application.protocol_manager import safe_filename

        suggested = self.ctx.manager.path or (paths.protocols_dir() / f"{safe_filename(self.ctx.protocol.name)}.json")
        paths.protocols_dir().mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save protocol as", str(suggested), FILE_FILTER)
        if not path:
            return False
        return self._save_to(Path(path))

    def _save_to(self, path: Path) -> bool:
        try:
            saved = self.ctx.manager.save(path)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot save", str(exc))
            return False
        self.ctx.settings.add_recent(str(saved))
        self._save_settings()
        self.statusBar().showMessage(f"Saved {saved}", 5000)
        return True

    def _restore_examples(self) -> None:
        copied = paths.copy_examples(overwrite=False)
        QMessageBox.information(
            self, "Example protocols",
            f"{len(copied)} example(s) copied to\n{paths.protocols_dir()}\n\nExisting files were not changed.",
        )

    def _open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --------------------------------------------------------- connection ---

    def toggle_connection(self) -> None:
        monitor: MonitorPage = self.pages["monitor"]  # type: ignore[assignment]
        self.show_page("monitor")
        monitor.toggle_connection()

    def _on_connection(self, connected: bool, message: str) -> None:
        text = self.ctx.protocol.transport.summary()
        self.conn_status.setText(f"{'● Connected' if connected else '○ Disconnected'}  {text}")
        self.connect_action.setText("&Disconnect" if connected else "&Connect")

    # ------------------------------------------------------------ closing ---

    def _restore_geometry(self) -> None:
        s = self.ctx.settings
        if s.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(s.window_geometry.encode()))
        else:
            self.resize(1280, 800)

    def _save_settings(self) -> None:
        self.ctx.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        try:
            self.ctx.settings.save()
        except OSError:
            pass

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        if not self.maybe_save():
            event.ignore()
            return
        test_page: TestPage = self.pages["tests"]  # type: ignore[assignment]
        test_page.stop()
        self.ctx.disconnect_session()
        self._save_settings()
        event.accept()

    def open_initial(self, path: Optional[str]) -> None:
        """Open the file given on the command line, else the last one, else an example."""
        candidates = [path] if path else []
        if self.ctx.settings.last_protocol:
            candidates.append(self.ctx.settings.last_protocol)
        candidates += [str(p) for p in self.ctx.manager.user_protocols()]
        for candidate in candidates:
            if candidate and Path(candidate).is_file() and self.open_file(candidate, ask=False):
                return
        QTimer.singleShot(0, lambda: self.show_page("dashboard"))
