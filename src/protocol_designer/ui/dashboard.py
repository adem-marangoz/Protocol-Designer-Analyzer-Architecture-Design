"""Dashboard: protocol overview, validation status, quick actions, recent files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..protocol.validator import ERROR
from .widgets import ERROR_COLOR, OK_COLOR, WARN_COLOR, Page, hint_label


class DashboardPage(Page):
    key = "dashboard"
    title = "Dashboard"

    open_requested = Signal()
    new_requested = Signal()
    open_path_requested = Signal(str)
    export_requested = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.layout_.addWidget(hint_label(
            "Describe a protocol once (messages, fields, CRC, enums, scaling) and use it to build packets, "
            "analyse traffic, run automated tests and generate C / C++ / Python / C# code."
        ))
        top = QHBoxLayout()
        self.layout_.addLayout(top)

        info = QGroupBox("Current protocol")
        form = QFormLayout(info)
        self.name = QLabel()
        self.file = QLabel()
        self.file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.transport = QLabel()
        self.counts = QLabel()
        self.state = QLabel()
        form.addRow("Name:", self.name)
        form.addRow("File:", self.file)
        form.addRow("Transport:", self.transport)
        form.addRow("Contents:", self.counts)
        form.addRow("Validation:", self.state)
        top.addWidget(info, 2)

        actions = QGroupBox("Quick actions")
        col = QVBoxLayout(actions)
        buttons = [
            ("New protocol", self.new_requested.emit),
            ("Open protocol…", self.open_requested.emit),
            ("Design messages", lambda: ctx.navigate.emit("messages", None)),
            ("Build a packet", lambda: ctx.navigate.emit("builder", None)),
            ("Live monitor", lambda: ctx.navigate.emit("monitor", None)),
            ("Run tests", lambda: ctx.navigate.emit("tests", None)),
            ("Generate code", lambda: ctx.navigate.emit("codegen", None)),
            ("Export specification (PDF)…", self.export_requested.emit),
        ]
        self.action_buttons = {}
        for text, slot in buttons:
            b = QPushButton(text)
            b.clicked.connect(slot)
            col.addWidget(b)
            self.action_buttons[text] = b
        col.addStretch(1)
        top.addWidget(actions, 1)

        lower = QHBoxLayout()
        self.layout_.addLayout(lower, 1)
        issues_box = QGroupBox("Validation issues")
        v = QVBoxLayout(issues_box)
        self.issues = QListWidget()
        v.addWidget(self.issues)
        lower.addWidget(issues_box, 2)

        recent_box = QGroupBox("Recent protocols (double-click to open)")
        v = QVBoxLayout(recent_box)
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(lambda item: self.open_path_requested.emit(item.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(self.recent)
        lower.addWidget(recent_box, 1)

        ctx.protocol_edited.connect(self.refresh)
        ctx.connection_changed.connect(lambda *_: self.refresh())
        self.refresh()

    def on_protocol_changed(self) -> None:
        self.refresh()

    def on_show(self, argument=None) -> None:
        self.refresh()

    def refresh(self) -> None:
        p = self.ctx.protocol
        m = self.ctx.manager
        self.name.setText(f"<b>{p.name}</b> v{p.version}")
        self.file.setText(str(m.path) if m.path else "(not saved yet)")
        conn = " — connected" if self.ctx.connected else ""
        self.transport.setText(p.transport.summary() + conn)
        self.counts.setText(f"{len(p.frames)} message(s), {len(p.enums)} enumeration(s), {len(p.tests)} test(s)")
        issues = m.validate()
        errors = sum(1 for i in issues if i.severity == ERROR)
        warnings = len(issues) - errors
        if not issues:
            self.state.setText("✓ No problems found")
            self.state.setStyleSheet(f"color: {OK_COLOR.name()}")
        else:
            self.state.setText(f"{errors} error(s), {warnings} warning(s)")
            self.state.setStyleSheet(f"color: {(ERROR_COLOR if errors else WARN_COLOR).name()}")
        self.issues.clear()
        for issue in issues:
            item = QListWidgetItem(str(issue))
            item.setForeground(ERROR_COLOR if issue.severity == ERROR else WARN_COLOR)
            self.issues.addItem(item)
        if not issues:
            self.issues.addItem("No problems found.")
        self.recent.clear()
        for path in self.ctx.settings.recent_files:
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.recent.addItem(item)
