"""Analyzer: paste or load bytes and see them decoded."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ..application.packet_analyzer import PacketAnalyzer
from ..protocol.values import parse_int
from .widgets import DecodedTree, Page, hint_label, mono_font, set_combo_items

AUTO = "Auto detect"


class AnalyzerPage(Page):
    key = "analyzer"
    title = "Packet Analyzer"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.layout_.addWidget(hint_label(
            "Paste raw bytes in hex (e.g. 'AA 01 10 01 05 94'). Several frames and garbage in between are "
            "detected automatically; choose a message to force decoding as that message."
        ))
        split = QSplitter(Qt.Orientation.Vertical)
        self.layout_.addWidget(split, 1)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        self.input = QPlainTextEdit()
        self.input.setFont(mono_font(11))
        self.input.setPlaceholderText("AA 01 10 01 05 94")
        tv.addWidget(self.input, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Decode as:"))
        self.frame = QComboBox()
        self.frame.setMinimumWidth(200)
        row.addWidget(self.frame)
        row.addWidget(QLabel("CAN ID:"))
        self.can_id = QLineEdit()
        self.can_id.setPlaceholderText("optional")
        self.can_id.setMaximumWidth(120)
        row.addWidget(self.can_id)
        row.addStretch(1)
        self.btn_load = QPushButton("Load binary file…")
        self.btn_analyze = QPushButton("Analyze")
        self.btn_analyze.setDefault(True)
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_analyze)
        tv.addLayout(row)
        split.addWidget(top)
        self.tree = DecodedTree()
        split.addWidget(self.tree)
        split.setSizes([160, 440])
        self.summary = QLabel()
        self.layout_.addWidget(self.summary)

        self.btn_analyze.clicked.connect(self.analyze)
        self.btn_load.clicked.connect(self._load_file)
        ctx.protocol_edited.connect(self.on_protocol_changed)
        self.on_protocol_changed()

    def on_protocol_changed(self) -> None:
        set_combo_items(self.frame, [AUTO] + [f.name for f in self.ctx.protocol.frames])

    def on_show(self, argument=None) -> None:
        self.on_protocol_changed()
        if isinstance(argument, (bytes, bytearray)):
            self.input.setPlainText(bytes(argument).hex(" ").upper())
            self.analyze()

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load binary capture", "", "All files (*)")
        if path:
            data = Path(path).read_bytes()[:65536]
            self.input.setPlainText(data.hex(" ").upper())
            self.analyze()

    def analyze(self) -> int:
        text = self.input.toPlainText()
        frame = self.frame.currentText()
        try:
            can_id = parse_int(self.can_id.text()) if self.can_id.text().strip() else None
            events = PacketAnalyzer(self.ctx.protocol).analyze(text, None if frame == AUTO else frame, can_id)
        except (ValueError, KeyError) as exc:
            self.tree.clear()
            self.summary.setText(f"✗ {exc}")
            return 0
        messages = [e.message for e in events if e.kind == "message"]
        garbage = [e.data for e in events if e.kind == "garbage"]
        self.tree.show_events(events)
        valid = sum(1 for m in messages if m.valid)
        self.summary.setText(
            f"{len(messages)} message(s): {valid} valid, {len(messages) - valid} invalid"
            + (f", {sum(len(g) for g in garbage)} unrecognised byte(s)" if garbage else "")
        )
        return len(messages)
