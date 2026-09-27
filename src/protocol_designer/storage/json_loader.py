"""Load protocol definitions from JSON (``.json`` / ``.pdproj``).

The loader accepts the format shown in the design document (Section 8) and a
few convenient shorthands, e.g. ``"encoding": "CRC8"`` or ``"encoding":
"AUTO"``, and converts everything into the :mod:`protocol.model` classes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..protocol import crc as crc_mod
from ..protocol.errors import DefinitionError
from ..protocol.model import (
    BitDefinition,
    CheckDefinition,
    Direction,
    Encoding,
    Endianness,
    EnumDefinition,
    FieldDefinition,
    FieldType,
    FrameDefinition,
    ProtocolDefinition,
    SimulatorRule,
    TestDefinition,
    TestStep,
    TransportSettings,
    TransportType,
)
from ..protocol.values import parse_int, parse_number

FORMAT_VERSION = 1

_TYPE_ALIASES = {
    "U8": "UINT8", "U16": "UINT16", "U32": "UINT32", "U64": "UINT64",
    "I8": "INT8", "I16": "INT16", "I32": "INT32", "I64": "INT64",
    "BYTE": "UINT8", "WORD": "UINT16", "DWORD": "UINT32",
    "FLOAT": "FLOAT32", "DOUBLE": "FLOAT64", "BOOL": "BOOLEAN",
    "BITS": "BITFIELD", "RAW": "BYTES", "ASCII": "STRING",
}
_ENCODING_ALIASES = {
    "": "VALUE", "NONE": "VALUE", "VALUE": "VALUE", "USER": "VALUE",
    "UINT8": "VALUE", "INT": "VALUE",
    "CONST": "CONSTANT", "CONSTANT": "CONSTANT", "FIXED": "CONSTANT",
    "AUTO": "LENGTH", "LENGTH": "LENGTH", "LEN": "LENGTH",
    "CRC": "CRC", "CHECKSUM": "CRC",
}


def _enum_value(cls, value: Any, what: str, aliases: Optional[dict] = None):
    text = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    if aliases:
        text = aliases.get(text, text)
    try:
        return cls(text)
    except ValueError:
        options = ", ".join(m.value for m in cls)
        raise DefinitionError(f"invalid {what} '{value}' (expected one of: {options})") from None


def _opt_int(value: Any) -> Optional[int]:
    return None if value is None or value == "" else parse_int(value)


def _parse_enum_values(raw: Any, where: str) -> Dict[int, str]:
    values: Dict[int, str] = {}
    if isinstance(raw, dict):
        for key, label in raw.items():
            values[parse_int(key)] = str(label)
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or "value" not in item or "name" not in item:
                raise DefinitionError(f"{where}: enum entries need 'value' and 'name'")
            values[parse_int(item["value"])] = str(item["name"])
    else:
        raise DefinitionError(f"{where}: invalid enumeration values")
    return values


def _parse_enum_ref(raw: Any, where: str):
    if raw is None or isinstance(raw, str):
        return raw
    return _parse_enum_values(raw, where)


def parse_enums(raw: Any) -> Dict[str, EnumDefinition]:
    enums: Dict[str, EnumDefinition] = {}
    if raw is None:
        return enums
    if isinstance(raw, list):
        for item in raw:
            name = str(item.get("name", ""))
            enums[name] = EnumDefinition(name, _parse_enum_values(item.get("values", {}), f"enum {name}"),
                                         str(item.get("description", "")))
        return enums
    for name, body in raw.items():
        if isinstance(body, dict) and "values" in body:
            enums[name] = EnumDefinition(name, _parse_enum_values(body["values"], f"enum {name}"),
                                         str(body.get("description", "")))
        else:
            enums[name] = EnumDefinition(name, _parse_enum_values(body, f"enum {name}"))
    return enums


def _parse_bit(raw: dict, where: str) -> BitDefinition:
    name = str(raw.get("name", ""))
    if "start" in raw:
        start = parse_int(raw["start"])
        length = parse_int(raw.get("length", 1))
    elif "bits" in raw or "bit" in raw:
        spec = str(raw.get("bits", raw.get("bit")))
        m = re.fullmatch(r"\s*(\d+)\s*(?:[-:.]+\s*(\d+))?\s*", spec)
        if not m:
            raise DefinitionError(f"{where}.{name}: invalid bit range '{spec}'")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) is not None else a
        start, length = min(a, b), abs(b - a) + 1
    else:
        raise DefinitionError(f"{where}.{name}: bit definition needs 'start' or 'bits'")
    return BitDefinition(
        name=name,
        start=start,
        length=length,
        enum=_parse_enum_ref(raw.get("enum"), f"{where}.{name}"),
        description=str(raw.get("description", "")),
    )


def parse_field(raw: dict, where: str = "field") -> FieldDefinition:
    if not isinstance(raw, dict):
        raise DefinitionError(f"{where}: field must be an object")
    name = str(raw.get("name", "")).strip()
    where = f"{where} {name}"
    ftype = _enum_value(FieldType, raw.get("type", "UINT8"), f"type of {where}", _TYPE_ALIASES)

    enc_text = str(raw.get("encoding", "VALUE")).strip().upper().replace("-", "_")
    crc_spec = raw.get("crc")
    if enc_text in _ENCODING_ALIASES:
        encoding = Encoding(_ENCODING_ALIASES[enc_text])
    elif crc_mod.is_crc_name(enc_text):
        encoding = Encoding.CRC
        crc_spec = crc_spec or crc_mod.normalize_name(enc_text)
    else:
        raise DefinitionError(f"{where}: unknown encoding '{raw.get('encoding')}'")
    if encoding == Encoding.CRC and crc_spec is None:
        crc_spec = "CRC8"
    if isinstance(crc_spec, str):
        crc_spec = crc_mod.normalize_name(crc_spec)

    try:
        return FieldDefinition(
            name=name,
            type=ftype,
            size=_opt_int(raw.get("size")),
            offset=_opt_int(raw.get("offset")),
            encoding=encoding,
            value=raw.get("value"),
            endianness=(
                _enum_value(Endianness, raw["endianness"], f"endianness of {where}")
                if raw.get("endianness") not in (None, "", "DEFAULT")
                else None
            ),
            crc=crc_spec if encoding == Encoding.CRC else None,
            crc_from=raw.get("crc_from") or None,
            crc_to=raw.get("crc_to") or None,
            length_from=raw.get("length_from") or None,
            length_to=raw.get("length_to") or None,
            length_adjust=parse_int(raw.get("length_adjust", 0)),
            scale=float(parse_number(raw.get("scale", 1.0))),
            value_offset=float(parse_number(raw.get("value_offset", raw.get("scale_offset", 0.0)))),
            unit=str(raw.get("unit", "")),
            minimum=None if raw.get("min") is None else float(parse_number(raw["min"])),
            maximum=None if raw.get("max") is None else float(parse_number(raw["max"])),
            enum=_parse_enum_ref(raw.get("enum"), where),
            bits=[_parse_bit(b, where) for b in raw.get("bits", [])],
            default=raw.get("default"),
            string_encoding=str(raw.get("string_encoding", "ascii")),
            description=str(raw.get("description", "")),
        )
    except ValueError as exc:
        raise DefinitionError(f"{where}: {exc}") from None


def parse_frame(raw: dict) -> FrameDefinition:
    if not isinstance(raw, dict):
        raise DefinitionError("frame must be an object")
    name = str(raw.get("name", "")).strip()
    try:
        return FrameDefinition(
            name=name,
            id=_opt_int(raw.get("id")),
            direction=_enum_value(Direction, raw.get("direction", "BOTH"), f"direction of frame {name}"),
            fields=[parse_field(f, f"frame {name} field") for f in raw.get("fields", [])],
            description=str(raw.get("description", "")),
            can_id=_opt_int(raw.get("can_id")),
            can_extended=bool(raw.get("can_extended", False)),
            expected_response=raw.get("expected_response") or None,
        )
    except ValueError as exc:
        raise DefinitionError(f"frame {name}: {exc}") from None


def parse_transport(raw: Optional[dict]) -> TransportSettings:
    raw = dict(raw or {})
    t = TransportSettings()
    if "type" in raw:
        t.type = _enum_value(TransportType, raw.pop("type"), "transport type",
                             {"SERIAL": "UART", "RS232": "UART", "CAN_FD": "CANFD"})
    mapping = {
        "port": str, "baudrate": parse_int, "data_bits": parse_int, "parity": lambda v: str(v).upper(),
        "stop_bits": lambda v: float(parse_number(v)), "host": str, "tcp_port": parse_int,
        "can_interface": str, "can_channel": str, "can_bitrate": parse_int,
        "can_data_bitrate": parse_int, "timeout_ms": parse_int,
    }
    for key, conv in mapping.items():
        if key in raw:
            try:
                setattr(t, key, conv(raw.pop(key)))
            except ValueError as exc:
                raise DefinitionError(f"transport.{key}: {exc}") from None
    if float(t.stop_bits).is_integer():
        t.stop_bits = int(t.stop_bits)
    t.options = dict(raw.pop("options", {}))
    t.options.update(raw)  # keep unknown keys so nothing is lost on save
    return t


def _parse_check(raw: dict) -> CheckDefinition:
    return CheckDefinition(
        field=str(raw.get("field", "")),
        equals=raw.get("equals"),
        not_equals=raw.get("not_equals"),
        minimum=None if raw.get("min") is None else float(parse_number(raw["min"])),
        maximum=None if raw.get("max") is None else float(parse_number(raw["max"])),
        raw=bool(raw.get("raw", False)),
        save_as=raw.get("save_as") or None,
    )


def parse_test(raw: dict) -> TestDefinition:
    steps = []
    for s in raw.get("steps", []):
        steps.append(
            TestStep(
                action=str(s.get("action", "")).lower(),
                message=s.get("message"),
                values=dict(s.get("values", {})),
                data=str(s.get("data", "")),
                timeout_ms=_opt_int(s.get("timeout_ms")),
                checks=[_parse_check(c) for c in s.get("checks", [])],
                text=str(s.get("text", "")),
                delay_ms=parse_int(s.get("delay_ms", s.get("ms", 0))),
                variable=str(s.get("variable", "")),
                value=s.get("value"),
            )
        )
    return TestDefinition(
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        timeout_ms=parse_int(raw.get("timeout_ms", 500)),
        steps=steps,
    )


def parse_simulator_rule(raw: dict) -> SimulatorRule:
    return SimulatorRule(
        on=str(raw.get("on", "")),
        reply=raw.get("reply"),
        values=dict(raw.get("values", {})),
        copy=list(raw.get("copy", [])),
        delay_ms=parse_int(raw.get("delay_ms", 0)),
        match=dict(raw.get("match", {})),
    )


def protocol_from_dict(data: dict) -> ProtocolDefinition:
    if not isinstance(data, dict):
        raise DefinitionError("protocol file must contain a JSON object")
    version = data.get("format_version", FORMAT_VERSION)
    if parse_int(version) > FORMAT_VERSION:
        raise DefinitionError(
            f"file format version {version} is newer than supported ({FORMAT_VERSION}); please update the program"
        )
    head = data.get("protocol", {})
    frames_raw = data.get("frames", data.get("messages", []))
    return ProtocolDefinition(
        name=str(head.get("name", "Unnamed Protocol")),
        version=str(head.get("version", "1.0")),
        description=str(head.get("description", "")),
        endianness=_enum_value(Endianness, head.get("endianness", "BIG"), "protocol endianness"),
        transport=parse_transport(data.get("transport")),
        enums=parse_enums(data.get("enums")),
        frames=[parse_frame(f) for f in frames_raw],
        tests=[parse_test(t) for t in data.get("tests", [])],
        simulator=[parse_simulator_rule(r) for r in data.get("simulator", [])],
    )


def loads(text: str) -> ProtocolDefinition:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DefinitionError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    return protocol_from_dict(data)


def load_protocol(path: Union[str, Path]) -> ProtocolDefinition:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DefinitionError(f"cannot read '{path}': {exc.strerror or exc}") from None
    try:
        return loads(text)
    except DefinitionError as exc:
        raise DefinitionError(f"{path.name}: {exc}") from None


def list_protocol_files(directory: Union[str, Path]) -> List[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.suffix.lower() in (".json", ".pdproj") and p.is_file()]
    return sorted(files, key=lambda p: p.name.lower())
