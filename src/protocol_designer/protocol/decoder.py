"""Decoder: raw bytes -> decoded message.

Pipeline (Section 13 of the design): find frame -> read header -> read length
-> extract payload -> validate CRC -> identify command -> decode fields ->
apply scaling -> apply enum -> decoded message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .errors import DecodeError, DefinitionError
from .fields import (
    bytes_to_raw,
    crc_algorithm,
    decode_bits,
    field_endianness,
    format_display,
    raw_to_physical,
    resolve_enum,
)
from .layout import FieldSpan, compute_layout, constant_bytes, crc_range, length_range
from .model import Encoding, FieldType, FrameDefinition, ProtocolDefinition
from .values import format_number, to_hex


@dataclass
class DecodedBit:
    name: str
    value: int
    display: str
    start: int
    length: int


@dataclass
class DecodedField:
    name: str
    type: FieldType
    encoding: Encoding
    offset: int
    size: int
    raw_bytes: bytes
    raw: Any
    value: Any
    display: str
    unit: str = ""
    bits: List[DecodedBit] = field(default_factory=list)
    valid: bool = True
    error: Optional[str] = None

    @property
    def hex(self) -> str:
        return to_hex(self.raw_bytes)


@dataclass
class DecodedMessage:
    frame: Optional[FrameDefinition]
    data: bytes
    fields: List[DecodedField] = field(default_factory=list)
    crc_valid: Optional[bool] = None
    length_valid: Optional[bool] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    can_id: Optional[int] = None

    @property
    def name(self) -> str:
        return self.frame.name if self.frame else "UNKNOWN"

    @property
    def valid(self) -> bool:
        return self.frame is not None and not self.errors

    @property
    def hex(self) -> str:
        return to_hex(self.data)

    def field(self, name: str) -> DecodedField:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"message '{self.name}' has no field '{name}'")

    def has_field(self, name: str) -> bool:
        return any(f.name == name for f in self.fields)

    @property
    def values(self) -> Dict[str, Any]:
        return {f.name: f.value for f in self.fields}

    @property
    def crc_status(self) -> str:
        if self.crc_valid is None:
            return "N/A"
        return "VALID" if self.crc_valid else "INVALID"

    def summary(self) -> str:
        """Multi-line text similar to the Live Monitor 'Decoded' pane."""
        lines = [self.name]
        for f in self.fields:
            if f.encoding == Encoding.CRC:
                lines.append(f"  {f.name:<16}{f.display} ({'VALID' if f.valid else 'INVALID'})")
                continue
            lines.append(f"  {f.name:<16}{f.display}")
            for bit in f.bits:
                lines.append(f"    {bit.name:<18}{bit.display}")
        for err in self.errors:
            lines.append(f"  ERROR: {err}")
        for warn in self.warnings:
            lines.append(f"  WARNING: {warn}")
        return "\n".join(lines)


class Decoder:
    def __init__(self, protocol: ProtocolDefinition):
        self.protocol = protocol

    # --------------------------------------------------------------- single ---

    def decode(
        self,
        frame: Union[str, FrameDefinition],
        data: bytes,
        *,
        check_constants: bool = True,
        can_id: Optional[int] = None,
    ) -> DecodedMessage:
        """Decode ``data`` (one complete frame) as ``frame``.

        Raises :class:`DecodeError` when the bytes cannot be laid out as the
        frame (too short, constant mismatch). CRC / length problems do not
        raise; they are reported on the returned message.
        """
        fdef = self.protocol.frame(frame) if isinstance(frame, str) else frame
        data = bytes(data)
        spans = compute_layout(fdef, data, self.protocol, complete=True, check_constants=check_constants)
        return self._decode_spans(fdef, data, spans, can_id)

    def decode_spans(self, fdef: FrameDefinition, data: bytes, spans: List[FieldSpan]) -> DecodedMessage:
        return self._decode_spans(fdef, bytes(data), spans, None)

    def _decode_spans(
        self, fdef: FrameDefinition, data: bytes, spans: List[FieldSpan], can_id: Optional[int]
    ) -> DecodedMessage:
        msg = DecodedMessage(fdef, data, can_id=can_id)
        total = spans[-1].end if spans else 0
        if len(data) > total:
            msg.errors.append(f"{len(data) - total} unexpected trailing byte(s)")
            data_used = data[:total]
        else:
            data_used = data
        sizes = [s.size for s in spans]

        for span in spans:
            f = span.field
            chunk = data_used[span.offset : span.end]
            endian = field_endianness(f, self.protocol)
            raw = bytes_to_raw(f, chunk, endian)
            try:
                physical = raw_to_physical(f, raw)
                display = format_display(f, raw, physical, self.protocol, span.size)
            except DefinitionError as exc:
                physical, display = raw, str(raw)
                msg.errors.append(str(exc))
            decoded = DecodedField(
                name=f.name,
                type=f.type,
                encoding=f.encoding,
                offset=span.offset,
                size=span.size,
                raw_bytes=chunk,
                raw=raw,
                value=physical,
                display=display,
                unit=f.unit,
            )

            if f.encoding == Encoding.CONSTANT:
                expected = constant_bytes(f, self.protocol)
                if chunk != expected:
                    decoded.valid = False
                    decoded.error = f"expected {to_hex(expected)}"
                    msg.errors.append(f"{f.name}: expected constant {to_hex(expected)}, got {to_hex(chunk)}")
            elif f.encoding == Encoding.LENGTH:
                start, end = length_range(fdef, span.index)
                expected_len = sum(sizes[start : end + 1]) + f.length_adjust
                ok = raw == expected_len
                msg.length_valid = ok if msg.length_valid is None else (msg.length_valid and ok)
                if not ok:
                    decoded.valid = False
                    decoded.error = f"expected {expected_len}"
                    msg.errors.append(f"{f.name}: length {raw} does not match payload size {expected_len}")
            elif f.encoding == Encoding.CRC:
                start, end = crc_range(fdef, span.index)
                covered = data_used[spans[start].offset : spans[end].end] if end >= start else b""
                alg = crc_algorithm(f)
                expected_crc = alg.compute(covered)
                ok = raw == expected_crc
                msg.crc_valid = ok if msg.crc_valid is None else (msg.crc_valid and ok)
                if not ok:
                    decoded.valid = False
                    decoded.error = f"expected 0x{expected_crc:0{span.size * 2}X}"
                    msg.errors.append(
                        f"{f.name}: CRC 0x{raw:0{span.size * 2}X} invalid, "
                        f"expected 0x{expected_crc:0{span.size * 2}X} ({alg.name})"
                    )
            else:
                if f.type == FieldType.BITFIELD and isinstance(raw, int):
                    try:
                        for bit, value, bit_display in decode_bits(f, raw, self.protocol):
                            decoded.bits.append(DecodedBit(bit.name, value, bit_display, bit.start, bit.length))
                    except DefinitionError as exc:
                        msg.errors.append(str(exc))
                    decoded.value = {b.name: b.value for b in decoded.bits} if decoded.bits else raw
                self._check_range(f, decoded, msg)
                self._check_enum(f, decoded, msg)
            msg.fields.append(decoded)
        return msg

    def _check_range(self, f, decoded: DecodedField, msg: DecodedMessage) -> None:
        value = decoded.value
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return
        if f.minimum is not None and value < f.minimum:
            decoded.error = f"below minimum {format_number(f.minimum)}"
            msg.warnings.append(f"{f.name}: {format_number(value)} below minimum {format_number(f.minimum)}")
        elif f.maximum is not None and value > f.maximum:
            decoded.error = f"above maximum {format_number(f.maximum)}"
            msg.warnings.append(f"{f.name}: {format_number(value)} above maximum {format_number(f.maximum)}")

    def _check_enum(self, f, decoded: DecodedField, msg: DecodedMessage) -> None:
        if f.enum is None or not isinstance(decoded.raw, int):
            return
        try:
            mapping = resolve_enum(f.enum, self.protocol)
        except DefinitionError:
            return
        if mapping is not None:
            if decoded.raw in mapping:
                decoded.value = mapping[decoded.raw]
            else:
                msg.warnings.append(f"{f.name}: value {decoded.raw} is not in the enumeration")

    # ------------------------------------------------------------- identify ---

    def candidate_frames(self, can_id: Optional[int] = None) -> List[FrameDefinition]:
        frames = list(self.protocol.frames)
        if can_id is not None and any(f.can_id is not None for f in frames):
            frames = [f for f in frames if f.can_id == can_id]
        # Frames with more constants are more specific; try them first.
        return sorted(frames, key=lambda f: -sum(1 for x in f.fields if x.encoding == Encoding.CONSTANT))

    def identify(self, data: bytes, can_id: Optional[int] = None) -> Optional[DecodedMessage]:
        """Find the frame that best matches one complete packet."""
        best: Optional[DecodedMessage] = None
        for fdef in self.candidate_frames(can_id):
            try:
                msg = self.decode(fdef, data, can_id=can_id)
            except (DecodeError, DefinitionError):
                continue
            if msg.valid:
                return msg
            if best is None or len(msg.errors) < len(best.errors):
                best = msg
        return best

    def decode_any(self, data: bytes, can_id: Optional[int] = None) -> DecodedMessage:
        msg = self.identify(data, can_id)
        if msg is not None:
            return msg
        unknown = DecodedMessage(None, bytes(data), can_id=can_id)
        unknown.errors.append("no frame definition matches these bytes")
        return unknown
