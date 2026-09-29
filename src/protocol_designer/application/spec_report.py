"""Protocol specification document (PDF / HTML).

Produces a complete, printable description of a protocol: transport, message
overview, enumerations, and for every message its byte layout, field table,
bit fields, LENGTH/CRC rules and an example packet, followed by the CRC
algorithms, tests, simulator rules and validation results.

The document is built as HTML (``build_spec_html``) and rendered to PDF with
Qt's own PDF writer (``export_pdf``), so no extra dependency is needed.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from .. import APP_NAME, __version__
from ..protocol.decoder import Decoder
from ..protocol.encoder import Encoder
from ..protocol.errors import DefinitionError, ProtocolError
from ..protocol.fields import crc_algorithm, field_endianness, fixed_size, resolve_enum
from ..protocol.layout import covering_length_field, crc_range, length_range
from ..protocol.model import Encoding, FieldDefinition, FieldType, FrameDefinition, ProtocolDefinition
from ..protocol.validator import ERROR, validate_protocol
from ..protocol.values import format_number
from ..storage.json_writer import step_to_dict

ENCODING_COLORS = {
    Encoding.CONSTANT: "#dbeafe",
    Encoding.LENGTH: "#fef3c7",
    Encoding.CRC: "#fde2e1",
    Encoding.VALUE: "#e6f4ea",
}
HEADER_BG = "#1f3a5f"
TABLE = 'border="1" cellspacing="0" cellpadding="4" width="100%" style="border-collapse: collapse; border-color: #9aa4b2;"'

CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 9pt; color: #1f2328; }
h1 { font-size: 22pt; color: #1f3a5f; margin-bottom: 2px; }
h2 { font-size: 15pt; color: #1f3a5f; margin-top: 14px; margin-bottom: 6px; }
h3 { font-size: 11.5pt; color: #1f3a5f; margin-top: 12px; margin-bottom: 4px; }
th { background-color: #1f3a5f; color: #ffffff; font-weight: bold; text-align: left; }
td { vertical-align: top; }
.mono { font-family: Consolas, 'DejaVu Sans Mono', monospace; }
.muted { color: #57606a; }
.small { font-size: 8pt; }
.err { color: #cf222e; }
.warn { color: #9a6700; }
.ok { color: #1a7f37; }
"""


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _hex(value: Optional[int], width: int = 2) -> str:
    return "" if value is None else f"0x{value:0{width}X}"


def _num(value: Optional[float]) -> str:
    return "" if value is None else format_number(value)


