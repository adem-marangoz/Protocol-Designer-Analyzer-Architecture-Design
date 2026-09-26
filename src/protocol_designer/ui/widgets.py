"""Small reusable widgets and helpers for the GUI."""

from __future__ import annotations

from typing import Iterable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..protocol.decoder import DecodedMessage
from ..protocol.model import Encoding

OK_COLOR = QColor("#1a7f37")
ERROR_COLOR = QColor("#cf222e")
WARN_COLOR = QColor("#9a6700")
MUTED_COLOR = QColor("#6e7781")


def mono_font(size: int = 0) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    if size:
        font.setPointSize(size)
    return font


def title_label(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 1.5)
    font.setBold(True)
    label.setFont(font)
    return label


def hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #6e7781;")
    return label


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def button_row(*buttons: QPushButton, stretch_first: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    if stretch_first:
        row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    if not stretch_first:
        row.addStretch(1)
    return row


class Page(QWidget):
    """Base class of the pages in the main window."""

    key = ""
    title = ""

    def __init__(self, ctx, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(16, 12, 16, 12)
        self.layout_.addWidget(title_label(self.title))
        ctx.protocol_changed.connect(self.on_protocol_changed)

    def on_protocol_changed(self) -> None:
        """Reload everything that depends on the protocol."""

    def on_show(self, argument=None) -> None:
        """Called when the page becomes visible (argument from navigation)."""

    def error(self, text: str, title: str = "Error") -> None:
        QMessageBox.warning(self, title, text)


class DecodedTree(QTreeWidget):
    """Tree view of decoded messages: message > fields > bit groups."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["Field", "Value", "Raw", "Offset"])
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.header().setStretchLastSection(True)
        self.setColumnWidth(0, 200)
        self.setColumnWidth(1, 220)
        self.setColumnWidth(2, 160)

    def show_messages(self, messages: Iterable[DecodedMessage], garbage: Iterable[bytes] = ()) -> None:
        self.clear()
        for msg in messages:
            self.add_message(msg)
        for data in garbage:
            self.add_garbage(data)
        self.expandAll()

    def show_events(self, events) -> None:
        """Show stream events (messages and unrecognised bytes) in arrival order."""
        self.clear()
        for event in events:
            if event.kind == "message" and event.message is not None:
                self.add_message(event.message)
            else:
                self.add_garbage(event.data)
        self.expandAll()

    def add_garbage(self, data: bytes) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"?? ({len(data)} byte(s))", "no matching frame", " ".join(f"{b:02X}" for b in data), ""])
        item.setForeground(0, ERROR_COLOR)
        self.addTopLevelItem(item)
        return item

    def add_message(self, msg: DecodedMessage) -> QTreeWidgetItem:
        status = "VALID" if msg.valid else ("CRC ERROR" if msg.crc_valid is False else "INVALID")
        top = QTreeWidgetItem([msg.name, status, msg.hex, ""])
        top.setForeground(1, OK_COLOR if msg.valid else ERROR_COLOR)
        font = top.font(0)
        font.setBold(True)
        top.setFont(0, font)
        self.addTopLevelItem(top)
        for f in msg.fields:
            value = f.display
            if f.encoding == Encoding.CRC:
                value = f"{f.display} ({'VALID' if f.valid else 'INVALID'})"
            child = QTreeWidgetItem([f.name, value, f.hex, str(f.offset)])
            child.setFont(2, mono_font())
            if not f.valid:
                child.setForeground(1, ERROR_COLOR)
                child.setToolTip(1, f.error or "")
            elif f.error:
                child.setForeground(1, WARN_COLOR)
                child.setToolTip(1, f.error)
            top.addChild(child)
            for bit in f.bits:
                rng = f"bit {bit.start}" if bit.length == 1 else f"bits {bit.start}-{bit.start + bit.length - 1}"
                b = QTreeWidgetItem([bit.name, bit.display, str(bit.value), rng])
                if bit.display == "✓":
                    b.setForeground(1, OK_COLOR)
                elif bit.display == "✗":
                    b.setForeground(1, MUTED_COLOR)
                child.addChild(b)
        for err in msg.errors:
            e = QTreeWidgetItem(["error", err, "", ""])
            e.setForeground(1, ERROR_COLOR)
            top.addChild(e)
        for warn in msg.warnings:
            w = QTreeWidgetItem(["warning", warn, "", ""])
            w.setForeground(1, WARN_COLOR)
            top.addChild(w)
        return top


def confirm(parent: QWidget, title: str, text: str) -> bool:
    return (
        QMessageBox.question(parent, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        == QMessageBox.StandardButton.Yes
    )


def set_combo_items(combo, items: List[str], keep: bool = True) -> None:
    current = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(items)
    if keep and current in items:
        combo.setCurrentText(current)
    combo.blockSignals(False)


ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
