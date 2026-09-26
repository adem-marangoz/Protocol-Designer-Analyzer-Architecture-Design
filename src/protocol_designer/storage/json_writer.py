"""Write protocol definitions to JSON.

Default values are omitted so the saved file stays as readable as a
hand-written one. Files are written atomically (temp file + rename) so a crash
never leaves a half-written protocol behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Union

from ..protocol.model import (
    BitDefinition,
    CheckDefinition,
    Encoding,
    FieldDefinition,
    FieldType,
    FrameDefinition,
    ProtocolDefinition,
    SimulatorRule,
    TestDefinition,
    TestStep,
    TransportSettings,
)
from .json_loader import FORMAT_VERSION


def _num(value: float):
    return int(value) if float(value).is_integer() else value


def _enum_ref(ref):
    if isinstance(ref, dict):
        return {f"0x{k:02X}": v for k, v in sorted(ref.items())}
    return ref


def bit_to_dict(bit: BitDefinition) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": bit.name, "start": bit.start}
    if bit.length != 1:
        out["length"] = bit.length
    if bit.enum is not None:
        out["enum"] = _enum_ref(bit.enum)
    if bit.description:
        out["description"] = bit.description
    return out


def field_to_dict(f: FieldDefinition) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": f.name, "type": f.type.value}
    if f.offset is not None:
        out["offset"] = f.offset
    if f.size is not None:
        out["size"] = f.size
    if f.encoding != Encoding.VALUE:
        out["encoding"] = f.encoding.value
    if f.encoding == Encoding.CONSTANT:
        out["value"] = f.value
    if f.endianness is not None:
        out["endianness"] = f.endianness.value
    if f.encoding == Encoding.CRC:
        out["crc"] = f.crc
        if f.crc_from:
            out["crc_from"] = f.crc_from
        if f.crc_to:
            out["crc_to"] = f.crc_to
    if f.encoding == Encoding.LENGTH:
        if f.length_from:
            out["length_from"] = f.length_from
        if f.length_to:
            out["length_to"] = f.length_to
        if f.length_adjust:
            out["length_adjust"] = f.length_adjust
    if f.scale != 1.0:
        out["scale"] = _num(f.scale)
    if f.value_offset != 0.0:
        out["value_offset"] = _num(f.value_offset)
    if f.unit:
        out["unit"] = f.unit
    if f.minimum is not None:
        out["min"] = _num(f.minimum)
    if f.maximum is not None:
        out["max"] = _num(f.maximum)
    if f.enum is not None:
        out["enum"] = _enum_ref(f.enum)
    if f.bits:
        out["bits"] = [bit_to_dict(b) for b in f.bits]
    if f.default is not None:
        out["default"] = f.default
    if f.type == FieldType.STRING and f.string_encoding != "ascii":
        out["string_encoding"] = f.string_encoding
    if f.description:
        out["description"] = f.description
    return out


def frame_to_dict(fr: FrameDefinition) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": fr.name}
    if fr.id is not None:
        out["id"] = fr.id
    out["direction"] = fr.direction.value
    if fr.description:
        out["description"] = fr.description
    if fr.can_id is not None:
        out["can_id"] = fr.can_id
        if fr.can_extended:
            out["can_extended"] = True
    if fr.expected_response:
        out["expected_response"] = fr.expected_response
    out["fields"] = [field_to_dict(f) for f in fr.fields]
    return out


def transport_to_dict(t: TransportSettings) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": t.type.value}
    out.update(
        {
            "port": t.port,
            "baudrate": t.baudrate,
            "data_bits": t.data_bits,
            "parity": t.parity,
            "stop_bits": _num(t.stop_bits),
            "host": t.host,
            "tcp_port": t.tcp_port,
            "can_interface": t.can_interface,
            "can_channel": t.can_channel,
            "can_bitrate": t.can_bitrate,
            "can_data_bitrate": t.can_data_bitrate,
            "timeout_ms": t.timeout_ms,
        }
    )
    if t.options:
        out["options"] = dict(t.options)
    return out


def _check_to_dict(c: CheckDefinition) -> Dict[str, Any]:
    out: Dict[str, Any] = {"field": c.field}
    if c.equals is not None:
        out["equals"] = c.equals
    if c.not_equals is not None:
        out["not_equals"] = c.not_equals
    if c.minimum is not None:
        out["min"] = _num(c.minimum)
    if c.maximum is not None:
        out["max"] = _num(c.maximum)
    if c.raw:
        out["raw"] = True
    if c.save_as:
        out["save_as"] = c.save_as
    return out


def step_to_dict(s: TestStep) -> Dict[str, Any]:
    out: Dict[str, Any] = {"action": s.action}
    if s.message:
        out["message"] = s.message
    if s.values:
        out["values"] = dict(s.values)
    if s.data:
        out["data"] = s.data
    if s.timeout_ms is not None:
        out["timeout_ms"] = s.timeout_ms
    if s.checks:
        out["checks"] = [_check_to_dict(c) for c in s.checks]
    if s.text:
        out["text"] = s.text
    if s.delay_ms:
        out["delay_ms"] = s.delay_ms
    if s.variable:
        out["variable"] = s.variable
    if s.value is not None:
        out["value"] = s.value
    return out


def test_to_dict(t: TestDefinition) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": t.name}
    if t.description:
        out["description"] = t.description
    out["timeout_ms"] = t.timeout_ms
    out["steps"] = [step_to_dict(s) for s in t.steps]
    return out


test_to_dict.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def rule_to_dict(r: SimulatorRule) -> Dict[str, Any]:
    out: Dict[str, Any] = {"on": r.on}
    if r.reply:
        out["reply"] = r.reply
    if r.values:
        out["values"] = dict(r.values)
    if r.copy:
        out["copy"] = list(r.copy)
    if r.delay_ms:
        out["delay_ms"] = r.delay_ms
    if r.match:
        out["match"] = dict(r.match)
    return out


def protocol_to_dict(p: ProtocolDefinition) -> Dict[str, Any]:
    head: Dict[str, Any] = {"name": p.name, "version": p.version}
    if p.description:
        head["description"] = p.description
    head["endianness"] = p.endianness.value
    out: Dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "protocol": head,
        "transport": transport_to_dict(p.transport),
    }
    if p.enums:
        out["enums"] = {
            name: ({"description": e.description} if e.description else {})
            | {"values": {f"0x{k:02X}": v for k, v in sorted(e.values.items())}}
            for name, e in p.enums.items()
        }
    out["frames"] = [frame_to_dict(f) for f in p.frames]
    if p.tests:
        out["tests"] = [test_to_dict(t) for t in p.tests]
    if p.simulator:
        out["simulator"] = [rule_to_dict(r) for r in p.simulator]
    return out


def _json_default(obj):
    if isinstance(obj, (bytes, bytearray)):
        return " ".join(f"{b:02X}" for b in obj)
    raise TypeError(f"cannot serialise {type(obj).__name__}")


def dumps(p: ProtocolDefinition) -> str:
    return json.dumps(protocol_to_dict(p), indent=2, ensure_ascii=False, default=_json_default) + "\n"


def save_protocol(p: ProtocolDefinition, path: Union[str, Path]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(p)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