def _table(
    headers: List[str],
    rows: List[List[str]],
    widths: Optional[List[str]] = None,
    raw: bool = False,
    nowrap: Tuple[int, ...] = (),
) -> str:
    """HTML table. Cells are escaped unless ``raw`` is true (then callers escape).

    Columns listed in ``nowrap`` never break inside a word or value.
    """
    head = ""
    for i, h in enumerate(headers):
        width = f' width="{widths[i]}"' if widths else ""
        head += f"<th{width} nowrap>{e(h)}</th>"
    body = []
    for row in rows:
        cells = "".join(
            f"<td{' nowrap' if i in nowrap else ''}>{c if raw else e(c)}</td>" for i, c in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    return f"<table {TABLE}><tr>{head}</tr>{''.join(body)}</table>"


def _kv_table(pairs: List[Tuple[str, str]]) -> str:
    rows = "".join(
        f'<tr><td width="28%" bgcolor="#eef2f7"><b>{e(k)}</b></td><td>{v}</td></tr>' for k, v in pairs
    )
    return f"<table {TABLE}>{rows}</table>"


def _page_break() -> str:
    return '<div style="page-break-before: always;"></div>'


# --------------------------------------------------------------- analysis ---


def frame_offsets(frame: FrameDefinition) -> List[Tuple[FieldDefinition, Optional[int], Optional[int]]]:
    """(field, offset, size) with None where the position depends on a variable field."""
    out = []
    offset: Optional[int] = 0
    for f in frame.fields:
        try:
            size = fixed_size(f)
        except DefinitionError:
            size = None
        out.append((f, offset, size))
        offset = offset + size if (offset is not None and size is not None) else None
    return out


def frame_size_text(frame: FrameDefinition) -> str:
    try:
        sizes = [fixed_size(f) for f in frame.fields]
    except DefinitionError:
        return "invalid"
    fixed = sum(s or 0 for s in sizes)
    return f"{fixed} bytes" if all(s is not None for s in sizes) else f"{fixed} bytes + variable"


def _field_name(frame: FrameDefinition, index: int) -> str:
    return frame.fields[index].name if 0 <= index < len(frame.fields) else "?"


def _range_text(frame: FrameDefinition, start: int, end: int) -> str:
    if start > end:
        return "no fields"
    if start == end:
        return _field_name(frame, start)
    return f"{_field_name(frame, start)} … {_field_name(frame, end)}"


def _type_text(f: FieldDefinition, size: Optional[int], protocol: ProtocolDefinition) -> str:
    """Type, plus the byte order for multi-byte numbers (BE = big endian, LE = little endian)."""
    if f.type in (FieldType.BYTES, FieldType.STRING):
        return f"{f.type.value}[{size}]" if size else f"{f.type.value}[var]"
    text = f.type.value
    if (size or 0) > 1:
        text += " " + ("LE" if field_endianness(f, protocol).value == "LITTLE" else "BE")
    return text


def _rule_text(frame: FrameDefinition, index: int, f: FieldDefinition) -> str:
    """Human readable rule for the Value / Rule column."""
    if f.encoding == Encoding.CONSTANT:
        return f"constant = {e(f.value)}"
    if f.encoding == Encoding.LENGTH:
        try:
            start, end = length_range(frame, index)
            text = f"length of {e(_range_text(frame, start, end))}"
        except KeyError:
            text = "length (invalid range)"
        if f.length_adjust:
            text += f" {f.length_adjust:+d}"
        return text
    if f.encoding == Encoding.CRC:
        try:
            alg = crc_algorithm(f).name
        except DefinitionError:
            alg = "unknown CRC"
        try:
            start, end = crc_range(frame, index)
            return f"{e(alg)} over {e(_range_text(frame, start, end))}"
        except KeyError:
            return f"{e(alg)} (invalid range)"
    parts = []
    if f.enum is not None:
        parts.append(f"enum {f.enum}" if isinstance(f.enum, str) else
                     "values " + ", ".join(f"{k}={v}" for k, v in sorted(f.enum.items())))
    if f.is_scaled:
        parts.append(f"value = raw × {format_number(f.scale)}" + (f" + {format_number(f.value_offset)}" if f.value_offset else ""))
    if f.minimum is not None or f.maximum is not None:
        parts.append(f"range {_num(f.minimum)} … {_num(f.maximum)}")
    if f.unit:
        parts.append(f"unit {f.unit}")
    if f.default is not None:
        parts.append(f"default {e(f.default)}")
    if f.is_variable and covering_length_field(frame, index) is None:
        parts.append("size = remaining bytes")
    return "; ".join(e(p) for p in parts)


# --------------------------------------------------------------- sections ---


def _title(protocol: ProtocolDefinition, source: Optional[str], contents: List[Tuple[str, str]]) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    frames = len(protocol.frames)
    rows = [
        ("Protocol", f"<b>{e(protocol.name)}</b>"),
        ("Version", e(protocol.version)),
        ("Default byte order", "Big endian (MSB first)" if protocol.endianness.value == "BIG" else "Little endian (LSB first)"),
        ("Transport", e(protocol.transport.summary())),
        ("Contents", f"{frames} message(s), {len(protocol.enums)} enumeration(s), {len(protocol.tests)} test(s), "
                     f"{len(protocol.simulator)} simulator rule(s)"),
        ("Generated", f"{now} by {e(APP_NAME)} {e(__version__)}"),
    ]
    if source:
        rows.insert(5, ("Source file", f'<span class="mono">{e(source)}</span>'))
    desc = f"<p>{e(protocol.description)}</p>" if protocol.description else ""
    toc_html = "".join(
        f'<tr><td width="8%">{e(num)}</td><td>{e(title)}</td></tr>' for num, title in contents
    )
    return (
        f'<h1>{e(protocol.name)}</h1><p class="muted">Protocol Specification · version {e(protocol.version)}</p>'
        f'{desc}{_kv_table(rows)}<h3>Contents</h3><table cellpadding="1" width="100%">{toc_html}</table>'
        "<h3>Conventions</h3><p class=\"small\">Offsets and sizes are in bytes, counted from the first byte of the "
        "frame (offset 0). Multi-byte values use the byte order shown per field. <i>raw</i> is the value on the wire; "
        "the physical value is <i>raw × scale + offset</i>. Field colours: "
        f'<span style="background-color:{ENCODING_COLORS[Encoding.CONSTANT]}">&nbsp;constant&nbsp;</span> '
        f'<span style="background-color:{ENCODING_COLORS[Encoding.LENGTH]}">&nbsp;length&nbsp;</span> '
        f'<span style="background-color:{ENCODING_COLORS[Encoding.CRC]}">&nbsp;checksum&nbsp;</span> '
        f'<span style="background-color:{ENCODING_COLORS[Encoding.VALUE]}">&nbsp;data&nbsp;</span></p>'
    )


def _transport(protocol: ProtocolDefinition) -> str:
    t = protocol.transport
    rows = [("Type", e(t.type.value)), ("Summary", e(t.summary()))]
    rows += [
        ("Serial port", e(t.port or "—")),
        ("Baud rate / format", f"{t.baudrate} baud, {t.data_bits} data bits, parity {e(t.parity)}, {e(t.stop_bits)} stop bit(s)"),
        ("Network", f"{e(t.host)}:{t.tcp_port}"),
        ("CAN", f"interface {e(t.can_interface)}, channel {e(t.can_channel)}, {t.can_bitrate} bit/s"
                f" (data phase {t.can_data_bitrate} bit/s for CAN-FD)"),
        ("Timeout", f"{t.timeout_ms} ms"),
    ]
    if t.options:
        rows.append(("Options", '<span class="mono">' + e(", ".join(f"{k}={v}" for k, v in t.options.items())) + "</span>"))
    return f"<h2>1. Transport</h2><p class=\"small muted\">The transport only carries bytes; the same frames can be " \
           f"used over any of the supported physical layers.</p>{_kv_table(rows)}"


def _overview(protocol: ProtocolDefinition) -> str:
    rows = []
    for f in protocol.frames:
        rows.append([
            f"<b>{e(f.name)}</b>",
            e(_hex(f.id)),
            e(f.direction.value),
            e(_hex(f.can_id, 3) + (" (ext)" if f.can_extended else "")) if f.can_id is not None else "",
            e(frame_size_text(f)),
            e(f.expected_response or ""),
            e(f.description),
        ])
    return "<h2>2. Message overview</h2>" + _table(
        ["Message", "ID", "Dir", "CAN ID", "Size", "Response", "Description"], rows,
        ["18%", "7%", "6%", "10%", "14%", "15%", "30%"], raw=True,
    )


def _enums(protocol: ProtocolDefinition) -> str:
    if not protocol.enums:
        return ""
    parts = ["<h2>3. Enumerations</h2>"]
    for name, enum in protocol.enums.items():
        used = [
            f"{fr.name}.{fd.name}" for fr in protocol.frames for fd in fr.fields
            if fd.enum == name or any(b.enum == name for b in fd.bits)
        ]
        parts.append(f"<h3>{e(name)}</h3>")
        if enum.description:
            parts.append(f"<p>{e(enum.description)}</p>")
        rows = [[_hex(v), str(v), label] for v, label in sorted(enum.values.items())]
        parts.append(_table(["Value (hex)", "Value (dec)", "Name"], rows, ["20%", "20%", "60%"]))
        if used:
            parts.append(f'<p class="small muted">Used by: {e(", ".join(used))}</p>')
    return "".join(parts)


def _layout_diagram(frame: FrameDefinition, offsets) -> str:
    cells = []
    for f, offset, size in offsets:
        if offset is None:
            pos = "after var."
        elif size is None:
            pos = f"byte {offset}…"
        elif size == 1:
            pos = f"byte {offset}"
        else:
            pos = f"bytes {offset}–{offset + size - 1}"
        colour = ENCODING_COLORS.get(f.encoding, "#ffffff")
        cells.append(
            f'<td bgcolor="{colour}" align="center"><b>{e(f.name)}</b><br/>'
            f'<span class="small">{e(pos)}<br/>{e(size if size is not None else "var")} B</span></td>'
        )
    rows = []
    per_row = 7
    for i in range(0, len(cells), per_row):
        chunk = cells[i : i + per_row]
        if len(cells) > per_row:  # keep the columns of a wrapped layout aligned
            chunk += ["<td></td>"] * (per_row - len(chunk))
        rows.append("<tr>" + "".join(chunk) + "</tr>")
    return f'<table border="1" cellspacing="0" cellpadding="5" width="100%" style="border-collapse: collapse; border-color: #6e7781;">{"".join(rows)}</table>'


def _frame_section(protocol: ProtocolDefinition, frame: FrameDefinition, number: str) -> str:
    offsets = frame_offsets(frame)
    parts = [_page_break(), f"<h2>{number} Message {e(frame.name)}</h2>"]
    if frame.description:
        parts.append(f"<p>{e(frame.description)}</p>")
    props = [
        ("Message ID", e(_hex(frame.id) + (f" ({frame.id})" if frame.id is not None else "")) or "—"),
        ("Direction", e(frame.direction.value)),
        ("Frame size", e(frame_size_text(frame))),
        ("Expected response", e(frame.expected_response or "—")),
    ]
    if frame.can_id is not None:
        props.append(("CAN identifier", e(f"{_hex(frame.can_id, 3)} ({'29-bit extended' if frame.can_extended else '11-bit standard'})")))
    responders = [f.name for f in protocol.frames if f.expected_response == frame.name]
    if responders:
        props.append(("Response to", e(", ".join(responders))))
    parts.append(_kv_table(props))

    parts.append("<h3>Byte layout</h3>")
    parts.append(_layout_diagram(frame, offsets))

    parts.append("<h3>Fields</h3>")
    rows = []
    for i, (f, offset, size) in enumerate(offsets):
        try:
            type_text = _type_text(f, size, protocol)
        except DefinitionError:
            type_text = f.type.value
        rows.append([
            str(i + 1),
            f'<span style="background-color:{ENCODING_COLORS.get(f.encoding, "#fff")}">&nbsp;<b>{e(f.name)}</b>&nbsp;</span>',
            e("" if offset is None else offset),
            e("var" if size is None else size),
            e(type_text),
            e(f.encoding.value),
            _rule_text(frame, i, f),
            e(f.description),
        ])
    parts.append(_table(
        ["#", "Field", "Offset", "Size", "Type", "Encoding", "Value / rule", "Description"],
        rows, ["3%", "16%", "8%", "6%", "13%", "12%", "26%", "16%"], raw=True, nowrap=(0, 2, 3, 4, 5),
    ))
    parts.append('<p class="small muted">BE = big endian (most significant byte first), '
                 "LE = little endian (least significant byte first).</p>")

    # bit fields
    for f in frame.fields:
        if not f.bits:
            continue
        parts.append(f"<h3>Bit field {e(f.name)}</h3>")
        rows = []
        for b in sorted(f.bits, key=lambda x: x.start):
            bits = f"{b.start}" if b.length == 1 else f"{b.start}–{b.start + b.length - 1}"
            try:
                mapping = resolve_enum(b.enum, protocol) if b.enum is not None else None
            except DefinitionError:
                mapping = None
            meaning = ", ".join(f"{v} = {n}" for v, n in sorted(mapping.items())) if mapping else (
                "1 = set, 0 = clear" if b.length == 1 else f"0 … {(1 << b.length) - 1}")
            rows.append([bits, f"0x{b.mask:X}", b.name, meaning, b.description])
        parts.append(_table(["Bits", "Mask", "Name", "Values", "Description"], rows,
                            ["10%", "12%", "22%", "36%", "20%"]))

    # rules for computed fields
    rules = []
    for i, f in enumerate(frame.fields):
        if f.encoding == Encoding.LENGTH:
            try:
                start, end = length_range(frame, i)
                rules.append(f"<li><b>{e(f.name)}</b> = number of bytes of {e(_range_text(frame, start, end))}"
                             + (f" {f.length_adjust:+d}" if f.length_adjust else "") + ".</li>")
            except KeyError:
                pass
        elif f.encoding == Encoding.CRC:
            try:
                alg = crc_algorithm(f)
                start, end = crc_range(frame, i)
                rules.append(f"<li><b>{e(f.name)}</b> = {e(alg.name)} computed over the bytes of "
                             f"{e(_range_text(frame, start, end))} (see section 'Checksum / CRC algorithms').</li>")
            except (DefinitionError, KeyError):
                pass
    for i, f in enumerate(frame.fields):
        if f.is_variable:
            if covering_length_field(frame, i) is not None:
                rules.append(f"<li><b>{e(f.name)}</b> has a variable size given by the LENGTH field.</li>")
            else:
                rules.append(f"<li><b>{e(f.name)}</b> has a variable size: all bytes before the trailing fixed-size fields.</li>")
    constants = [f.name for f in frame.fields if f.encoding == Encoding.CONSTANT]
    if constants:
        rules.append(f"<li>The message is recognised by its constant field(s): {e(', '.join(constants))}"
                     + (" and its CAN identifier" if frame.can_id is not None else "") + ".</li>")
    elif frame.can_id is not None:
        rules.append("<li>The message is recognised by its CAN identifier.</li>")
    if rules:
        parts.append("<h3>Rules</h3><ul>" + "".join(rules) + "</ul>")

    parts.append(_example(protocol, frame))
    return "".join(parts)


def _example(protocol: ProtocolDefinition, frame: FrameDefinition) -> str:
    try:
        encoded = Encoder(protocol).encode(frame, {})
        decoded = Decoder(protocol).decode(frame, encoded.data, check_constants=False)
    except (ProtocolError, KeyError, ValueError) as exc:
        return f'<h3>Example</h3><p class="err">No example: {e(exc)}</p>'
    rows = []
    for f in decoded.fields:
        value = f.display
        if f.bits:
            value += " (" + ", ".join(f"{b.name}={b.display}" for b in f.bits) + ")"
        rows.append([f.name, str(f.offset), f.hex or "(empty)", value])
    return (
        "<h3>Example (default values)</h3>"
        f'<p class="mono"><b>{e(encoded.hex)}</b> &nbsp;<span class="muted">({len(encoded.data)} bytes)</span></p>'
        + _table(["Field", "Offset", "Bytes", "Value"], rows, ["30%", "10%", "25%", "35%"])
    )


def _crc_section(protocol: ProtocolDefinition, number: str) -> str:
    algorithms = {}
    for frame in protocol.frames:
        for f in frame.fields:
            if f.encoding == Encoding.CRC:
                try:
                    alg = crc_algorithm(f)
                except DefinitionError:
                    continue
                algorithms.setdefault(alg.name, [alg, []])[1].append(f"{frame.name}.{f.name}")
    if not algorithms:
        return f"<h2>{number} Checksum / CRC algorithms</h2><p>This protocol has no checksum fields.</p>"
    rows = []
    for name, (alg, users) in sorted(algorithms.items()):
        if alg.custom is None:
            params = (f"width {alg.width}, poly 0x{alg.poly:X}, init 0x{alg.init:X}, refin {str(alg.refin).lower()}, "
                      f"refout {str(alg.refout).lower()}, xorout 0x{alg.xorout:X}")
        else:
            params = alg.description
        check = "" if alg.check is None else f"0x{alg.check:0{alg.size * 2}X}"
        rows.append([name, params, check, ", ".join(users)])
    return (
        f"<h2>{number} Checksum / CRC algorithms</h2>"
        '<p class="small muted">Parameters follow the Rocksoft model. "Check" is the result for the ASCII bytes '
        '"123456789", useful to verify an implementation.</p>'
        + _table(["Algorithm", "Parameters", "Check", "Used by"], rows, ["16%", "44%", "12%", "28%"])
    )


def _step_text(step) -> Tuple[str, str]:
    d = step_to_dict(step)
    action = d.pop("action")
    checks = d.pop("checks", [])
    detail = ", ".join(f"{k}={_value_text(v)}" for k, v in d.items())
    check_text = "; ".join(
        f"{c['field']} " + ", ".join(f"{k} {_value_text(v)}" for k, v in c.items() if k != "field") for c in checks
    )
    return f"{action}: {detail}", check_text


def _tests_section(protocol: ProtocolDefinition, number: str) -> str:
    if not protocol.tests:
        return ""
    parts = [f"<h2>{number} Tests</h2>"]
    for test in protocol.tests:
        parts.append(f"<h3>{e(test.name)}</h3>")
        if test.description:
            parts.append(f"<p>{e(test.description)}</p>")
        parts.append(f'<p class="small muted">Default timeout {test.timeout_ms} ms</p>')
        rows = []
        for i, step in enumerate(test.steps, 1):
            action, checks = _step_text(step)
            rows.append([str(i), action, checks])
        parts.append(_table(["#", "Step", "Checks"], rows, ["5%", "55%", "40%"]))
    return "".join(parts)


def _value_text(value: Any) -> str:
    """Values as written in the protocol file (JSON for dicts and lists)."""
    if isinstance(value, (dict, list, bool)) or value is None:
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _simulator_section(protocol: ProtocolDefinition, number: str) -> str:
    if not protocol.simulator:
        return ""
    rows = []
    for r in protocol.simulator:
        rows.append([
            r.on,
            ", ".join(f"{k}={_value_text(v)}" for k, v in r.match.items()),
            r.reply or "(no reply)",
            ", ".join(r.copy),
            ", ".join(f"{k}={_value_text(v)}" for k, v in r.values.items()),
            f"{r.delay_ms} ms" if r.delay_ms else "",
        ])
    return (
        f"<h2>{number} Device simulator rules</h2>"
        '<p class="small muted">Behaviour of the built-in simulated device; the first matching rule is used.</p>'
        + _table(["On request", "If", "Reply", "Copied fields", "Reply values", "Delay"], rows,
                 ["16%", "14%", "16%", "14%", "32%", "8%"])
    )


def _validation_section(protocol: ProtocolDefinition, number: str) -> str:
    issues = validate_protocol(protocol)
    if not issues:
        return f'<h2>{number} Validation</h2><p class="ok">✓ No problems found.</p>'
    rows = []
    for issue in issues:
        cls = "err" if issue.severity == ERROR else "warn"
        rows.append([f'<span class="{cls}">{e(issue.severity)}</span>', e(issue.location), e(issue.message)])
    return f"<h2>{number} Validation</h2>" + _table(["Severity", "Location", "Message"], rows,
                                                    ["12%", "28%", "60%"], raw=True)


def build_spec_html(protocol: ProtocolDefinition, source: Optional[str] = None) -> str:
    """The complete specification as one HTML document."""
    contents: List[Tuple[str, str]] = [("1.", "Transport"), ("2.", "Message overview")]
    n = 2
    if protocol.enums:
        n += 1
        contents.append((f"{n}.", "Enumerations"))
    n += 1
    contents.append((f"{n}.", "Messages"))
    message_numbers = []
    for i, frame in enumerate(protocol.frames, 1):
        message_numbers.append(f"{n}.{i}")
        contents.append((f"{n}.{i}", f"Message {frame.name}"))
    n += 1
    crc_number = f"{n}."
    contents.append((crc_number, "Checksum / CRC algorithms"))
    tests_number = sim_number = ""
    if protocol.tests:
        n += 1
        tests_number = f"{n}."
        contents.append((tests_number, "Tests"))
    if protocol.simulator:
        n += 1
        sim_number = f"{n}."
        contents.append((sim_number, "Device simulator rules"))
    n += 1
    validation_number = f"{n}."
    contents.append((validation_number, "Validation"))

    sections = [_title(protocol, source, contents), _page_break(), _transport(protocol), _overview(protocol),
                _enums(protocol)]
    for number, frame in zip(message_numbers, protocol.frames):
        sections.append(_frame_section(protocol, frame, number))
    sections.append(_page_break() + _crc_section(protocol, crc_number))
    if protocol.tests:
        sections.append(_tests_section(protocol, tests_number))
    if protocol.simulator:
        sections.append(_simulator_section(protocol, sim_number))
    sections.append(_validation_section(protocol, validation_number))
    return (
        f'<html><head><meta charset="utf-8"><title>{e(protocol.name)} – Protocol Specification</title>'
        f"<style>{CSS}</style></head><body>{''.join(sections)}</body></html>"
    )


def export_html(protocol: ProtocolDefinition, path: Union[str, Path], source: Optional[str] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_spec_html(protocol, source), encoding="utf-8")
    return path


_APP = None  # keeps a QGuiApplication alive when exporting from the command line


def _ensure_qt_app():
    global _APP
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        # a full QApplication (not QGuiApplication) so the GUI can reuse it in the same process
        _APP = app = QApplication([sys.argv[0] if sys.argv else "pdcli"])
    return app


def export_pdf(protocol: ProtocolDefinition, path: Union[str, Path], source: Optional[str] = None) -> Path:
    """Render the specification to an A4 PDF with a running header and page numbers."""
    _ensure_qt_app()
    from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
    from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen, QTextDocument

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(14, 12, 14, 12), QPageLayout.Unit.Millimeter)
    writer.setResolution(96)
    writer.setTitle(f"{protocol.name} – Protocol Specification")
    writer.setCreator(f"{APP_NAME} {__version__}")

    area = writer.pageLayout().paintRectPixels(96)
    header_h, footer_h = 26.0, 22.0
    body_h = area.height() - header_h - footer_h

    doc = QTextDocument()
    doc.setDocumentMargin(0)
    font = QFont("Segoe UI")
    font.setPointSizeF(9)
    doc.setDefaultFont(font)
    doc.setHtml(build_spec_html(protocol, source))
    doc.setPageSize(QSizeF(area.width(), body_h))
    pages = doc.pageCount()

    painter = QPainter()
    if not painter.begin(writer):
        raise OSError(f"cannot write '{path}'")
    small = QFont("Segoe UI")
    small.setPointSizeF(7.5)
    pen = QPen(QColor("#9aa4b2"))
    try:
        for page in range(pages):
            if page:
                writer.newPage()
            # running header
            painter.setFont(small)
            painter.setPen(QColor("#57606a"))
            painter.drawText(QRectF(0, 0, area.width(), header_h - 8), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             f"{protocol.name} v{protocol.version} — Protocol Specification")
            painter.drawText(QRectF(0, 0, area.width(), header_h - 8), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                             APP_NAME)
            painter.setPen(pen)
            painter.drawLine(0, int(header_h - 5), int(area.width()), int(header_h - 5))
            # body
            painter.save()
            painter.translate(0, header_h - page * body_h)
            doc.drawContents(painter, QRectF(0, page * body_h, area.width(), body_h))
            painter.restore()
            # footer
            y = area.height() - footer_h
            painter.setPen(pen)
            painter.drawLine(0, int(y + 4), int(area.width()), int(y + 4))
            painter.setPen(QColor("#57606a"))
            painter.drawText(QRectF(0, y + 6, area.width(), footer_h - 6), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                             f"Page {page + 1} of {pages}")
            painter.drawText(QRectF(0, y + 6, area.width(), footer_h - 6), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             _dt.date.today().isoformat())
    finally:
        painter.end()
    return path


def export(protocol: ProtocolDefinition, path: Union[str, Path], source: Optional[str] = None) -> Path:
    """Export as PDF or HTML depending on the file extension."""
    if Path(path).suffix.lower() in (".html", ".htm"):
        return export_html(protocol, path, source)
    return export_pdf(protocol, path, source)
