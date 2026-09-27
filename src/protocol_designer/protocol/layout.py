"""Frame layout: where each field sits in a byte sequence.

Most frames have a fixed layout. A frame may contain one variable-size
BYTES/STRING field whose size is taken from a preceding LENGTH field, or –
when the whole frame is available (e.g. a CAN message) – from the bytes that
remain after the trailing fixed-size fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .errors import DecodeError, DefinitionError, FrameMismatch, IncompleteFrame
from .fields import bytes_to_raw, constant_raw, field_endianness, fixed_size, raw_to_bytes
from .model import Encoding, FieldDefinition, FrameDefinition, ProtocolDefinition


@dataclass
class FieldSpan:
    field: FieldDefinition
    index: int
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


def length_range(frame: FrameDefinition, index: int) -> Tuple[int, int]:
    """Inclusive field index range counted by the LENGTH field at ``index``."""
    f = frame.fields[index]
    start = frame.field_index(f.length_from) if f.length_from else index + 1
    if f.length_to:
        end = frame.field_index(f.length_to)
    else:
        end = len(frame.fields) - 1
        for j in range(index + 1, len(frame.fields)):
            if frame.fields[j].encoding == Encoding.CRC:
                end = j - 1
                break
    return start, end


def crc_range(frame: FrameDefinition, index: int) -> Tuple[int, int]:
    """Inclusive field index range covered by the CRC field at ``index``."""
    f = frame.fields[index]
    start = frame.field_index(f.crc_from) if f.crc_from else 0
    end = frame.field_index(f.crc_to) if f.crc_to else index - 1
    return start, end


def constant_bytes(f: FieldDefinition, protocol: Optional[ProtocolDefinition]) -> bytes:
    return raw_to_bytes(f, constant_raw(f), fixed_size(f), field_endianness(f, protocol))


def variable_index(frame: FrameDefinition) -> Optional[int]:
    found = [i for i, f in enumerate(frame.fields) if fixed_size(f) is None]
    if len(found) > 1:
        raise DefinitionError(f"frame '{frame.name}' has more than one variable-size field")
    return found[0] if found else None


def covering_length_field(frame: FrameDefinition, index: int) -> Optional[int]:
    """Index of the LENGTH field (before ``index``) whose range includes ``index``."""
    for j in range(index):
        if frame.fields[j].encoding == Encoding.LENGTH:
            start, end = length_range(frame, j)
            if start <= index <= end:
                return j
    return None


def is_streamable(frame: FrameDefinition) -> bool:
    """True when the frame's total length can be derived from a byte stream."""
    idx = variable_index(frame)
    return idx is None or covering_length_field(frame, idx) is not None


def minimum_size(frame: FrameDefinition) -> int:
    return sum(fixed_size(f) or 0 for f in frame.fields)


def compute_layout(
    frame: FrameDefinition,
    data: bytes,
    protocol: Optional[ProtocolDefinition] = None,
    *,
    complete: bool = True,
    check_constants: bool = True,
) -> List[FieldSpan]:
    """Compute field spans for ``data``.

    ``complete`` means ``data`` holds exactly one whole frame (message-oriented
    transports, or a user pasted frame). With ``complete=False`` the data is the
    head of a byte stream and :class:`IncompleteFrame` is raised when more bytes
    are needed. :class:`FrameMismatch` is raised as soon as a constant differs.
    """
    spans: List[FieldSpan] = []
    offset = 0
    fields = frame.fields
    for i, f in enumerate(fields):
        size = fixed_size(f)
        if size is None:
            size = _variable_size(frame, i, spans, data, offset, protocol, complete)
        if offset + size > len(data):
            if complete:
                raise DecodeError(
                    f"frame '{frame.name}' too short: field '{f.name}' needs bytes "
                    f"{offset}..{offset + size - 1}, got {len(data)} byte(s)"
                )
            raise IncompleteFrame(offset + size)
        if check_constants and f.encoding == Encoding.CONSTANT:
            expected = constant_bytes(f, protocol)
            if data[offset : offset + size] != expected:
                raise FrameMismatch(f"field '{f.name}' is not the constant {expected.hex().upper()}")
        spans.append(FieldSpan(f, i, offset, size))
        offset += size
    return spans


def _variable_size(
    frame: FrameDefinition,
    index: int,
    spans: List[FieldSpan],
    data: bytes,
    offset: int,
    protocol: Optional[ProtocolDefinition],
    complete: bool,
) -> int:
    length_idx = covering_length_field(frame, index)
    if length_idx is not None:
        lf = frame.fields[length_idx]
        span = spans[length_idx]
        length_value = bytes_to_raw(lf, data[span.offset : span.end], field_endianness(lf, protocol))
        start, end = length_range(frame, length_idx)
        others = sum(fixed_size(frame.fields[j]) or 0 for j in range(start, end + 1) if j != index)
        size = int(length_value) - lf.length_adjust - others
        if size < 0:
            raise FrameMismatch(
                f"length field '{lf.name}' = {length_value} is smaller than the fixed fields it covers"
            )
        return size
    if not complete:
        raise DefinitionError(
            f"frame '{frame.name}' cannot be decoded from a stream: variable field "
            f"'{frame.fields[index].name}' is not covered by a LENGTH field"
        )
    trailing = sum(fixed_size(f) or 0 for f in frame.fields[index + 1 :])
    size = len(data) - offset - trailing
    if size < 0:
        raise DecodeError(f"frame '{frame.name}' too short for its trailing fields")
    return size
