"""Message Designer (Section 17): fields of one message and their properties."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..protocol import crc as crc_mod
from ..protocol.encoder import Encoder
from ..protocol.errors import DefinitionError, ProtocolError
from ..protocol.fields import fixed_size
from ..protocol.model import (
    BitDefinition,
    Direction,
    Encoding,
    Endianness,
    FieldDefinition,
    FieldType,
    FrameDefinition,
)
from ..protocol.validator import ERROR, validate_protocol
from ..protocol.values import parse_int, parse_number
from .widgets import ERROR_COLOR, WARN_COLOR, Page, confirm, mono_font, set_combo_items

DEFAULT = "(default)"
NONE = "(none)"


def _opt_float(text: str) -> Optional[float]:
    text = text.strip()
    return None if not text else float(parse_number(text))


def _num_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def field_details(f: FieldDefinition) -> str:
    parts = []
    if f.encoding == Encoding.CONSTANT:
        parts.append(f"= {f.value}")
    if f.encoding == Encoding.CRC:
        parts.append(str(f.crc if isinstance(f.crc, str) else "custom CRC"))
        if f.crc_from or f.crc_to:
            parts.append(f"over {f.crc_from or 'start'}..{f.crc_to or 'previous'}")
    if f.encoding == Encoding.LENGTH:
        parts.append(f"counts {f.length_from or 'next'}..{f.length_to or 'before CRC'}")
        if f.length_adjust:
            parts.append(f"{f.length_adjust:+d}")
    if f.is_scaled:
        parts.append(f"Scale {f.scale:g}" + (f" Offset {f.value_offset:g}" if f.value_offset else ""))
    if f.unit:
        parts.append(f.unit)
    if f.enum is not None:
        parts.append(f"enum {f.enum if isinstance(f.enum, str) else '(inline)'}")
    if f.bits:
        parts.append(f"{len(f.bits)} bit group(s)")
    if f.endianness is not None:
        parts.append(f.endianness.value.lower() + " endian")
    return ", ".join(parts)


class MessagePage(Page):
    key = "messages"
    title = "Message Designer"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._loading = False
        top = QHBoxLayout()
        top.addWidget(QLabel("Message:"))
        self.selector = QComboBox()
        self.selector.setMinimumWidth(240)
        self.selector.currentTextChanged.connect(self._select_message)
        top.addWidget(self.selector)
        top.addStretch(1)
        self.layout_.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.layout_.addWidget(split, 1)

        # ---- left: message properties + field table + preview
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        props = QGroupBox("Message")
        pf = QFormLayout(props)
        self.m_name = QLineEdit()
        self.m_id = QLineEdit()
        self.m_dir = QComboBox()
        self.m_dir.addItems([d.value for d in Direction])
        self.m_can = QLineEdit()
        self.m_can.setPlaceholderText("only for CAN (e.g. 0x301)")
        self.m_ext = QCheckBox("29-bit extended CAN id")
        self.m_resp = QComboBox()
        self.m_desc = QLineEdit()
        pf.addRow("Name", self.m_name)
        pf.addRow("Command / ID", self.m_id)
        pf.addRow("Direction", self.m_dir)
        pf.addRow("CAN ID", self.m_can)
        pf.addRow("", self.m_ext)
        pf.addRow("Expected response", self.m_resp)
        pf.addRow("Description", self.m_desc)
        self.m_apply = QPushButton("Apply message properties")
        self.m_apply.clicked.connect(self.apply_message)
        pf.addRow("", self.m_apply)
        lv.addWidget(props)

        fields_box = QGroupBox("Fields")
        fv = QVBoxLayout(fields_box)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Offset", "Size", "Type", "Encoding", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.currentCellChanged.connect(lambda *_: self._load_field())
        fv.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.f_add = QPushButton("+ Add Field")
        self.f_del = QPushButton("Remove")
        self.f_up = QPushButton("Up")
        self.f_down = QPushButton("Down")
        for b in (self.f_add, self.f_del, self.f_up, self.f_down):
            row.addWidget(b)
        row.addStretch(1)
        fv.addLayout(row)
        self.f_add.clicked.connect(self._add_field)
        self.f_del.clicked.connect(self._remove_field)
        self.f_up.clicked.connect(lambda: self._move_field(-1))
        self.f_down.clicked.connect(lambda: self._move_field(1))
        lv.addWidget(fields_box, 2)

        prev = QGroupBox("Example packet (default values) and checks")
        pv = QVBoxLayout(prev)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font())
        self.preview.setMaximumHeight(130)
        pv.addWidget(self.preview)
        lv.addWidget(prev)
        split.addWidget(left)

        # ---- right: field editor
        editor = QWidget()
        form = QFormLayout(editor)
        self.e_name = QLineEdit()
        self.e_type = QComboBox()
        self.e_type.addItems([t.value for t in FieldType])
        self.e_size = QSpinBox()
        self.e_size.setRange(0, 65535)
        self.e_size.setSpecialValueText("auto / variable")
        self.e_encoding = QComboBox()
        self.e_encoding.addItems([e.value for e in Encoding])
        self.e_value = QLineEdit()
        self.e_value.setPlaceholderText("e.g. 0xAA")
        self.e_endian = QComboBox()
        self.e_endian.addItems([DEFAULT] + [e.value for e in Endianness])
        self.e_crc = QComboBox()
        self.e_crc.setEditable(True)
        self.e_crc.addItems(sorted(crc_mod.ALGORITHMS))
        self.e_crc_from = QComboBox()
        self.e_crc_to = QComboBox()
        self.e_len_from = QComboBox()
        self.e_len_to = QComboBox()
        self.e_len_adj = QSpinBox()
        self.e_len_adj.setRange(-65535, 65535)
        self.e_scale = QLineEdit()
        self.e_offset = QLineEdit()
        self.e_unit = QLineEdit()
        self.e_min = QLineEdit()
        self.e_max = QLineEdit()
        self.e_enum = QComboBox()
        self.e_default = QLineEdit()
        self.e_desc = QLineEdit()
        form.addRow("Name", self.e_name)
        form.addRow("Type", self.e_type)
        form.addRow("Size (bytes)", self.e_size)
        form.addRow("Encoding", self.e_encoding)
        form.addRow("Constant value", self.e_value)
        form.addRow("Byte order", self.e_endian)
        form.addRow("CRC algorithm", self.e_crc)
        form.addRow("CRC from field", self.e_crc_from)
        form.addRow("CRC to field", self.e_crc_to)
        form.addRow("Length counts from", self.e_len_from)
        form.addRow("Length counts to", self.e_len_to)
        form.addRow("Length adjust", self.e_len_adj)
        form.addRow("Scale", self.e_scale)
        form.addRow("Value offset", self.e_offset)
        form.addRow("Unit", self.e_unit)
        form.addRow("Minimum", self.e_min)
        form.addRow("Maximum", self.e_max)
        form.addRow("Enumeration", self.e_enum)
        form.addRow("Default", self.e_default)
        form.addRow("Description", self.e_desc)
        self.bits = QTableWidget(0, 4)
        self.bits.setHorizontalHeaderLabels(["Bit group", "Start bit", "Bits", "Enum"])
        self.bits.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bits.setMinimumHeight(120)
        brow = QHBoxLayout()
        self.b_add = QPushButton("+ Bit group")
        self.b_del = QPushButton("Remove")
        brow.addWidget(self.b_add)
        brow.addWidget(self.b_del)
        brow.addStretch(1)
        self.b_add.clicked.connect(self._add_bit)
        self.b_del.clicked.connect(lambda: self.bits.removeRow(self.bits.currentRow()))
        self.bits_label = QLabel("Bit fields (BITFIELD type, Section 12)")
        form.addRow(self.bits_label)
        form.addRow(self.bits)
        form.addRow(brow)
        self.e_apply = QPushButton("Apply field")
        self.e_apply.setDefault(True)
        self.e_apply.clicked.connect(self.apply_field)
        form.addRow(self.e_apply)
        self.e_type.currentTextChanged.connect(self._update_enabled)
        self.e_encoding.currentTextChanged.connect(self._update_enabled)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        box = QGroupBox("Field properties")
        bl = QVBoxLayout(box)
        bl.addWidget(editor)
        scroll.setWidget(box)
        split.addWidget(scroll)
        split.setSizes([620, 420])

        ctx.protocol_edited.connect(self._refresh_after_edit)
        self.on_protocol_changed()

    # --------------------------------------------------------------- state ---

    @property
    def frame(self) -> Optional[FrameDefinition]:
        name = self.selector.currentText()
        return self.ctx.protocol.frame(name) if name and self.ctx.protocol.has_frame(name) else None

    def current_field(self) -> Optional[FieldDefinition]:
        frame = self.frame
        row = self.table.currentRow()
        if frame is None or row < 0 or row >= len(frame.fields):
            return None
        return frame.fields[row]

    def on_protocol_changed(self) -> None:
        set_combo_items(self.selector, [f.name for f in self.ctx.protocol.frames])
        self._select_message(self.selector.currentText())

    def on_show(self, argument=None) -> None:
        names = [f.name for f in self.ctx.protocol.frames]
        set_combo_items(self.selector, names)
        if argument and argument in names:
            self.selector.setCurrentText(argument)
        self._select_message(self.selector.currentText())

    def _refresh_after_edit(self) -> None:
        if self._loading:
            return
        names = [f.name for f in self.ctx.protocol.frames]
        set_combo_items(self.selector, names)
        self._select_message(self.selector.currentText(), keep_row=True)

    def _select_message(self, name: str, keep_row: bool = False) -> None:
        row = self.table.currentRow() if keep_row else 0
        self._load_message()
        self._load_table()
        if self.table.rowCount():
            self.table.selectRow(min(max(row, 0), self.table.rowCount() - 1))
        self._load_field()
        self._update_preview()

    # ------------------------------------------------------------ message ---

    def _load_message(self) -> None:
        frame = self.frame
        enabled = frame is not None
        for w in (self.m_name, self.m_id, self.m_dir, self.m_can, self.m_ext, self.m_resp, self.m_desc, self.m_apply,
                  self.f_add, self.f_del, self.f_up, self.f_down):
            w.setEnabled(enabled)
        if frame is None:
            return
        self.m_name.setText(frame.name)
        self.m_id.setText("" if frame.id is None else f"0x{frame.id:02X}")
        self.m_dir.setCurrentText(frame.direction.value)
        self.m_can.setText("" if frame.can_id is None else f"0x{frame.can_id:X}")
        self.m_ext.setChecked(frame.can_extended)
        set_combo_items(self.m_resp, [NONE] + [f.name for f in self.ctx.protocol.frames if f.name != frame.name], keep=False)
        self.m_resp.setCurrentText(frame.expected_response or NONE)
        self.m_desc.setText(frame.description)

    def apply_message(self) -> bool:
        frame = self.frame
        if frame is None:
            return False
        try:
            new_id = parse_int(self.m_id.text()) if self.m_id.text().strip() else None
            can_id = parse_int(self.m_can.text()) if self.m_can.text().strip() else None
        except ValueError as exc:
            self.error(str(exc))
            return False
        new_name = self.m_name.text().strip().upper().replace(" ", "_")
        self._loading = True
        try:
            if new_name != frame.name:
                self.ctx.manager.rename_frame(frame.name, new_name)
            frame.id = new_id
            frame.direction = Direction(self.m_dir.currentText())
            frame.can_id = can_id
            frame.can_extended = self.m_ext.isChecked()
            resp = self.m_resp.currentText()
            frame.expected_response = None if resp == NONE else resp
            frame.description = self.m_desc.text()
        except ValueError as exc:
            self.error(str(exc))
            return False
        finally:
            self._loading = False
        self.ctx.mark_modified()
        self.selector.setCurrentText(frame.name)
        return True

    # ------------------------------------------------------------- table ---

    def _load_table(self) -> None:
        frame = self.frame
        fields = frame.fields if frame else []
        self.table.setRowCount(len(fields))
        offset: Optional[int] = 0
        for r, f in enumerate(fields):
            try:
                size = fixed_size(f)
                size_text = "var" if size is None else str(size)
            except DefinitionError:
                size, size_text = None, "?"
            cells = [f.name, "" if offset is None else str(offset), size_text, f.type.value,
                     "" if f.encoding == Encoding.VALUE else f.encoding.value, field_details(f)]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
            offset = offset + size if (offset is not None and size is not None) else None

    def _add_field(self) -> None:
        frame = self.frame
        if frame is None:
            return
        row = self.table.currentRow()
        index = row + 1 if row >= 0 else None
        if index is not None and index >= len(frame.fields):
            index = None
        f = self.ctx.manager.add_field(frame.name, index)
        self._select_field(f.name)

    def _remove_field(self) -> None:
        f = self.current_field()
        if f and confirm(self, "Remove field", f"Remove field {f.name}?"):
            self.ctx.manager.remove_field(self.frame.name, f.name)

    def _move_field(self, delta: int) -> None:
        f = self.current_field()
        if f:
            self.ctx.manager.move_field(self.frame.name, f.name, delta)
            self._select_field(f.name)

    def _select_field(self, name: str) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == name:
                self.table.selectRow(r)
                self._load_field()
                return

    # ------------------------------------------------------------ editor ---

    def _load_field(self) -> None:
        f = self.current_field()
        self.e_apply.setEnabled(f is not None)
        if f is None:
            return
        frame = self.frame
        names = [x.name for x in frame.fields]
        for combo in (self.e_crc_from, self.e_crc_to, self.e_len_from, self.e_len_to):
            set_combo_items(combo, [DEFAULT] + names, keep=False)
        set_combo_items(self.e_enum, [NONE] + list(self.ctx.protocol.enums), keep=False)
        self.e_name.setText(f.name)
        self.e_type.setCurrentText(f.type.value)
        self.e_size.setValue(f.size or 0)
        self.e_encoding.setCurrentText(f.encoding.value)
        self.e_value.setText("" if f.value is None else str(f.value))
        self.e_endian.setCurrentText(f.endianness.value if f.endianness else DEFAULT)
        self.e_crc.setCurrentText(f.crc if isinstance(f.crc, str) else "CRC8")
        self.e_crc_from.setCurrentText(f.crc_from or DEFAULT)
        self.e_crc_to.setCurrentText(f.crc_to or DEFAULT)
        self.e_len_from.setCurrentText(f.length_from or DEFAULT)
        self.e_len_to.setCurrentText(f.length_to or DEFAULT)
        self.e_len_adj.setValue(f.length_adjust)
        self.e_scale.setText(_num_text(f.scale))
        self.e_offset.setText(_num_text(f.value_offset))
        self.e_unit.setText(f.unit)
        self.e_min.setText(_num_text(f.minimum))
        self.e_max.setText(_num_text(f.maximum))
        if isinstance(f.enum, str):
            self.e_enum.setCurrentText(f.enum)
        elif isinstance(f.enum, dict):
            self.e_enum.addItem("(inline)")
            self.e_enum.setCurrentText("(inline)")
        else:
            self.e_enum.setCurrentText(NONE)
        self.e_default.setText("" if f.default is None else str(f.default))
        self.e_desc.setText(f.description)
        self.bits.setRowCount(0)
        for b in f.bits:
            self._add_bit(b)
        self._update_enabled()

    def _add_bit(self, bit: Optional[BitDefinition] = None) -> None:
        if not isinstance(bit, BitDefinition):
            used = 0
            for r in range(self.bits.rowCount()):
                try:
                    used = max(used, int(self.bits.item(r, 1).text()) + int(self.bits.item(r, 2).text()))
                except (ValueError, AttributeError):
                    pass
            bit = BitDefinition(f"Bit {used}", used, 1)
        r = self.bits.rowCount()
        self.bits.insertRow(r)
        enum = bit.enum if isinstance(bit.enum, str) else ""
        for c, text in enumerate([bit.name, str(bit.start), str(bit.length), enum or ""]):
            self.bits.setItem(r, c, QTableWidgetItem(text))
        if isinstance(bit.enum, dict):
            self.bits.item(r, 3).setData(Qt.ItemDataRole.UserRole, bit.enum)
            self.bits.item(r, 3).setText("(inline)")

    def _update_enabled(self) -> None:
        enc = Encoding(self.e_encoding.currentText())
        ftype = FieldType(self.e_type.currentText())
        self.e_value.setEnabled(enc == Encoding.CONSTANT)
        for w in (self.e_crc, self.e_crc_from, self.e_crc_to):
            w.setEnabled(enc == Encoding.CRC)
        for w in (self.e_len_from, self.e_len_to, self.e_len_adj):
            w.setEnabled(enc == Encoding.LENGTH)
        numeric = enc == Encoding.VALUE and ftype not in (FieldType.BYTES, FieldType.STRING, FieldType.BOOLEAN,
                                                          FieldType.BITFIELD)
        for w in (self.e_scale, self.e_offset, self.e_min, self.e_max, self.e_unit):
            w.setEnabled(numeric)
        self.e_enum.setEnabled(numeric or ftype == FieldType.ENUM)
        is_bits = ftype == FieldType.BITFIELD
        for w in (self.bits, self.b_add, self.b_del, self.bits_label):
            w.setEnabled(is_bits)

    def _combo_field(self, combo: QComboBox) -> Optional[str]:
        text = combo.currentText()
        return None if text in (DEFAULT, "") else text

    def build_field(self) -> FieldDefinition:
        """Create a FieldDefinition from the editor (raises ValueError)."""
        old = self.current_field()
        name = self.e_name.text().strip()
        if not name:
            raise ValueError("the field name is empty")
        enc = Encoding(self.e_encoding.currentText())
        ftype = FieldType(self.e_type.currentText())
        crc_name = self.e_crc.currentText().strip()
        if enc == Encoding.CRC and not crc_mod.is_crc_name(crc_name):
            raise ValueError(f"unknown CRC algorithm '{crc_name}'")
        enum_text = self.e_enum.currentText()
        if enum_text == "(inline)":
            enum = old.enum if old else None
        elif enum_text in (NONE, ""):
            enum = None
        else:
            enum = enum_text
        bits = []
        if ftype == FieldType.BITFIELD:
            for r in range(self.bits.rowCount()):
                cells = [self.bits.item(r, c).text().strip() if self.bits.item(r, c) else "" for c in range(4)]
                if not cells[0]:
                    raise ValueError(f"bit group {r + 1} has no name")
                bit_enum = self.bits.item(r, 3).data(Qt.ItemDataRole.UserRole) if self.bits.item(r, 3) else None
                if bit_enum is None:
                    bit_enum = cells[3] or None
                    if bit_enum and bit_enum not in self.ctx.protocol.enums:
                        raise ValueError(f"unknown enumeration '{bit_enum}' in bit group {cells[0]}")
                bits.append(BitDefinition(cells[0], parse_int(cells[1]), parse_int(cells[2] or 1), bit_enum))
        value = self.e_value.text().strip() if enc == Encoding.CONSTANT else None
        if enc == Encoding.CONSTANT and not value:
            raise ValueError("a CONSTANT field needs a value")
        default_text = self.e_default.text().strip()
        default = None
        if default_text:
            try:
                default = parse_number(default_text)
            except ValueError:
                default = default_text
        endian = self.e_endian.currentText()
        field = FieldDefinition(
            name=name,
            type=ftype,
            size=self.e_size.value() or None,
            offset=None,
            encoding=enc,
            value=value,
            endianness=None if endian == DEFAULT else Endianness(endian),
            crc=crc_mod.normalize_name(crc_name) if enc == Encoding.CRC else None,
            crc_from=self._combo_field(self.e_crc_from) if enc == Encoding.CRC else None,
            crc_to=self._combo_field(self.e_crc_to) if enc == Encoding.CRC else None,
            length_from=self._combo_field(self.e_len_from) if enc == Encoding.LENGTH else None,
            length_to=self._combo_field(self.e_len_to) if enc == Encoding.LENGTH else None,
            length_adjust=self.e_len_adj.value() if enc == Encoding.LENGTH else 0,
            scale=1.0 if _opt_float(self.e_scale.text()) is None else _opt_float(self.e_scale.text()),
            value_offset=_opt_float(self.e_offset.text()) or 0.0,
            unit=self.e_unit.text().strip(),
            minimum=_opt_float(self.e_min.text()),
            maximum=_opt_float(self.e_max.text()),
            enum=enum,
            bits=bits,
            default=default,
            string_encoding=old.string_encoding if old else "ascii",
            description=self.e_desc.text(),
        )
        if field.scale == 0:
            raise ValueError("scale cannot be 0")
        fixed_size(field)  # raises DefinitionError for impossible sizes
        return field

    def apply_field(self) -> bool:
        frame = self.frame
        old = self.current_field()
        if frame is None or old is None:
            return False
        try:
            new = self.build_field()
        except (ValueError, DefinitionError) as exc:
            self.error(str(exc), "Invalid field")
            return False
        if new.name != old.name and frame.has_field(new.name):
            self.error(f"Field {new.name} already exists in {frame.name}.")
            return False
        index = frame.fields.index(old)
        # keep references from other fields when renaming
        if new.name != old.name:
            for other in frame.fields:
                for attr in ("crc_from", "crc_to", "length_from", "length_to"):
                    if getattr(other, attr) == old.name:
                        setattr(other, attr, new.name)
        frame.fields[index] = new
        self.ctx.mark_modified()
        self._select_field(new.name)
        return True

    # ----------------------------------------------------------- preview ---

    def _update_preview(self) -> None:
        frame = self.frame
        if frame is None:
            self.preview.setPlainText("No message selected. Add one on the Protocol Designer page.")
            return
        lines = []
        try:
            encoded = Encoder(self.ctx.protocol).encode(frame, {})
            lines.append(f"{encoded.hex}   ({len(encoded.data)} bytes)")
        except (ProtocolError, KeyError, ValueError) as exc:
            lines.append(f"Cannot build an example packet: {exc}")
        issues = [i for i in validate_protocol(self.ctx.protocol) if i.location.startswith(f"frame {frame.name}")]
        for issue in issues:
            lines.append(str(issue))
        if not issues:
            lines.append("✓ No problems in this message")
        self.preview.setPlainText("\n".join(lines))
        color = ERROR_COLOR if any(i.severity == ERROR for i in issues) else (WARN_COLOR if issues else None)
        self.preview.setStyleSheet(f"color: {color.name()}" if color else "")
