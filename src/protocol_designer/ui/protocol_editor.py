"""Protocol Designer (Section 16): protocol, transport, enumerations, messages."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..protocol.model import Endianness, EnumDefinition, TransportType
from ..protocol.values import parse_int
from ..storage.json_loader import parse_simulator_rule
from ..storage.json_writer import rule_to_dict
from ..transport import list_can_interfaces, list_serial_ports
from .widgets import Page, confirm, hint_label, mono_font

BAUD_RATES = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600", "1000000"]


class ProtocolPage(Page):
    key = "protocols"
    title = "Protocol Designer"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._loading = False
        tabs = QTabWidget()
        self.tabs = tabs
        self.layout_.addWidget(tabs, 1)
        tabs.addTab(self._build_general(), "Protocol && Messages")
        tabs.addTab(self._build_transport(), "Transport")
        tabs.addTab(self._build_enums(), "Enumerations")
        tabs.addTab(self._build_simulator(), "Device Simulator")
        ctx.protocol_edited.connect(self._refresh_messages)
        self.load()

    # ---------------------------------------------------------- general tab ---

    def _build_general(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        self.name = QLineEdit()
        self.version = QLineEdit()
        self.description = QLineEdit()
        self.endianness = QComboBox()
        self.endianness.addItems([e.value for e in Endianness])
        form.addRow("Name", self.name)
        form.addRow("Version", self.version)
        form.addRow("Description", self.description)
        form.addRow("Default byte order", self.endianness)
        v.addLayout(form)
        for widget in (self.name, self.version, self.description):
            widget.editingFinished.connect(self._apply_general)
        self.endianness.currentTextChanged.connect(self._apply_general)

        box = QGroupBox("Messages")
        bv = QVBoxLayout(box)
        self.messages = QTableWidget(0, 5)
        self.messages.setHorizontalHeaderLabels(["Message", "ID", "Direction", "CAN ID", "Expected response"])
        self.messages.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.messages.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.messages.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.messages.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.messages.verticalHeader().setDefaultSectionSize(24)
        self.messages.cellDoubleClicked.connect(self._open_selected)
        bv.addWidget(self.messages)
        row = QHBoxLayout()
        self.btn_add = QPushButton("+ Add Message")
        self.btn_dup = QPushButton("Duplicate")
        self.btn_del = QPushButton("Remove")
        self.btn_up = QPushButton("Move up")
        self.btn_down = QPushButton("Move down")
        self.btn_edit = QPushButton("Edit fields…")
        for b in (self.btn_add, self.btn_dup, self.btn_del, self.btn_up, self.btn_down):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_edit)
        bv.addLayout(row)
        v.addWidget(box, 1)
        self.btn_add.clicked.connect(self._add_message)
        self.btn_dup.clicked.connect(self._duplicate_message)
        self.btn_del.clicked.connect(self._remove_message)
        self.btn_up.clicked.connect(lambda: self._move_message(-1))
        self.btn_down.clicked.connect(lambda: self._move_message(1))
        self.btn_edit.clicked.connect(self._open_selected)
        return w

    def _apply_general(self) -> None:
        if self._loading:
            return
        p = self.ctx.protocol
        new = (self.name.text().strip() or p.name, self.version.text().strip() or p.version,
               self.description.text(), Endianness(self.endianness.currentText()))
        if new != (p.name, p.version, p.description, p.endianness):
            p.name, p.version, p.description, p.endianness = new
            self.ctx.mark_modified()

    def selected_message(self) -> str:
        row = self.messages.currentRow()
        if row < 0:
            return ""
        return self.messages.item(row, 0).text()

    def _refresh_messages(self) -> None:
        current = self.selected_message()
        p = self.ctx.protocol
        self.messages.setRowCount(len(p.frames))
        for r, f in enumerate(p.frames):
            cells = [
                f.name,
                "" if f.id is None else f"0x{f.id:02X}",
                f.direction.value,
                "" if f.can_id is None else f"0x{f.can_id:X}",
                f.expected_response or "",
            ]
            for c, text in enumerate(cells):
                self.messages.setItem(r, c, QTableWidgetItem(text))
            if f.name == current:
                self.messages.selectRow(r)

    def _add_message(self) -> None:
        name, ok = QInputDialog.getText(self, "Add message", "Message name:", text=self.ctx.manager.unique_frame_name())
        if not ok or not name.strip():
            return
        name = name.strip().upper().replace(" ", "_")
        if self.ctx.protocol.has_frame(name):
            self.error(f"A message named {name} already exists.")
            return
        self.ctx.manager.add_frame(name)
        self._select(name)

    def _duplicate_message(self) -> None:
        name = self.selected_message()
        if name:
            self._select(self.ctx.manager.duplicate_frame(name).name)

    def _remove_message(self) -> None:
        name = self.selected_message()
        if name and confirm(self, "Remove message", f"Remove message {name}?"):
            self.ctx.manager.remove_frame(name)

    def _move_message(self, delta: int) -> None:
        name = self.selected_message()
        if name:
            self.ctx.manager.move_frame(name, delta)
            self._select(name)

    def _select(self, name: str) -> None:
        for r in range(self.messages.rowCount()):
            if self.messages.item(r, 0).text() == name:
                self.messages.selectRow(r)

    def _open_selected(self, *_):
        name = self.selected_message()
        if name:
            self.ctx.navigate.emit("messages", name)

    # -------------------------------------------------------- transport tab ---

    def _build_transport(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(hint_label(
            "The transport only moves bytes; the same protocol can run over RS485, UART, TCP or CAN. "
            "Choose SIMULATOR to try everything without hardware (the device answers using the rules on the "
            "'Device Simulator' tab)."
        ))
        grid = QGridLayout()
        v.addLayout(grid)
        self.t_type = QComboBox()
        self.t_type.addItems([t.value for t in TransportType])
        grid.addWidget(QLabel("Transport type"), 0, 0)
        grid.addWidget(self.t_type, 0, 1)

        self.serial_box = QGroupBox("Serial (UART / RS485)")
        sf = QFormLayout(self.serial_box)
        port_row = QHBoxLayout()
        self.t_port = QComboBox()
        self.t_port.setEditable(True)
        self.t_refresh = QPushButton("Refresh")
        self.t_refresh.clicked.connect(self._refresh_ports)
        port_row.addWidget(self.t_port, 1)
        port_row.addWidget(self.t_refresh)
        self.t_baud = QComboBox()
        self.t_baud.setEditable(True)
        self.t_baud.addItems(BAUD_RATES)
        self.t_bits = QComboBox()
        self.t_bits.addItems(["8", "7", "6", "5"])
        self.t_parity = QComboBox()
        self.t_parity.addItems(["NONE", "EVEN", "ODD", "MARK", "SPACE"])
        self.t_stop = QComboBox()
        self.t_stop.addItems(["1", "1.5", "2"])
        sf.addRow("Port", port_row)
        sf.addRow("Baud rate", self.t_baud)
        sf.addRow("Data bits", self.t_bits)
        sf.addRow("Parity", self.t_parity)
        sf.addRow("Stop bits", self.t_stop)

        self.net_box = QGroupBox("Network (TCP / UDP)")
        nf = QFormLayout(self.net_box)
        self.t_host = QLineEdit()
        self.t_tcp_port = QSpinBox()
        self.t_tcp_port.setRange(0, 65535)
        nf.addRow("Host", self.t_host)
        nf.addRow("Port", self.t_tcp_port)

        self.can_box = QGroupBox("CAN / CAN-FD (python-can)")
        cf = QFormLayout(self.can_box)
        self.t_can_if = QComboBox()
        self.t_can_if.setEditable(True)
        self.t_can_if.addItems(list_can_interfaces())
        self.t_can_ch = QLineEdit()
        self.t_can_rate = QComboBox()
        self.t_can_rate.setEditable(True)
        self.t_can_rate.addItems(["125000", "250000", "500000", "1000000"])
        self.t_can_drate = QComboBox()
        self.t_can_drate.setEditable(True)
        self.t_can_drate.addItems(["1000000", "2000000", "4000000", "5000000", "8000000"])
        cf.addRow("Interface", self.t_can_if)
        cf.addRow("Channel", self.t_can_ch)
        cf.addRow("Bit rate", self.t_can_rate)
        cf.addRow("Data bit rate (FD)", self.t_can_drate)

        self.t_timeout = QSpinBox()
        self.t_timeout.setRange(1, 60000)
        self.t_timeout.setSuffix(" ms")
        self.t_options = QLineEdit()
        self.t_options.setPlaceholderText('e.g. {"local_echo": true, "rts_toggle": true}')
        grid.addWidget(self.serial_box, 1, 0, 1, 2)
        grid.addWidget(self.net_box, 2, 0, 1, 2)
        grid.addWidget(self.can_box, 3, 0, 1, 2)
        grid.addWidget(QLabel("Timeout"), 4, 0)
        grid.addWidget(self.t_timeout, 4, 1)
        grid.addWidget(QLabel("Options (JSON)"), 5, 0)
        grid.addWidget(self.t_options, 5, 1)
        v.addStretch(1)

        self.t_type.currentTextChanged.connect(self._transport_type_changed)
        for combo in (self.t_port, self.t_baud, self.t_can_if, self.t_can_rate, self.t_can_drate):
            combo.lineEdit().editingFinished.connect(self._apply_transport)
        for combo in (self.t_port, self.t_baud, self.t_bits, self.t_parity, self.t_stop, self.t_can_if,
                      self.t_can_rate, self.t_can_drate):
            combo.activated.connect(self._apply_transport)
        for edit in (self.t_host, self.t_can_ch, self.t_options):
            edit.editingFinished.connect(self._apply_transport)
        for spin in (self.t_tcp_port, self.t_timeout):
            spin.editingFinished.connect(self._apply_transport)
        return w

    def _refresh_ports(self) -> None:
        current = self.t_port.currentText()
        self.t_port.clear()
        self.t_port.addItems(list_serial_ports() + ["loop://"])
        self.t_port.setCurrentText(current)

    def _transport_type_changed(self, text: str) -> None:
        t = TransportType(text)
        self.serial_box.setEnabled(t in (TransportType.UART, TransportType.RS485))
        self.net_box.setEnabled(t in (TransportType.TCP, TransportType.UDP))
        self.can_box.setEnabled(t in (TransportType.CAN, TransportType.CANFD))
        self._apply_transport()

    def _apply_transport(self, *_) -> None:
        if self._loading:
            return
        t = self.ctx.protocol.transport
        before = (t.type, t.port, t.baudrate, t.data_bits, t.parity, t.stop_bits, t.host, t.tcp_port,
                  t.can_interface, t.can_channel, t.can_bitrate, t.can_data_bitrate, t.timeout_ms, dict(t.options))
        try:
            t.type = TransportType(self.t_type.currentText())
            t.port = self.t_port.currentText().strip()
            t.baudrate = parse_int(self.t_baud.currentText())
            t.data_bits = int(self.t_bits.currentText())
            t.parity = self.t_parity.currentText()
            stop = float(self.t_stop.currentText())
            t.stop_bits = int(stop) if stop.is_integer() else stop
            t.host = self.t_host.text().strip()
            t.tcp_port = self.t_tcp_port.value()
            t.can_interface = self.t_can_if.currentText().strip()
            t.can_channel = self.t_can_ch.text().strip()
            t.can_bitrate = parse_int(self.t_can_rate.currentText())
            t.can_data_bitrate = parse_int(self.t_can_drate.currentText())
            t.timeout_ms = self.t_timeout.value()
            text = self.t_options.text().strip()
            t.options = json.loads(text) if text else {}
            if not isinstance(t.options, dict):
                raise ValueError("options must be a JSON object")
        except ValueError as exc:
            self.error(f"Invalid transport setting: {exc}")
            self._load_transport()
            return
        after = (t.type, t.port, t.baudrate, t.data_bits, t.parity, t.stop_bits, t.host, t.tcp_port,
                 t.can_interface, t.can_channel, t.can_bitrate, t.can_data_bitrate, t.timeout_ms, dict(t.options))
        if after != before:
            self.ctx.mark_modified()

    def _load_transport(self) -> None:
        t = self.ctx.protocol.transport
        was = self._loading
        self._loading = True
        self.t_type.setCurrentText(t.type.value)
        self._refresh_ports()
        self.t_port.setCurrentText(t.port)
        self.t_baud.setCurrentText(str(t.baudrate))
        self.t_bits.setCurrentText(str(t.data_bits))
        self.t_parity.setCurrentText(str(t.parity).upper())
        self.t_stop.setCurrentText(str(t.stop_bits))
        self.t_host.setText(t.host)
        self.t_tcp_port.setValue(t.tcp_port)
        self.t_can_if.setCurrentText(t.can_interface)
        self.t_can_ch.setText(t.can_channel)
        self.t_can_rate.setCurrentText(str(t.can_bitrate))
        self.t_can_drate.setCurrentText(str(t.can_data_bitrate))
        self.t_timeout.setValue(t.timeout_ms)
        self.t_options.setText(json.dumps(t.options) if t.options else "")
        self._loading = was
        self._transport_type_changed_silent(t.type)

    def _transport_type_changed_silent(self, t: TransportType) -> None:
        self.serial_box.setEnabled(t in (TransportType.UART, TransportType.RS485))
        self.net_box.setEnabled(t in (TransportType.TCP, TransportType.UDP))
        self.can_box.setEnabled(t in (TransportType.CAN, TransportType.CANFD))

    # ------------------------------------------------------------ enums tab ---

    def _build_enums(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        h.addWidget(splitter)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Enumerations"))
        self.enum_list = QListWidget()
        lv.addWidget(self.enum_list, 1)
        row = QHBoxLayout()
        self.enum_add = QPushButton("+ Add")
        self.enum_del = QPushButton("Remove")
        row.addWidget(self.enum_add)
        row.addWidget(self.enum_del)
        lv.addLayout(row)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("Values (e.g. 0x02 = ERROR). Displayed instead of the number (Section 11)."))
        self.enum_table = QTableWidget(0, 2)
        self.enum_table.setHorizontalHeaderLabels(["Value", "Name"])
        self.enum_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rv.addWidget(self.enum_table, 1)
        row = QHBoxLayout()
        self.enum_value_add = QPushButton("+ Add value")
        self.enum_value_del = QPushButton("Remove value")
        self.enum_apply = QPushButton("Apply")
        row.addWidget(self.enum_value_add)
        row.addWidget(self.enum_value_del)
        row.addStretch(1)
        row.addWidget(self.enum_apply)
        rv.addLayout(row)
        splitter.addWidget(right)
        splitter.setSizes([200, 500])

        self.enum_list.currentTextChanged.connect(self._show_enum)
        self.enum_add.clicked.connect(self._add_enum)
        self.enum_del.clicked.connect(self._remove_enum)
        self.enum_value_add.clicked.connect(self._add_enum_value)
        self.enum_value_del.clicked.connect(lambda: self.enum_table.removeRow(self.enum_table.currentRow()))
        self.enum_apply.clicked.connect(self.apply_enum)
        return w

    def _load_enums(self) -> None:
        current = self.enum_list.currentItem().text() if self.enum_list.currentItem() else ""
        self.enum_list.clear()
        self.enum_list.addItems(list(self.ctx.protocol.enums))
        items = self.enum_list.findItems(current, Qt.MatchFlag.MatchExactly) if current else []
        if items:
            self.enum_list.setCurrentItem(items[0])
        elif self.enum_list.count():
            self.enum_list.setCurrentRow(0)
        else:
            self.enum_table.setRowCount(0)

    def _show_enum(self, name: str) -> None:
        enum = self.ctx.protocol.enums.get(name)
        self.enum_table.setRowCount(0)
        if enum is None:
            return
        for value, label in sorted(enum.values.items()):
            r = self.enum_table.rowCount()
            self.enum_table.insertRow(r)
            self.enum_table.setItem(r, 0, QTableWidgetItem(f"0x{value:02X}"))
            self.enum_table.setItem(r, 1, QTableWidgetItem(label))

    def _add_enum(self) -> None:
        name, ok = QInputDialog.getText(self, "Add enumeration", "Enumeration name:")
        name = name.strip().upper().replace(" ", "_")
        if not ok or not name:
            return
        if name in self.ctx.protocol.enums:
            self.error(f"Enumeration {name} already exists.")
            return
        self.ctx.protocol.enums[name] = EnumDefinition(name, {0: "OFF", 1: "ON"})
        self.ctx.mark_modified()
        self._load_enums()
        self.enum_list.setCurrentItem(self.enum_list.findItems(name, Qt.MatchFlag.MatchExactly)[0])

    def _remove_enum(self) -> None:
        item = self.enum_list.currentItem()
        if item and confirm(self, "Remove enumeration", f"Remove enumeration {item.text()}?"):
            del self.ctx.protocol.enums[item.text()]
            self.ctx.mark_modified()
            self._load_enums()

    def _add_enum_value(self) -> None:
        used = []
        for r in range(self.enum_table.rowCount()):
            try:
                used.append(parse_int(self.enum_table.item(r, 0).text()))
            except (ValueError, AttributeError):
                pass
        nxt = max(used) + 1 if used else 0
        r = self.enum_table.rowCount()
        self.enum_table.insertRow(r)
        self.enum_table.setItem(r, 0, QTableWidgetItem(f"0x{nxt:02X}"))
        self.enum_table.setItem(r, 1, QTableWidgetItem(f"VALUE_{nxt}"))

    def apply_enum(self) -> bool:
        item = self.enum_list.currentItem()
        if item is None:
            return False
        values = {}
        for r in range(self.enum_table.rowCount()):
            v_item, n_item = self.enum_table.item(r, 0), self.enum_table.item(r, 1)
            try:
                value = parse_int(v_item.text() if v_item else "")
            except ValueError:
                self.error(f"Row {r + 1}: invalid value")
                return False
            label = (n_item.text() if n_item else "").strip()
            if not label:
                self.error(f"Row {r + 1}: name is empty")
                return False
            if value in values:
                self.error(f"Value {value} is used twice")
                return False
            values[value] = label
        self.ctx.protocol.enums[item.text()].values = values
        self.ctx.mark_modified()
        return True

    # -------------------------------------------------------- simulator tab ---

    def _build_simulator(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(hint_label(
            "With the SIMULATOR transport a virtual device answers your requests. Each rule says: when the "
            "device receives message 'on' (optionally only if the fields in 'match' have these values), it "
            "replies with message 'reply' using 'values', copying the fields listed in 'copy' from the request."
        ))
        self.sim_edit = QPlainTextEdit()
        self.sim_edit.setFont(mono_font())
        v.addWidget(self.sim_edit, 1)
        self.sim_apply = QPushButton("Apply rules")
        self.sim_apply.clicked.connect(self.apply_simulator)
        v.addLayout(_right(self.sim_apply))
        return w

    def _load_simulator(self) -> None:
        rules = [rule_to_dict(r) for r in self.ctx.protocol.simulator]
        self.sim_edit.setPlainText(json.dumps(rules, indent=2, ensure_ascii=False))

    def apply_simulator(self) -> bool:
        try:
            data = json.loads(self.sim_edit.toPlainText() or "[]")
            if not isinstance(data, list):
                raise ValueError("the rules must be a JSON list")
            rules = [parse_simulator_rule(item) for item in data]
        except (ValueError, AttributeError) as exc:
            self.error(f"Invalid simulator rules: {exc}")
            return False
        self.ctx.protocol.simulator = rules
        self.ctx.mark_modified()
        return True

    # ---------------------------------------------------------------- load ---

    def load(self) -> None:
        self._loading = True
        p = self.ctx.protocol
        self.name.setText(p.name)
        self.version.setText(p.version)
        self.description.setText(p.description)
        self.endianness.setCurrentText(p.endianness.value)
        self._loading = False
        self._refresh_messages()
        self._load_transport()
        self._load_enums()
        self._load_simulator()

    def on_protocol_changed(self) -> None:
        self.load()


def _right(widget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(widget)
    return row

