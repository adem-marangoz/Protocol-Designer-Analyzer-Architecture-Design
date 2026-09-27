"""Logs: the session traffic log and the application log file."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QTabWidget

from .. import paths
from .widgets import Page, mono_font


class LogsPage(Page):
    key = "logs"
    title = "Logs"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.tabs = QTabWidget()
        self.traffic = QPlainTextEdit()
        self.app_log = QPlainTextEdit()
        for view in (self.traffic, self.app_log):
            view.setReadOnly(True)
            view.setFont(mono_font())
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.tabs.addTab(self.traffic, "Traffic (decoded)")
        self.tabs.addTab(self.app_log, "Application log")
        self.layout_.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_folder = QPushButton("Open logs folder")
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        row.addWidget(self.btn_folder)
        self.layout_.addLayout(row)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_folder.clicked.connect(self._open_folder)

    def on_show(self, argument=None) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.traffic.setPlainText(self.ctx.traffic.to_text() or "No traffic yet.")
        logfile = paths.logs_dir() / "protocol_designer.log"
        try:
            text = logfile.read_text(encoding="utf-8", errors="replace")
            self.app_log.setPlainText(text[-200_000:])
        except OSError:
            self.app_log.setPlainText(f"No log file yet ({logfile}).")
        self.traffic.moveCursor(self.traffic.textCursor().MoveOperation.End)

    def _open_folder(self) -> None:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir())))
