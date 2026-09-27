"""Language independent description of a protocol, consumed by the generators.

The plan resolves everything the generated code needs – sizes, byte order,
constants, CRC parameters, LENGTH/CRC ranges – so each language backend only
has to print code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ...protocol.crc import CrcAlgorithm
from ...protocol.errors import DefinitionError
from ...protocol.fields import constant_raw, crc_algorithm, field_endianness, fixed_size, raw_to_bytes, resolve_enum
from ...protocol.layout import covering_length_field, crc_range, length_range
from ...protocol.model import (
    FLOAT_SIZES,
    INTEGER_SIZES,
    Encoding,
    Endianness,
    FieldDefinition,
    FieldType,
    FrameDefinition,
    ProtocolDefinition,
)
from ...protocol.validator import has_errors, validate_protocol

DEFAULT_MAX_VARIABLE = 256


# Words that are reserved in C, C++, C# or Python; identifiers get a trailing "_".
RESERVED = set(
    """auto break case char const continue default do double else enum extern float for goto if inline int long
    register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while
    bool true false class delete new namespace operator private protected public template this throw try catch
    virtual using friend explicit mutable typename export and or not xor
    abstract as base byte checked decimal delegate event finally fixed foreach implicit in interface internal is
    lock null object out override params readonly ref sbyte sealed stackalloc string uint ulong unchecked unsafe
    ushort
    def del elif except from global import lambda nonlocal pass raise with yield assert async await none""".split()
)


def snake(name: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_")
    text = re.sub(r"([a-z])([A-Z])", r"\1_\2", text)  # camelCase -> camel_Case
    text = re.sub(r"([0-9])([A-Z][a-z])", r"\1_\2", text)
    text = text.lower() or "field"
    if text[0].isdigit():
        text = f"f_{text}"
    return f"{text}_" if text in RESERVED else text


def pascal(name: str) -> str:
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", name) if p]
    text = "".join(p[:1].upper() + p[1:].lower() if p.isupper() or p.islower() else p[:1].upper() + p[1:] for p in parts)
    text = text or "Message"
    return f"M{text}" if text[0].isdigit() else text


def upper(name: str) -> str:
    return snake(name).upper()


@dataclass
class FieldPlan:
    field: FieldDefinition
    index: int
    ident: str  # snake_case identifier
    kind: str  # uint | int | float | bool | bytes | string
    size: Optional[int]  # None for the variable field
    little: bool
    user: bool  # value supplied by the application
    const: Optional[bytes] = None
    crc: Optional[CrcAlgorithm] = None
    range_start: int = 0  # LENGTH/CRC covered field range (inclusive)
    range_end: int = -1
    length_adjust: int = 0
    length_field: Optional[int] = None  # for the variable field: index of its LENGTH field
    max_size: int = 0  # for the variable field
    storage_bits: int = 0  # integer width used to store the value (8/16/32/64)
    enum: Optional[str] = None  # enum type name for ENUM/enum fields
    comment: str = ""

    @property
    def name(self) -> str:
        return self.field.name

    @property
    def encoding(self) -> Encoding:
        return self.field.encoding

    @property
    def variable(self) -> bool:
        return self.size is None


@dataclass
class FramePlan:
    frame: FrameDefinition
    type_name: str  # PascalCase
    const_name: str  # UPPER_SNAKE
    fields: List[FieldPlan]
    min_size: int
    max_size: int

    @property
    def user_fields(self) -> List[FieldPlan]:
        return [f for f in self.fields if f.user]

    @property
    def variable(self) -> Optional[FieldPlan]:
        return next((f for f in self.fields if f.variable), None)


@dataclass
class EnumPlan:
    name: str
    type_name: str
    const_name: str
    values: Dict[int, str]


@dataclass
class ProtocolPlan:
    protocol: ProtocolDefinition
    prefix: str  # e.g. TPMS_RS485
    type_prefix: str  # e.g. TpmsRs485
    enums: List[EnumPlan]
    frames: List[FramePlan]
    crcs: List[CrcAlgorithm] = field(default_factory=list)


def _kind(f: FieldDefinition) -> str:
    if f.encoding in (Encoding.LENGTH, Encoding.CRC):
        return "uint"
    if f.type in FLOAT_SIZES:
        return "float"
    if f.type == FieldType.BOOLEAN:
        return "bool"
    if f.type == FieldType.BYTES:
        return "bytes"
    if f.type == FieldType.STRING:
        return "string"
    if f.type in INTEGER_SIZES and INTEGER_SIZES[f.type][1]:
        return "int"
    return "uint"


def _storage_bits(size: Optional[int]) -> int:
    if not size:
        return 0
    for bits in (8, 16, 32, 64):
        if size * 8 <= bits:
            return bits
    return 64


def build_plan(
    protocol: ProtocolDefinition, prefix: Optional[str] = None, max_variable: int = DEFAULT_MAX_VARIABLE
) -> ProtocolPlan:
    issues = validate_protocol(protocol)
    if has_errors(issues):
        errors = "; ".join(str(i) for i in issues if i.severity == "ERROR")
        raise DefinitionError(f"fix the protocol errors before generating code: {errors}")
    prefix = upper(prefix if prefix is not None else protocol.name)
    type_prefix = pascal(prefix)

    enums = [
        EnumPlan(name, type_prefix + pascal(name), upper(name), dict(sorted(e.values.items())))
        for name, e in protocol.enums.items()
    ]
    enum_types = {e.name: e.type_name for e in enums}

    frames: List[FramePlan] = []
    crcs: Dict[str, CrcAlgorithm] = {}
    for fdef in protocol.frames:
        plans: List[FieldPlan] = []
        for i, f in enumerate(fdef.fields):
            size = fixed_size(f)
            endian = field_endianness(f, protocol)
            plan = FieldPlan(
                field=f,
                index=i,
                ident=snake(f.name),
                kind=_kind(f),
                size=size,
                little=endian == Endianness.LITTLE,
                user=f.encoding == Encoding.VALUE,
                storage_bits=_storage_bits(size) if _kind(f) in ("uint", "int", "bool") else 0,
            )
            if f.encoding == Encoding.CONSTANT:
                plan.const = raw_to_bytes(f, constant_raw(f), size, endian)
            elif f.encoding == Encoding.CRC:
                plan.crc = crc_algorithm(f)
                crcs[plan.crc.name] = plan.crc
                plan.range_start, plan.range_end = crc_range(fdef, i)
            elif f.encoding == Encoding.LENGTH:
                plan.range_start, plan.range_end = length_range(fdef, i)
                plan.length_adjust = f.length_adjust
            if f.enum is not None:
                if isinstance(f.enum, str):
                    plan.enum = enum_types.get(f.enum)
                else:
                    resolve_enum(f.enum, protocol)
            notes = []
            if f.is_scaled:
                notes.append(f"physical = raw * {f.scale:g} + {f.value_offset:g}")
            if f.unit:
                notes.append(f"unit: {f.unit}")
            if f.minimum is not None or f.maximum is not None:
                notes.append(f"range: {'' if f.minimum is None else f'{f.minimum:g}'}..{'' if f.maximum is None else f'{f.maximum:g}'}")
            if f.description:
                notes.append(f.description)
            plan.comment = "; ".join(notes)
            plans.append(plan)

        min_size = sum(p.size or 0 for p in plans)
        max_size = min_size
        var = next((p for p in plans if p.variable), None)
        if var is not None:
            var.length_field = covering_length_field(fdef, var.index)
            limit = max_variable
            if var.length_field is not None:
                lp = plans[var.length_field]
                others = sum(plans[j].size or 0 for j in range(lp.range_start, lp.range_end + 1) if j != var.index)
                limit = min(limit, (1 << (8 * (lp.size or 1))) - 1 - lp.length_adjust - others)
            var.max_size = max(limit, 0)
            max_size += var.max_size
        frames.append(FramePlan(fdef, type_prefix + pascal(fdef.name), upper(fdef.name), plans, min_size, max_size))
    return ProtocolPlan(protocol, prefix, type_prefix, enums, frames, sorted(crcs.values(), key=lambda a: a.name))
