"""Live Monitor (Section 18): connection, traffic table, decoded details."""

from __future__ import annotations

import threading
from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..application.logger import ERROR, INFO, RX, TX, TrafficEntry
from ..protocol.values import parse_hex_bytes, parse_int
from ..transport.base import TransportError
from .widgets import ERROR_COLOR, MUTED_COLOR, OK_COLOR, WARN_COLOR, DecodedTree, Page, mono_font, set_combo_items

COLUMNS = ["Time", "Dir", "CAN ID", "Raw Data", "Message", "Status"]


class TrafficModel(QAbstractTableModel):
    def __init__(self, max_rows: int = 5000, parent=None):
        super().__init__(parent)
        self.rows: List[TrafficEntry] = []
        self.max_rows = max_rows

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return e.time_text
            if col == 1:
                return e.direction
            if col == 2:
                return "" if e.can_id is None else f"0x{e.can_id:X}"
            if col == 3:
                return e.hex if e.data else e.note
            if col == 4:
                return e.name
            if col == 5:
                return e.status
        if role == Qt.ItemDataRole.ForegroundRole:
            if e.direction == ERROR:
                return ERROR_COLOR
            if e.direction == INFO:
                return MUTED_COLOR
            if col == 5:
                return {"OK": OK_COLOR, "WARN": WARN_COLOR}.get(e.status, ERROR_COLOR)
            if col == 1:
                return OK_COLOR if e.direction == TX else None
        if role == Qt.ItemDataRole.FontRole and col == 3 and e.data:
            return mono_font()
        if role == Qt.ItemDataRole.ToolTipRole and e.note:
            return e.note
        return None

    def append(self, entry: TrafficEntry) -> None:
        if len(self.rows) >= self.max_rows:
            drop = len(self.rows) - self.max_rows + 1
            self.beginRemoveRows(QModelIndex(), 0, drop - 1)
            del self.rows[:drop]
            self.endRemoveRows()
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append(entry)
        self.endInsertRows()

    def reset(self, entries: List[TrafficEntry]) -> None:
        self.beginResetModel()
        self.rows = list(entries[-self.max_rows:])
        self.endResetModel()


