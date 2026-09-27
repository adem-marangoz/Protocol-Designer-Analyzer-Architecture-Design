"""Encoder: user values -> raw packet bytes.

Pipeline (Section 14 of the design): user input -> field validation ->
encoding -> length calculation -> CRC calculation -> raw packet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

from .errors import DefinitionError, EncodeError
from .fields import (
    constant_raw,
    crc_algorithm,
    default_physical,
    field_endianness,
    fixed_size,
    physical_to_raw,
    raw_to_bytes,
)
from .layout import FieldSpan, crc_range, length_range
from .model import Encoding, FrameDefinition, ProtocolDefinition
from .values import parse_int, to_hex


@dataclass
class EncodedFrame:
    frame: FrameDefinition
    data: bytes
    spans: List[FieldSpan] = field(default_factory=list)
    values: Dict[str, Any] = field(default_factory=dict)

    @property
    def hex(self) -> str:
        return to_hex(self.data)

    def field_bytes(self, name: str) -> bytes:
        for span in self.spans:
            if span.field.name == name:
                return self.data[span.offset : span.end]
        raise KeyError(name)


class Encoder:
    def __init__(self, protocol: ProtocolDefinition):
        self.protocol = protocol

    def encode(
        self,
        frame: Union[str, FrameDefinition],
        values: Optional[Mapping[str, Any]] = None,
        *,
        raw: bool = False,
        strict: bool = False,
    ) -> EncodedFrame:
        """Build a frame.

        ``values`` maps field names to physical values (scaled numbers, enum
        names, bit dicts...). With ``raw=True`` numbers are raw register values.
        Supplying a value for a LENGTH or CRC field overrides the computed value,
        which is useful for fault-injection tests. With ``strict=True`` missing
        user fields raise instead of using their default.
        """
        fdef = self.protocol.frame(frame) if isinstance(frame, str) else frame
        values = dict(values or {})
        unknown = [name for name in values if not fdef.has_field(name)]
        if unknown:
            raise EncodeError(f"frame '{fdef.name}' has no field(s): {', '.join(unknown)}")

        parts: List[Optional[bytes]] = []
        resolved: Dict[str, Any] = {}
        for f in fdef.fields:
            endian = field_endianness(f, self.protocol)
            try:
                size = fixed_size(f)
            except DefinitionError as exc:
                raise EncodeError(str(exc)) from None
            if f.encoding == Encoding.CONSTANT:
                raw_value = constant_raw(f)
                parts.append(raw_to_bytes(f, raw_value, size, endian))
                resolved[f.name] = raw_value
            elif f.encoding in (Encoding.LENGTH, Encoding.CRC):
                if f.name in values:  # explicit override (fault injection)
                    override = parse_int(values[f.name])
                    parts.append(raw_to_bytes(f, override, size, endian))
                    resolved[f.name] = override
                else:
                    parts.append(None)
            else:
                if f.name in values:
                    value = values[f.name]
                elif strict:
                    raise EncodeError(f"missing value for field '{f.name}'")
                else:
                    value = default_physical(f)
                raw_value = self._raw_value(f, value, raw)
                parts.append(raw_to_bytes(f, raw_value, size, endian))
                resolved[f.name] = value

        sizes = [len(p) if p is not None else fixed_size(f) for p, f in zip(parts, fdef.fields)]

        for i, f in enumerate(fdef.fields):
            if f.encoding == Encoding.LENGTH and parts[i] is None:
                start, end = length_range(fdef, i)
                length = sum(sizes[start : end + 1]) + f.length_adjust
                parts[i] = raw_to_bytes(f, length, sizes[i], field_endianness(f, self.protocol))
                resolved[f.name] = length

        for i, f in enumerate(fdef.fields):
            if f.encoding == Encoding.CRC and parts[i] is None:
                start, end = crc_range(fdef, i)
                covered = parts[start : end + 1]
                if any(p is None for p in covered):
                    raise EncodeError(f"CRC field '{f.name}' covers a CRC that is computed after it")
                alg = crc_algorithm(f)
                value = alg.compute(b"".join(covered))  # type: ignore[arg-type]
                parts[i] = raw_to_bytes(f, value, sizes[i], field_endianness(f, self.protocol))
                resolved[f.name] = value

        spans: List[FieldSpan] = []
        offset = 0
        for i, (f, part) in enumerate(zip(fdef.fields, parts)):
            assert part is not None
            spans.append(FieldSpan(f, i, offset, len(part)))
            offset += len(part)
        data = b"".join(parts)  # type: ignore[arg-type]
        return EncodedFrame(fdef, data, spans, resolved)

    def _raw_value(self, f, value: Any, raw: bool):
        return physical_to_raw(f, value, self.protocol, raw=raw)


def encode(protocol: ProtocolDefinition, frame: str, values: Optional[Mapping[str, Any]] = None, **kw) -> bytes:
    return Encoder(protocol).encode(frame, values, **kw).data
