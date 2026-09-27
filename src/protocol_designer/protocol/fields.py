"""Field parser: conversion between field values and bytes.

Three representations of a field value exist:

* **bytes**    – what travels on the wire,
* **raw**      – the integer / float / bytes / str stored in those bytes,
* **physical** – what the user sees: scaled numbers, enum names, bit dicts.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, Optional, Union

from . import crc as crc_mod
from .errors import DefinitionError, EncodeError
from .model import (
    FLOAT_SIZES,
    INTEGER_SIZES,
    UNSIGNED_CONTAINER_TYPES,
    BitDefinition,
    Encoding,
    Endianness,
    FieldDefinition,
    FieldType,
    ProtocolDefinition,
)
from .values import format_number, parse_bool, parse_hex_bytes, parse_int, parse_number, to_hex

Raw = Union[int, float, bytes, str]


# ---------------------------------------------------------------- sizes ---


def crc_algorithm(f: FieldDefinition) -> crc_mod.CrcAlgorithm:
    spec = f.crc
    if spec is None:
        raise DefinitionError(f"field '{f.name}' is a CRC field without an algorithm")
    if isinstance(spec, dict):
        return crc_mod.custom_algorithm(spec)
    return crc_mod.get_algorithm(spec)


def fixed_size(f: FieldDefinition) -> Optional[int]:
    """Return the field size in bytes, or ``None`` for variable-size fields."""
    if f.encoding == Encoding.CRC:
        size = crc_algorithm(f).size
        if f.size and f.size != size:
            raise DefinitionError(
                f"field '{f.name}': CRC {crc_algorithm(f).name} needs {size} byte(s), size is {f.size}"
            )
        return size
    if f.type in INTEGER_SIZES:
        size = INTEGER_SIZES[f.type][0]
        if f.size and f.size != size:
            raise DefinitionError(f"field '{f.name}': {f.type.value} is {size} byte(s), size is {f.size}")
        return size
    if f.type in FLOAT_SIZES:
        size = FLOAT_SIZES[f.type]
        if f.size and f.size != size:
            raise DefinitionError(f"field '{f.name}': {f.type.value} is {size} byte(s), size is {f.size}")
        return size
    if f.type in UNSIGNED_CONTAINER_TYPES:
        size = f.size or 1
        if size not in (1, 2, 3, 4, 8):
            raise DefinitionError(f"field '{f.name}': {f.type.value} size must be 1, 2, 3, 4 or 8")
        return size
    # BYTES / STRING
    if f.size is None or f.size == 0:
        return None
    if f.size < 0:
        raise DefinitionError(f"field '{f.name}': size cannot be negative")
    return f.size


def field_endianness(f: FieldDefinition, protocol: Optional[ProtocolDefinition]) -> Endianness:
    if f.endianness is not None:
        return f.endianness
    if protocol is not None:
        return protocol.endianness
    return Endianness.BIG


def _byteorder(endian: Endianness) -> str:
    return "little" if endian == Endianness.LITTLE else "big"


def is_signed(f: FieldDefinition) -> bool:
    return f.type in INTEGER_SIZES and INTEGER_SIZES[f.type][1]


def raw_range(f: FieldDefinition, size: int) -> Optional[tuple]:
    """Inclusive range of raw integer values, or None for non-integer types."""
    if f.type in FLOAT_SIZES or f.type in (FieldType.BYTES, FieldType.STRING):
        return None
    bits = size * 8
    if is_signed(f):
        return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return (0, (1 << bits) - 1)


# ------------------------------------------------------------ raw <-> bytes ---


def raw_to_bytes(f: FieldDefinition, raw: Raw, size: Optional[int], endian: Endianness) -> bytes:
    """Serialise a raw value. ``size`` is None only for variable BYTES/STRING."""
    if f.type in FLOAT_SIZES and f.encoding not in (Encoding.CRC, Encoding.LENGTH):
        fmt = ("<" if endian == Endianness.LITTLE else ">") + ("f" if f.type == FieldType.FLOAT32 else "d")
        try:
            return struct.pack(fmt, float(raw))
        except (OverflowError, struct.error) as exc:
            raise EncodeError(f"field '{f.name}': {exc}") from None
    if f.type == FieldType.BYTES and f.encoding not in (Encoding.CRC, Encoding.LENGTH):
        data = bytes(raw) if isinstance(raw, (bytes, bytearray)) else parse_hex_bytes(raw)
        return _fit(f, data, size, b"\x00")
    if f.type == FieldType.STRING and f.encoding not in (Encoding.CRC, Encoding.LENGTH):
        try:
            data = raw.encode(f.string_encoding) if isinstance(raw, str) else bytes(raw)
        except (UnicodeEncodeError, LookupError) as exc:
            raise EncodeError(f"field '{f.name}': cannot encode string: {exc}") from None
        return _fit(f, data, size, b"\x00")
    # integer-like
    assert size is not None
    value = int(raw)
    lo_hi = raw_range(f, size) or (0, (1 << (size * 8)) - 1)
    if f.encoding in (Encoding.CRC, Encoding.LENGTH):
        lo_hi = (0, (1 << (size * 8)) - 1)
    lo, hi = lo_hi
    if not lo <= value <= hi:
        raise EncodeError(f"field '{f.name}': raw value {value} out of range [{lo}, {hi}]")
    return value.to_bytes(size, _byteorder(endian), signed=value < 0)


def _fit(f: FieldDefinition, data: bytes, size: Optional[int], pad: bytes) -> bytes:
    if size is None:
        return data
    if len(data) > size:
        raise EncodeError(f"field '{f.name}': {len(data)} byte(s) do not fit in {size}")
    return data + pad * (size - len(data))


def bytes_to_raw(f: FieldDefinition, data: bytes, endian: Endianness) -> Raw:
    if f.encoding in (Encoding.CRC, Encoding.LENGTH):
        return int.from_bytes(data, _byteorder(endian), signed=False)
    if f.type in FLOAT_SIZES:
        fmt = ("<" if endian == Endianness.LITTLE else ">") + ("f" if f.type == FieldType.FLOAT32 else "d")
        return struct.unpack(fmt, data)[0]
    if f.type == FieldType.BYTES:
        return bytes(data)
    if f.type == FieldType.STRING:
        return bytes(data).split(b"\x00", 1)[0].decode(f.string_encoding, errors="replace")
    return int.from_bytes(data, _byteorder(endian), signed=is_signed(f))


# -------------------------------------------------------------- enumerations ---


def resolve_enum(
    ref: Optional[Union[str, Dict[int, str]]], protocol: Optional[ProtocolDefinition]
) -> Optional[Dict[int, str]]:
    if ref is None:
        return None
    if isinstance(ref, dict):
        return {parse_int(k): str(v) for k, v in ref.items()}
    if protocol is None or ref not in protocol.enums:
        raise DefinitionError(f"unknown enumeration '{ref}'")
    return protocol.enums[ref].values


def _enum_lookup_name(mapping: Dict[int, str], name: str) -> Optional[int]:
    target = name.strip().upper()
    for value, label in mapping.items():
        if label.upper() == target:
            return value
    return None


# ----------------------------------------------------------- physical -> raw ---


def constant_raw(f: FieldDefinition) -> Raw:
    """Raw value of a CONSTANT field."""
    if f.value is None:
        raise DefinitionError(f"constant field '{f.name}' has no value")
    if f.type == FieldType.BYTES:
        return parse_hex_bytes(f.value)
    if f.type == FieldType.STRING:
        return str(f.value)
    if f.type in FLOAT_SIZES:
        return float(parse_number(f.value))
    return parse_int(f.value)


def default_physical(f: FieldDefinition) -> Any:
    if f.default is not None:
        return f.default
    if f.type == FieldType.BYTES:
        return b""
    if f.type == FieldType.STRING:
        return ""
    if f.type == FieldType.BOOLEAN:
        return False
    if f.minimum is not None and f.minimum > 0:
        return f.minimum
    return 0


def physical_to_raw(
    f: FieldDefinition, value: Any, protocol: Optional[ProtocolDefinition], raw: bool = False
) -> Raw:
    """Convert a user supplied (physical) value to the raw value to transmit.

    With ``raw=True`` numbers are taken as raw register values: scaling and the
    physical min/max limits are skipped, but enum names and bit dicts still work.
    """
    t = f.type
    try:
        if t == FieldType.BYTES:
            return parse_hex_bytes(value)
        if t == FieldType.STRING:
            return value if isinstance(value, str) else str(value)
        if t == FieldType.BOOLEAN:
            return 1 if parse_bool(value) else 0
        if t == FieldType.BITFIELD:
            return _bits_to_raw(f, value, protocol)
        enum_map = resolve_enum(f.enum, protocol)
        if isinstance(value, str) and enum_map is not None:
            found = _enum_lookup_name(enum_map, value)
            if found is not None:
                return found
        if t == FieldType.ENUM:
            return parse_int(value)
        number = parse_number(value)
    except (ValueError, TypeError) as exc:
        raise EncodeError(f"field '{f.name}': {exc}") from None

    if raw:
        if t in FLOAT_SIZES:
            return float(number)
        if isinstance(number, float) and not number.is_integer():
            raise EncodeError(f"field '{f.name}': raw value {number} is not an integer")
        return int(number)
    _check_limits(f, number)
    if t in FLOAT_SIZES:
        return (float(number) - f.value_offset) / f.scale
    if f.is_scaled:
        # Values between two raw steps are rounded to the nearest step.
        return int(round((float(number) - f.value_offset) / f.scale))
    if isinstance(number, float):
        if not number.is_integer():
            raise EncodeError(f"field '{f.name}': {number} is not an integer")
        number = int(number)
    return number


def _check_limits(f: FieldDefinition, number: float) -> None:
    if f.minimum is not None and number < f.minimum:
        raise EncodeError(f"field '{f.name}': {format_number(number)} is below minimum {format_number(f.minimum)}")
    if f.maximum is not None and number > f.maximum:
        raise EncodeError(f"field '{f.name}': {format_number(number)} is above maximum {format_number(f.maximum)}")


def _bits_to_raw(f: FieldDefinition, value: Any, protocol: Optional[ProtocolDefinition]) -> int:
    if not isinstance(value, dict):
        return parse_int(value)
    raw = 0
    known = {b.name: b for b in f.bits}
    for name, bit_value in value.items():
        if name not in known:
            raise ValueError(f"unknown bit group '{name}'")
        bit = known[name]
        if isinstance(bit_value, str):
            enum_map = resolve_enum(bit.enum, protocol)
            mapped = _enum_lookup_name(enum_map, bit_value) if enum_map else None
            if mapped is not None:
                number = mapped
            elif bit.length == 1:
                number = 1 if parse_bool(bit_value) else 0
            else:
                number = parse_int(bit_value)
        elif isinstance(bit_value, bool):
            number = int(bit_value)
        else:
            number = parse_int(bit_value)
        if not 0 <= number < (1 << bit.length):
            raise ValueError(f"bit group '{name}' value {number} does not fit in {bit.length} bit(s)")
        raw |= number << bit.start
    return raw


# ----------------------------------------------------------- raw -> physical ---


def raw_to_physical(f: FieldDefinition, raw: Raw) -> Any:
    if f.encoding in (Encoding.CRC, Encoding.LENGTH):
        return raw
    if f.type == FieldType.BOOLEAN:
        return bool(raw)
    if f.type in FLOAT_SIZES:
        return float(raw) * f.scale + f.value_offset
    if isinstance(raw, int) and f.is_scaled and f.type in INTEGER_SIZES:
        value = raw * f.scale + f.value_offset
        return round(value, 12)
    return raw


def decode_bits(f: FieldDefinition, raw: int, protocol: Optional[ProtocolDefinition]) -> list:
    """Return ``[(BitDefinition, value, display)]`` for a BITFIELD raw value."""
    out = []
    for bit in f.bits:
        value = (raw >> bit.start) & ((1 << bit.length) - 1)
        enum_map = resolve_enum(bit.enum, protocol) if bit.enum is not None else None
        if enum_map is not None:
            display = enum_map.get(value, f"UNKNOWN({value})")
        elif bit.length == 1:
            display = "✓" if value else "✗"
        else:
            display = str(value)
        out.append((bit, value, display))
    return out


def format_display(
    f: FieldDefinition, raw: Raw, physical: Any, protocol: Optional[ProtocolDefinition], size: int
) -> str:
    """Human readable representation shown in analyzers and monitors."""
    if f.encoding == Encoding.CRC:
        return f"0x{int(raw):0{size * 2}X}"
    if f.type == FieldType.BYTES:
        return to_hex(raw) if raw else "(empty)"
    if f.type == FieldType.STRING:
        return f'"{raw}"'
    if f.type == FieldType.BOOLEAN:
        return "true" if physical else "false"
    if f.type == FieldType.BITFIELD:
        return f"0x{int(raw):0{size * 2}X}"
    try:
        enum_map = resolve_enum(f.enum, protocol)
    except DefinitionError:
        enum_map = None
    if enum_map is not None and isinstance(raw, int):
        return enum_map.get(raw, f"UNKNOWN(0x{raw:0{size * 2}X})")
    if f.encoding == Encoding.CONSTANT and isinstance(raw, int):
        return f"0x{raw:0{size * 2}X}"
    if f.is_scaled and f.type not in FLOAT_SIZES:
        text = f"{float(physical):.{scale_decimals(f.scale)}f}"
    elif f.type in FLOAT_SIZES:
        text = format_number(float(physical))
    else:
        text = str(physical)
    return f"{text} {f.unit}".strip()


def scale_decimals(scale: float) -> int:
    """Decimals needed to show values of a given scale (0.1 -> 1, 0.25 -> 2)."""
    text = format_number(abs(scale), 9)
    return min(len(text.split(".")[1]), 9) if "." in text else 0


def bit_by_name(f: FieldDefinition, name: str) -> BitDefinition:
    for bit in f.bits:
        if bit.name == name:
            return bit
    raise KeyError(name)
