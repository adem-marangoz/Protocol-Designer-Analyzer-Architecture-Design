"""Packet Builder (Section 14): fill in a form, get the raw packet, send it."""

from __future__ import annotations

from typing import Dict

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ..application.packet_builder import FieldInput, PacketBuilder
from ..protocol.decoder import Decoder
from ..protocol.encoder import EncodedFrame
from ..protocol.errors import ProtocolError
from ..transport.base import TransportError
from .widgets import ERROR_COLOR, OK_COLOR, Page, hint_label, mono_font, set_combo_items


class BuilderPage(Page):
    key = "builder"
    title = "Packet Builder"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.layout_.addWidget(hint_label(
            "Choose a message and enter the field values. LENGTH and CRC are calculated automatically."
        ))
        top = QHBoxLayout()
        top.addWidget(QLabel("Message:"))
        self.selector = QComboBox()
        self.selector.setMinimumWidth(240)
        self.selector.currentTextChanged.connect(self._rebuild_form)
        top.addWidget(self.selector)
        top.addStretch(1)
        self.layout_.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.layout_.addWidget(split, 1)
        self.form_box = QGroupBox("Field values")
        self.form_host = QVBoxLayout(self.form_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_box)
        split.addWidget(scroll)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        out_box = QGroupBox("Raw packet")
        ov = QVBoxLayout(out_box)
        self.output = QLineEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font(11))
        ov.addWidget(self.output)
        self.status = QLabel()
        ov.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_build = QPushButton("BUILD")
        self.btn_build.setDefault(True)
        self.btn_copy = QPushButton("Copy hex")
        self.btn_send = QPushButton("Send")
        self.btn_send.setToolTip("Send on the connected transport (connect on the Live Monitor page)")
        self.repeat = QSpinBox()
        self.repeat.setRange(1, 1000)
        self.repeat.setPrefix("× ")
        row.addWidget(self.btn_build)
        row.addWidget(self.btn_copy)
        row.addStretch(1)
        row.addWidget(self.repeat)
        row.addWidget(self.btn_send)
        ov.addLayout(row)
        rv.addWidget(out_box)
        layout_box = QGroupBox("Layout")
        lv = QVBoxLayout(layout_box)
        self.layout_table = QTableWidget(0, 4)
        self.layout_table.setHorizontalHeaderLabels(["Field", "Offset", "Bytes", "Value"])
        self.layout_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.layout_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lv.addWidget(self.layout_table)
        rv.addWidget(layout_box, 1)
        split.addWidget(right)
        split.setSizes([420, 560])

        self.btn_build.clicked.connect(self.build)
        self.btn_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.output.text()))
        self.btn_send.clicked.connect(self.send)
        ctx.protocol_edited.connect(self._refresh_messages)
        ctx.connection_changed.connect(lambda *_: self._update_send())
        self.editors: Dict[str, object] = {}
        self.last: EncodedFrame = None
        self.on_protocol_changed()

    def on_protocol_changed(self) -> None:
        self._refresh_messages()

    def on_show(self, argument=None) -> None:
        self._refresh_messages()
        if argument and self.ctx.protocol.has_frame(argument):
            self.selector.setCurrentText(argument)
        self._update_send()

    def _refresh_messages(self) -> None:
        set_combo_items(self.selector, [f.name for f in self.ctx.protocol.frames])
        self._rebuild_form(self.selector.currentText())  # keeps the values already typed

    def _update_send(self) -> None:
        self.btn_send.setEnabled(self.ctx.connected)

    # --------------------------------------------------------------- form ---

    def _rebuild_form(self, name: str) -> None:
        old_values = self.values() if self.editors else {}
        while self.form_host.count():
            item = self.form_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # detach now; deleteLater alone waits for the event loop
                widget.deleteLater()
        self.editors = {}
        if not name or not self.ctx.protocol.has_frame(name):
            self.form_host.addWidget(QLabel("No message selected."))
            return
        frame = self.ctx.protocol.frame(name)
        builder = PacketBuilder(self.ctx.protocol)
        holder = QWidget()
        form = QFormLayout(holder)
        try:
            inputs = builder.inputs(frame)
        except ProtocolError as exc:
            self.form_host.addWidget(QLabel(f"Cannot build this message: {exc}"))
            return
        for fi in inputs:
            editor = self._editor_for(fi, old_values.get(fi.name))
            label = QLabel(fi.name)
            label.setToolTip(fi.description or fi.hint)
            form.addRow(label, editor)
        if not inputs:
            form.addRow(QLabel("This message has no user fields (all constant / computed)."))
        self.form_host.addWidget(holder)
        self.form_host.addStretch(1)

    def _editor_for(self, fi: FieldInput, previous):
        if fi.kind == "enum":
            w = QComboBox()
            w.setEditable(True)
            w.addItems(fi.options)
            w.setCurrentText(str(previous) if previous is not None else fi.default)
        elif fi.kind == "bool":
            w = QCheckBox(fi.hint)
            w.setChecked(bool(previous) if previous is not None else fi.default == "true")
        elif fi.kind == "bits":
            w = QGroupBox(fi.hint)
            grid = QFormLayout(w)
            sub = {}
            for bit in fi.bits:
                if bit["options"]:
                    e = QComboBox()
                    e.addItems(bit["options"])
                elif bit["length"] == 1:
                    e = QCheckBox()
                else:
                    e = QSpinBox()
                    e.setRange(0, (1 << bit["length"]) - 1)
                grid.addRow(bit["name"], e)
                sub[bit["name"]] = e
            if isinstance(previous, dict):
                for key, value in previous.items():
                    e = sub.get(key)
                    if isinstance(e, QCheckBox):
                        e.setChecked(bool(value))
                    elif isinstance(e, QComboBox):
                        e.setCurrentText(str(value))
                    elif isinstance(e, QSpinBox):
                        e.setValue(int(value))
            self.editors[fi.name] = ("bits", sub)
            return w
        else:
            w = QLineEdit(str(previous) if previous is not None else fi.default)
            w.setPlaceholderText(fi.hint)
            w.setToolTip(fi.hint)
            if fi.kind in ("bytes",):
                w.setFont(mono_font())
            w.returnPressed.connect(self.build)
        self.editors[fi.name] = (fi.kind, w)
        return w

    def values(self) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for name, (kind, w) in self.editors.items():
            if kind == "bits":
                bits = {}
                for bit_name, e in w.items():
                    if isinstance(e, QCheckBox):
                        bits[bit_name] = e.isChecked()
                    elif isinstance(e, QComboBox):
                        bits[bit_name] = e.currentText()
                    else:
                        bits[bit_name] = e.value()
                out[name] = bits
            elif kind == "bool":
                out[name] = w.isChecked()
            elif kind == "enum":
                out[name] = w.currentText()
            else:
                out[name] = w.text()
        return out

    def set_value(self, name: str, value) -> None:
        kind, w = self.editors[name]
        if kind == "bool":
            w.setChecked(bool(value))
        elif kind == "enum":
            w.setCurrentText(str(value))
        elif kind == "bits":
            for key, v in value.items():
                e = w[key]
                if isinstance(e, QCheckBox):
                    e.setChecked(bool(v))
                elif isinstance(e, QComboBox):
                    e.setCurrentText(str(v))
                else:
                    e.setValue(int(v))
        else:
            w.setText(str(value))

    # ------------------------------------------------------------- actions ---

    def build(self) -> bool:
        name = self.selector.currentText()
        if not name:
            return False
        try:
            encoded = PacketBuilder(self.ctx.protocol).build(name, self.values())
        except (ProtocolError, ValueError, KeyError) as exc:
            self.last = None
            self.output.setText("")
            self.status.setText(f"✗ {exc}")
            self.status.setStyleSheet(f"color: {ERROR_COLOR.name()}")
            self.layout_table.setRowCount(0)
            return False
        self.last = encoded
        self.output.setText(encoded.hex)
        self.status.setText(f"✓ {len(encoded.data)} bytes")
        self.status.setStyleSheet(f"color: {OK_COLOR.name()}")
        decoded = Decoder(self.ctx.protocol).decode(encoded.frame, encoded.data, check_constants=False)
        self.layout_table.setRowCount(len(decoded.fields))
        for r, f in enumerate(decoded.fields):
            value = f.display
            if f.bits:
                value += "  (" + ", ".join(f"{b.name}={b.display}" for b in f.bits) + ")"
            cells = [f.name, str(f.offset), f.hex, value]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 2:
                    item.setFont(mono_font())
                self.layout_table.setItem(r, c, item)
        return True

    def send(self) -> bool:
        if not self.build():
            return False
        session = self.ctx.session
        if session is None or not session.connected:
            self.error("Not connected. Open the Live Monitor page and press Connect.")
            return False
        frame = self.last.frame
        try:
            for _ in range(self.repeat.value()):
                session.send_raw(self.last.data, frame.can_id, frame.can_extended)
        except TransportError as exc:
            self.error(str(exc))
            return False
        self.ctx.status_message.emit(f"Sent {frame.name}: {self.last.hex}")
        return True