class MonitorPage(Page):
    key = "monitor"
    title = "Live Monitor"
    _connect_done = Signal(str)  # error text, empty on success

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        header = QHBoxLayout()
        self.conn_label = QLabel()
        self.conn_label.setFont(mono_font())
        self.state_label = QLabel("Disconnected")
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setMinimumWidth(110)
        header.addWidget(self.conn_label, 1)
        header.addWidget(self.state_label)
        header.addWidget(self.btn_connect)
        self.layout_.addLayout(header)

        filters = QHBoxLayout()
        self.show_tx = QCheckBox("TX")
        self.show_rx = QCheckBox("RX")
        self.show_info = QCheckBox("Events")
        self.show_garbage = QCheckBox("Unknown bytes")
        for cb in (self.show_tx, self.show_rx, self.show_info, self.show_garbage):
            cb.setChecked(True)
            cb.toggled.connect(self._reload_rows)
            filters.addWidget(cb)
        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)
        self.pause = QCheckBox("Pause display")
        filters.addWidget(self.autoscroll)
        filters.addWidget(self.pause)
        filters.addStretch(1)
        self.btn_clear = QPushButton("Clear")
        self.btn_export = QPushButton("Export…")
        filters.addWidget(self.btn_clear)
        filters.addWidget(self.btn_export)
        self.layout_.addLayout(filters)

        split = QSplitter(Qt.Orientation.Vertical)
        self.model = TrafficModel(ctx.settings.monitor_max_rows)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(4, 150)
        self.table.selectionModel().currentRowChanged.connect(self._show_selected)
        split.addWidget(self.table)
        detail = QWidget()
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.addWidget(QLabel("Decoded"))
        self.tree = DecodedTree()
        dv.addWidget(self.tree)
        split.addWidget(detail)
        split.setSizes([380, 240])
        self.layout_.addWidget(split, 1)

        send = QHBoxLayout()
        send.addWidget(QLabel("Quick send:"))
        self.send_msg = QComboBox()
        self.send_msg.setMinimumWidth(180)
        self.btn_send_msg = QPushButton("Send (defaults)")
        self.btn_open_builder = QPushButton("Edit in builder…")
        send.addWidget(self.send_msg)
        send.addWidget(self.btn_send_msg)
        send.addWidget(self.btn_open_builder)
        send.addSpacing(20)
        send.addWidget(QLabel("Raw hex:"))
        self.raw = QLineEdit()
        self.raw.setFont(mono_font())
        self.raw.setPlaceholderText("AA 01 10 01 05 94")
        self.raw_can = QLineEdit()
        self.raw_can.setPlaceholderText("CAN id")
        self.raw_can.setMaximumWidth(90)
        self.btn_send_raw = QPushButton("Send raw")
        send.addWidget(self.raw, 1)
        send.addWidget(self.raw_can)
        send.addWidget(self.btn_send_raw)
        self.layout_.addLayout(send)

        self.btn_connect.clicked.connect(self.toggle_connection)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_export.clicked.connect(self._export)
        self.btn_send_msg.clicked.connect(self.send_selected_message)
        self.btn_open_builder.clicked.connect(lambda: ctx.navigate.emit("builder", self.send_msg.currentText()))
        self.btn_send_raw.clicked.connect(self.send_raw)
        self.raw.returnPressed.connect(self.send_raw)
        ctx.traffic_entry.connect(self._on_entry)
        ctx.connection_changed.connect(self._on_connection)
        ctx.protocol_edited.connect(self._refresh_header)
        self._connect_done.connect(self._after_connect)
        self._connecting = False
        self.on_protocol_changed()

    # ------------------------------------------------------------ state ---

    def on_protocol_changed(self) -> None:
        self._refresh_header()
        self._update_buttons()

    def on_show(self, argument=None) -> None:
        self._refresh_header()

    def _refresh_header(self) -> None:
        self.conn_label.setText(self.ctx.protocol.transport.summary())
        set_combo_items(self.send_msg, [f.name for f in self.ctx.protocol.frames])

    def _update_buttons(self) -> None:
        connected = self.ctx.connected
        self.btn_connect.setText("Disconnect" if connected else ("Connecting…" if self._connecting else "Connect"))
        self.btn_connect.setEnabled(not self._connecting)
        for b in (self.btn_send_msg, self.btn_send_raw):
            b.setEnabled(connected)
        self.state_label.setText("[Connected]" if connected else "[Disconnected]")
        self.state_label.setStyleSheet(f"color: {(OK_COLOR if connected else MUTED_COLOR).name()}; font-weight: bold")

    def _on_connection(self, connected: bool, message: str) -> None:
        self._update_buttons()
        if not connected and message.startswith("Connection lost"):
            self.ctx.status_message.emit(message)

    # ------------------------------------------------------- connection ---

    def toggle_connection(self) -> None:
        if self.ctx.connected:
            self.ctx.disconnect_session()
            self._update_buttons()
            return
        self._connecting = True
        self._update_buttons()

        def worker():
            try:
                self.ctx.connect_session()
                self._connect_done.emit("")
            except (TransportError, Exception) as exc:  # noqa: BLE001 - shown to the user
                self._connect_done.emit(str(exc) or exc.__class__.__name__)

        threading.Thread(target=worker, name="connect", daemon=True).start()

    def _after_connect(self, error: str) -> None:
        self._connecting = False
        self._update_buttons()
        if error:
            self.error(f"Cannot connect:\n{error}", "Connection failed")

    # ----------------------------------------------------------- traffic ---

    def _accept(self, e: TrafficEntry) -> bool:
        if e.direction == TX:
            return self.show_tx.isChecked()
        if e.direction == RX:
            if e.message is None and not self.show_garbage.isChecked():
                return False
            return self.show_rx.isChecked()
        return self.show_info.isChecked()

    def _on_entry(self, entry: TrafficEntry) -> None:
        if self.pause.isChecked() or not self._accept(entry):
            return
        self.model.append(entry)
        if self.autoscroll.isChecked():
            self.table.scrollToBottom()

    def _reload_rows(self) -> None:
        self.model.reset([e for e in self.ctx.traffic.entries if self._accept(e)])

    def clear(self) -> None:
        self.ctx.traffic.clear()
        self.model.reset([])
        self.tree.clear()

    def _show_selected(self, current, _previous=None) -> None:
        row = current.row() if current is not None and current.isValid() else -1
        if row < 0 or row >= len(self.model.rows):
            self.tree.clear()
            return
        e = self.model.rows[row]
        if e.message is not None:
            self.tree.show_messages([e.message])
        elif e.data:
            self.tree.show_messages([], [e.data])
        else:
            self.tree.clear()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export traffic", "traffic.csv", "CSV (*.csv);;Text (*.txt)")
        if path:
            self.ctx.traffic.save(path)
            self.ctx.status_message.emit(f"Traffic exported to {path}")

    # -------------------------------------------------------------- send ---

    def send_selected_message(self) -> bool:
        name = self.send_msg.currentText()
        if not name or self.ctx.session is None:
            return False
        try:
            self.ctx.session.send_message(name, {})
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.error(str(exc))
            return False
        return True

    def send_raw(self) -> bool:
        if self.ctx.session is None or not self.ctx.connected:
            return False
        try:
            data = parse_hex_bytes(self.raw.text())
            can_id: Optional[int] = parse_int(self.raw_can.text()) if self.raw_can.text().strip() else None
            if not data:
                return False
            self.ctx.session.send_raw(data, can_id)
        except (ValueError, TransportError) as exc:
            self.error(str(exc))
            return False
        return True
