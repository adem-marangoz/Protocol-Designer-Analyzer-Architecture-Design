"""Validation of protocol definitions.

The validator reports problems instead of raising, so editors can show every
issue at once. ``ERROR`` issues prevent encoding/decoding; ``WARNING`` issues
are suspicious but usable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import crc as crc_mod
from .errors import DefinitionError
from .fields import constant_raw, crc_algorithm, fixed_size, raw_range, raw_to_bytes, resolve_enum
from .layout import covering_length_field, crc_range, length_range
from .model import (
    FLOAT_SIZES,
    INTEGER_SIZES,
    Encoding,
    Endianness,
    FieldDefinition,
    FieldType,
    FrameDefinition,
    ProtocolDefinition,
)

ERROR = "ERROR"
WARNING = "WARNING"

_ACTIONS = {"send", "send_raw", "expect", "transact", "delay", "log", "set"}


@dataclass
class Issue:
    severity: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.location}: {self.message}"


def validate_protocol(protocol: ProtocolDefinition) -> List[Issue]:
    issues: List[Issue] = []
    add = lambda sev, loc, msg: issues.append(Issue(sev, loc, msg))  # noqa: E731

    if not protocol.name.strip():
        add(ERROR, "protocol", "name is empty")
    if not protocol.frames:
        add(WARNING, "protocol", "no frames defined")

    for name, enum in protocol.enums.items():
        if not name.strip():
            add(ERROR, "enums", "enumeration with empty name")
        labels = [v.upper() for v in enum.values.values()]
        if len(labels) != len(set(labels)):
            add(ERROR, f"enum {name}", "duplicate labels")
        if any(v < 0 for v in enum.values):
            add(ERROR, f"enum {name}", "values must be non-negative")

    names = [f.name for f in protocol.frames]
    for dup in {n for n in names if names.count(n) > 1}:
        add(ERROR, f"frame {dup}", "duplicate frame name")

    for fdef in protocol.frames:
        _validate_frame(protocol, fdef, add)

    _check_ambiguous(protocol, add)
    _validate_tests(protocol, add)
    _validate_simulator(protocol, add)
    return issues


def has_errors(issues: List[Issue]) -> bool:
    return any(i.severity == ERROR for i in issues)


def _validate_frame(protocol: ProtocolDefinition, fdef: FrameDefinition, add) -> None:
    loc = f"frame {fdef.name}"
    if not fdef.name.strip():
        add(ERROR, "frame", "frame with empty name")
    if not fdef.fields:
        add(ERROR, loc, "frame has no fields")
        return
    field_names = [f.name for f in fdef.fields]
    for dup in {n for n in field_names if field_names.count(n) > 1}:
        add(ERROR, f"{loc}.{dup}", "duplicate field name")
    if fdef.expected_response and not protocol.has_frame(fdef.expected_response):
        add(ERROR, loc, f"expected response '{fdef.expected_response}' is not a frame")
    if fdef.can_id is not None:
        limit = 0x1FFFFFFF if fdef.can_extended else 0x7FF
        if not 0 <= fdef.can_id <= limit:
            add(ERROR, loc, f"CAN id 0x{fdef.can_id:X} out of range (max 0x{limit:X})")

    variable = []
    offset: Optional[int] = 0
    for i, f in enumerate(fdef.fields):
        floc = f"{loc}.{f.name}"
        if not f.name.strip():
            add(ERROR, loc, f"field #{i} has an empty name")
        try:
            size = fixed_size(f)
        except DefinitionError as exc:
            add(ERROR, floc, str(exc))
            offset = None
            continue
        if size is None:
            variable.append(i)
        if f.offset is not None and offset is not None and f.offset != offset:
            add(ERROR, floc, f"declared offset {f.offset} but the field starts at byte {offset}")
        offset = offset + size if (offset is not None and size is not None) else None
        _validate_field(protocol, fdef, i, f, floc, add)

    if len(variable) > 1:
        add(ERROR, loc, "only one variable-size field is allowed per frame")
    elif variable:
        idx = variable[0]
        if covering_length_field(fdef, idx) is None and idx != len(fdef.fields) - 1:
            trailing_var = any(fixed_size(f) is None for f in fdef.fields[idx + 1 :])
            if trailing_var:
                add(ERROR, loc, "variable field is followed by another variable field")
        if covering_length_field(fdef, idx) is None:
            add(
                WARNING,
                loc,
                f"variable field '{fdef.fields[idx].name}' has no LENGTH field; the frame can only be "
                "decoded when received as a complete message (e.g. CAN), not from a byte stream",
            )


def _validate_field(protocol, fdef: FrameDefinition, index: int, f: FieldDefinition, floc: str, add) -> None:
    if f.endianness not in (None, Endianness.BIG, Endianness.LITTLE):
        add(ERROR, floc, "invalid endianness")
    if f.scale == 0:
        add(ERROR, floc, "scale cannot be 0")
    if f.minimum is not None and f.maximum is not None and f.minimum > f.maximum:
        add(ERROR, floc, "minimum is greater than maximum")

    if f.encoding == Encoding.CONSTANT:
        try:
            raw = constant_raw(f)
            raw_to_bytes(f, raw, fixed_size(f), f.endianness or protocol.endianness)
        except Exception as exc:  # noqa: BLE001 - report any conversion problem
            add(ERROR, floc, f"invalid constant value: {exc}")
    elif f.encoding == Encoding.LENGTH:
        if f.type not in (FieldType.UINT8, FieldType.UINT16, FieldType.UINT32, FieldType.UINT64):
            add(ERROR, floc, "LENGTH fields must be unsigned integers")
        try:
            start, end = length_range(fdef, index)
            if start <= index <= end:
                add(WARNING, floc, "LENGTH field counts itself")
        except KeyError as exc:
            add(ERROR, floc, f"length range: {exc.args[0]}")
    elif f.encoding == Encoding.CRC:
        try:
            alg = crc_algorithm(f)
            if f.type in FLOAT_SIZES or f.type in (FieldType.BYTES, FieldType.STRING):
                add(ERROR, floc, "CRC fields must be integers")
            elif f.type in INTEGER_SIZES and INTEGER_SIZES[f.type][0] != alg.size:
                add(ERROR, floc, f"{alg.name} needs {alg.size} byte(s) but {f.type.value} has {INTEGER_SIZES[f.type][0]}")
        except DefinitionError as exc:
            add(ERROR, floc, str(exc))
        try:
            start, end = crc_range(fdef, index)
            if start > end:
                add(ERROR, floc, "CRC covers no bytes")
            if start <= index <= end:
                add(ERROR, floc, "CRC field cannot cover itself")
        except KeyError as exc:
            add(ERROR, floc, f"CRC range: {exc.args[0]}")

    if f.enum is not None:
        try:
            resolve_enum(f.enum, protocol)
        except (DefinitionError, ValueError) as exc:
            add(ERROR, floc, str(exc))

    if f.type == FieldType.BITFIELD:
        try:
            size = fixed_size(f) or 1
        except DefinitionError:
            size = 1
        used = 0
        for bit in f.bits:
            if bit.length < 1 or bit.start < 0 or bit.start + bit.length > size * 8:
                add(ERROR, f"{floc}.{bit.name}", f"bits {bit.start}..{bit.start + bit.length - 1} outside {size * 8}-bit field")
                continue
            if used & bit.mask:
                add(ERROR, f"{floc}.{bit.name}", "overlaps another bit group")
            used |= bit.mask
            if bit.enum is not None:
                try:
                    resolve_enum(bit.enum, protocol)
                except (DefinitionError, ValueError) as exc:
                    add(ERROR, f"{floc}.{bit.name}", str(exc))
        if not f.bits:
            add(WARNING, floc, "BITFIELD without bit definitions")
    elif f.bits:
        add(WARNING, floc, "bit definitions are only used by BITFIELD fields")

    if f.type == FieldType.ENUM and f.enum is None:
        add(WARNING, floc, "ENUM field without an enumeration")

    if f.default is not None and f.encoding == Encoding.VALUE:
        rng = None
        try:
            rng = raw_range(f, fixed_size(f) or 0)
        except DefinitionError:
            pass
        if rng is not None and isinstance(f.default, (int, float)) and not f.is_scaled and f.type != FieldType.BITFIELD:
            if not rng[0] <= f.default <= rng[1]:
                add(ERROR, floc, f"default {f.default} out of range")

    if f.type == FieldType.STRING:
        try:
            "".encode(f.string_encoding)
        except LookupError:
            add(ERROR, floc, f"unknown string encoding '{f.string_encoding}'")


def _signature(protocol, fdef: FrameDefinition):
    """Constant bytes at their offsets plus the fixed size; used to find duplicates."""
    sig = []
    offset = 0
    for f in fdef.fields:
        size = fixed_size(f)
        if size is None:
            return None
        if f.encoding == Encoding.CONSTANT:
            sig.append((offset, raw_to_bytes(f, constant_raw(f), size, f.endianness or protocol.endianness)))
        offset += size
    return (tuple(sig), offset, fdef.can_id)


def _check_ambiguous(protocol: ProtocolDefinition, add) -> None:
    seen = {}
    for fdef in protocol.frames:
        try:
            sig = _signature(protocol, fdef)
        except Exception:  # noqa: BLE001 - errors are reported elsewhere
            continue
        if sig is None:
            continue
        if sig in seen:
            add(
                WARNING,
                f"frame {fdef.name}",
                f"cannot be distinguished from '{seen[sig]}' (same constants and size)",
            )
        else:
            seen[sig] = fdef.name


def _validate_tests(protocol: ProtocolDefinition, add) -> None:
    names = [t.name for t in protocol.tests]
    for dup in {n for n in names if names.count(n) > 1}:
        add(ERROR, f"test {dup}", "duplicate test name")
    for test in protocol.tests:
        for n, step in enumerate(test.steps, 1):
            loc = f"test {test.name} step {n}"
            if step.action not in _ACTIONS:
                add(ERROR, loc, f"unknown action '{step.action}'")
                continue
            if step.action in ("send", "expect", "transact"):
                if not step.message or not protocol.has_frame(step.message):
                    add(ERROR, loc, f"unknown message '{step.message}'")
                    continue
                fdef = protocol.frame(step.message)
                if step.action in ("send", "transact"):
                    for key in step.values:
                        if not fdef.has_field(key):
                            add(ERROR, loc, f"message '{fdef.name}' has no field '{key}'")
                if step.action == "transact" and not fdef.expected_response:
                    add(ERROR, loc, f"message '{fdef.name}' has no expected response")
                target = fdef
                if step.action == "transact" and fdef.expected_response and protocol.has_frame(fdef.expected_response):
                    target = protocol.frame(fdef.expected_response)
                for check in step.checks:
                    if not target.has_field(check.field):
                        add(ERROR, loc, f"message '{target.name}' has no field '{check.field}'")


def _validate_simulator(protocol: ProtocolDefinition, add) -> None:
    for n, rule in enumerate(protocol.simulator, 1):
        loc = f"simulator rule {n}"
        if not protocol.has_frame(rule.on):
            add(ERROR, loc, f"unknown request '{rule.on}'")
            continue
        if rule.reply is not None and not protocol.has_frame(rule.reply):
            add(ERROR, loc, f"unknown reply '{rule.reply}'")
            continue
        if rule.reply:
            reply = protocol.frame(rule.reply)
            request = protocol.frame(rule.on)
            for key in rule.values:
                if not reply.has_field(key):
                    add(ERROR, loc, f"reply '{reply.name}' has no field '{key}'")
            for key in rule.copy:
                if not reply.has_field(key) or not request.has_field(key):
                    add(ERROR, loc, f"field '{key}' must exist in both request and reply to be copied")


def known_crc_names() -> List[str]:
    return sorted(crc_mod.ALGORITHMS)
