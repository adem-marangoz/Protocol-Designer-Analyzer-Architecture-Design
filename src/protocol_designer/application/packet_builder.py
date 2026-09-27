"""Packet builder service: turns form input into packets (Section 14)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..protocol.encoder import EncodedFrame, Encoder
from ..protocol.errors import EncodeError
from ..protocol.fields import default_physical, fixed_size, resolve_enum
from ..protocol.model import FieldDefinition, FieldType, FrameDefinition, ProtocolDefinition
from ..protocol.values import parse_bool, parse_hex_bytes, parse_number, to_hex


@dataclass
class FieldInput:
    """What the GUI needs to render an input for one user field."""

    name: str
    type: FieldType
    kind: str  # number | enum | bool | bits | bytes | text
    default: str
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    options: List[str] = field(default_factory=list)  # enum labels
    bits: List[Dict[str, Any]] = field(default_factory=list)  # for BITFIELD
    size: Optional[int] = None
    description: str = ""

    @property
    def hint(self) -> str:
        parts = [self.type.value + (f"[{self.size}]" if self.size and self.kind in ("bytes", "text") else "")]
        if self.unit:
            parts.append(self.unit)
        if self.minimum is not None or self.maximum is not None:
            lo = "" if self.minimum is None else f"{self.minimum:g}"
            hi = "" if self.maximum is None else f"{self.maximum:g}"
            parts.append(f"{lo}..{hi}")
        return ", ".join(parts)


def _default_text(f: FieldDefinition, protocol: ProtocolDefinition) -> str:
    value = default_physical(f)
    if isinstance(value, (bytes, bytearray)):
        return to_hex(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if f.enum is not None and not isinstance(value, str):
        mapping = resolve_enum(f.enum, protocol) or {}
        if mapping:
            # the default number if it has a label, else the first label
            return mapping.get(int(value), next(iter(mapping.values())))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class PacketBuilder:
    def __init__(self, protocol: ProtocolDefinition):
        self.protocol = protocol
        self.encoder = Encoder(protocol)

    def inputs(self, frame: FrameDefinition) -> List[FieldInput]:
        result = []
        for f in frame.user_fields:
            enum_map = resolve_enum(f.enum, self.protocol) if f.enum is not None else None
            if f.type == FieldType.BITFIELD:
                kind = "bits"
            elif f.type == FieldType.BOOLEAN:
                kind = "bool"
            elif enum_map:
                kind = "enum"
            elif f.type == FieldType.BYTES:
                kind = "bytes"
            elif f.type == FieldType.STRING:
                kind = "text"
            else:
                kind = "number"
            bits = []
            for b in f.bits:
                bit_enum = resolve_enum(b.enum, self.protocol) if b.enum is not None else None
                bits.append({"name": b.name, "start": b.start, "length": b.length,
                             "options": list(bit_enum.values()) if bit_enum else []})
            result.append(
                FieldInput(
                    name=f.name,
                    type=f.type,
                    kind=kind,
                    default=_default_text(f, self.protocol),
                    unit=f.unit,
                    minimum=f.minimum,
                    maximum=f.maximum,
                    options=list(enum_map.values()) if enum_map else [],
                    bits=bits,
                    size=fixed_size(f),
                    description=f.description,
                )
            )
        return result

    def parse_input(self, f: FieldDefinition, text: Any) -> Any:
        """Convert text typed into the form into a value for the encoder."""
        if not isinstance(text, str):
            return text
        text = text.strip()
        if f.type == FieldType.BYTES:
            return parse_hex_bytes(text)
        if f.type == FieldType.STRING:
            return text
        if f.type == FieldType.BOOLEAN:
            return parse_bool(text)
        if text == "":
            return default_physical(f)
        try:
            return parse_number(text)
        except ValueError:
            return text  # enum label; the encoder resolves / rejects it

    def build(self, frame_name: str, form: Dict[str, Any]) -> EncodedFrame:
        frame = self.protocol.frame(frame_name)
        values: Dict[str, Any] = {}
        for name, text in form.items():
            if not frame.has_field(name):
                raise EncodeError(f"frame '{frame_name}' has no field '{name}'")
            try:
                values[name] = self.parse_input(frame.field(name), text)
            except ValueError as exc:
                raise EncodeError(f"field '{name}': {exc}") from None
        return self.encoder.encode(frame, values)
