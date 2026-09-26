"""Command line interface (``pdcli`` / ``protocol-designer-cli``).

Everything the GUI does is available for scripts and CI pipelines::

    pdcli validate  protocols/tpms_rs485.json
    pdcli encode    protocols/tpms_rs485.json READ_SENSOR ADDRESS=1 SENSOR_ID=5
    pdcli decode    protocols/tpms_rs485.json "AA 01 10 01 05 94"
    pdcli test      protocols/tpms_rs485.json --report results/
    pdcli generate  protocols/tpms_rs485.json --lang c --out generated/
    pdcli monitor   protocols/tpms_rs485.json --transport RS485 --port COM3 --seconds 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from . import APP_NAME, __version__, paths
from .application.codegen import LANGUAGES, generate, write_files
from .application.logger import TrafficEntry
from .application.packet_analyzer import PacketAnalyzer
from .application.packet_builder import PacketBuilder
from .application.session import Session
from .application.test_engine import TestEngine
from .protocol import crc as crc_mod
from .protocol.errors import ProtocolError
from .protocol.fields import fixed_size
from .protocol.model import ProtocolDefinition, TransportType
from .protocol.validator import validate_protocol
from .protocol.values import parse_hex_bytes, parse_int
from .storage import load_protocol
from .transport import TransportError, list_can_interfaces, list_serial_ports


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _parse_assignments(items: Sequence[str]) -> dict:
    values = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"error: expected FIELD=VALUE, got '{item}'")
        key, value = item.split("=", 1)
        text = value.strip()
        if text.startswith("{"):
            try:
                values[key.strip()] = json.loads(text)
                continue
            except ValueError:
                pass
        values[key.strip()] = text
    return values


def _apply_transport_overrides(protocol: ProtocolDefinition, args) -> None:
    t = protocol.transport
    if getattr(args, "transport", None):
        t.type = TransportType(args.transport.upper())
    for attr, name in (("port", "port"), ("baud", "baudrate"), ("host", "host"), ("tcp_port", "tcp_port"),
                       ("can_interface", "can_interface"), ("can_channel", "can_channel"),
                       ("can_bitrate", "can_bitrate")):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(t, name, value)


def _add_transport_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("transport overrides")
    g.add_argument("--transport", choices=[t.value for t in TransportType], type=str.upper)
    g.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyUSB0")
    g.add_argument("--baud", type=int)
    g.add_argument("--host")
    g.add_argument("--tcp-port", type=int, dest="tcp_port")
    g.add_argument("--can-interface", dest="can_interface")
    g.add_argument("--can-channel", dest="can_channel")
    g.add_argument("--can-bitrate", type=int, dest="can_bitrate")


# ------------------------------------------------------------------ commands ---


def cmd_validate(args) -> int:
    protocol = load_protocol(args.file)
    issues = validate_protocol(protocol)
    for issue in issues:
        _out(str(issue))
    errors = sum(1 for i in issues if i.severity == "ERROR")
    _out(f"{protocol.name} v{protocol.version}: {len(protocol.frames)} message(s), "
         f"{errors} error(s), {len(issues) - errors} warning(s)")
    return 1 if errors else 0


def cmd_info(args) -> int:
    p = load_protocol(args.file)
    _out(f"{p.name} v{p.version}  ({p.endianness.value} endian)")
    if p.description:
        _out(p.description)
    _out(f"Transport: {p.transport.summary()}")
    for frame in p.frames:
        ident = f"0x{frame.id:02X}" if frame.id is not None else "-"
        can = f"  CAN 0x{frame.can_id:X}" if frame.can_id is not None else ""
        _out(f"\n{frame.name}  id={ident}  {frame.direction.value}{can}")
        offset: Optional[int] = 0
        for f in frame.fields:
            size = fixed_size(f)
            extra = f.encoding.value if f.encoding.value != "VALUE" else ""
            if f.crc:
                extra += f" {f.crc if isinstance(f.crc, str) else 'custom'}"
            if f.is_scaled:
                extra += f" scale {f.scale:g}"
            pos = "" if offset is None else str(offset)
            _out(f"  {f.name:<16}{pos:>4}{('var' if size is None else str(size)):>5}  {f.type.value:<9}{extra}")
            offset = offset + size if (offset is not None and size is not None) else None
    if p.tests:
        _out("\nTests: " + ", ".join(t.name for t in p.tests))
    return 0


def cmd_encode(args) -> int:
    p = load_protocol(args.file)
    builder = PacketBuilder(p)
    values = _parse_assignments(args.values)
    if args.raw:
        encoded = builder.encoder.encode(args.message, values, raw=True)
    else:
        encoded = builder.build(args.message, values)
    _out(encoded.hex)
    return 0


def cmd_decode(args) -> int:
    p = load_protocol(args.file)
    can_id = parse_int(args.can_id) if args.can_id else None
    events = PacketAnalyzer(p).analyze(args.hex, frame=args.message, can_id=can_id)
    ok = True
    for event in events:
        if event.kind == "garbage":
            _out(f"?? {event.hex}  (no matching frame)")
            ok = False
            continue
        msg = event.message
        _out(f"{event.hex}")
        _out(msg.summary())
        _out(f"  CRC: {msg.crc_status}")
        ok = ok and msg.valid
        _out()
    return 0 if ok and events else 1


def cmd_crc(args) -> int:
    if args.list or not args.algorithm:
        for name, alg in sorted(crc_mod.ALGORITHMS.items()):
            _out(f"{name:<18} {alg.width:>2}-bit  {alg.description}")
        return 0
    alg = crc_mod.get_algorithm(args.algorithm)
    value = alg.compute(parse_hex_bytes(args.hex or ""))
    _out(f"0x{value:0{alg.size * 2}X}")
    return 0


def cmd_test(args) -> int:
    p = load_protocol(args.file)
    _apply_transport_overrides(p, args)
    tests = [p.test(args.test)] if args.test else p.tests
    if not tests:
        _out("no tests defined")
        return 1
    session = Session(p)
    failed = 0
    try:
        for test in tests:
            report = TestEngine(session).run(test)
            _out(report.to_text())
            _out()
            if args.report:
                path = report.save(Path(args.report), args.format)
                _out(f"report: {path}")
            if not report.passed:
                failed += 1
    finally:
        session.disconnect()
    _out(f"{len(tests) - failed}/{len(tests)} test(s) passed")
    return 1 if failed else 0


def cmd_generate(args) -> int:
    p = load_protocol(args.file)
    files = generate(p, args.lang, basename=args.name, prefix=args.prefix)
    out_dir = Path(args.out) if args.out else paths.generated_dir()
    for path in write_files(files, out_dir):
        _out(str(path))
    return 0


def cmd_monitor(args) -> int:
    p = load_protocol(args.file)
    _apply_transport_overrides(p, args)
    session = Session(p)

    def show(entry: TrafficEntry) -> None:
        can = f" [0x{entry.can_id:X}]" if entry.can_id is not None else ""
        line = f"{entry.time_text}  {entry.direction:<5}{can} {entry.hex}  {entry.name} {entry.status} {entry.note}"
        _out(line.rstrip())
        if entry.message is not None and args.verbose:
            _out("\n".join("      " + x for x in entry.message.summary().splitlines()[1:]))
        sys.stdout.flush()

    session.traffic.subscribe(show)
    try:
        session.connect()
        for item in args.send or []:
            name, _, rest = item.partition(":")
            session.send_message(name, _parse_assignments([x for x in rest.split(",") if x]))
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline and session.connected:
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        session.disconnect()
    return 0


def cmd_ports(args) -> int:
    _out("Serial ports:")
    for port in list_serial_ports() or ["(none found)"]:
        _out(f"  {port}")
    _out("CAN interfaces (python-can): " + ", ".join(list_can_interfaces()))
    return 0


def cmd_examples(args) -> int:
    copied = paths.copy_examples(overwrite=args.overwrite)
    for path in copied:
        _out(str(path))
    _out(f"{len(copied)} example(s) copied to {paths.protocols_dir()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdcli", description=f"{APP_NAME} {__version__} – command line")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check a protocol definition")
    p.add_argument("file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("info", help="show messages and field layout")
    p.add_argument("file")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("encode", help="build a packet: MESSAGE FIELD=VALUE ...")
    p.add_argument("file")
    p.add_argument("message")
    p.add_argument("values", nargs="*", metavar="FIELD=VALUE")
    p.add_argument("--raw", action="store_true", help="values are raw register values (no scaling)")
    p.set_defaults(func=cmd_encode)

    p = sub.add_parser("decode", help="decode hex bytes (one or more frames)")
    p.add_argument("file")
    p.add_argument("hex")
    p.add_argument("--message", help="force decoding as this message")
    p.add_argument("--can-id", dest="can_id", help="bytes are one CAN payload with this id")
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("crc", help="compute a checksum: ALGORITHM HEX (or --list)")
    p.add_argument("algorithm", nargs="?")
    p.add_argument("hex", nargs="?")
    p.add_argument("--list", action="store_true")
    p.set_defaults(func=cmd_crc)

    p = sub.add_parser("test", help="run the protocol's tests (exit code 1 on failure)")
    p.add_argument("file")
    p.add_argument("--test", help="run only this test")
    p.add_argument("--report", metavar="DIR", help="save reports into DIR")
    p.add_argument("--format", choices=["html", "json", "txt"], default="html")
    _add_transport_args(p)
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("generate", help="generate source code")
    p.add_argument("file")
    p.add_argument("--lang", choices=list(LANGUAGES), default="c")
    p.add_argument("--out", help="output directory (default: Documents/ProtocolDesigner/Generated)")
    p.add_argument("--name", help="base file name")
    p.add_argument("--prefix", help="identifier prefix (default: protocol name)")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("monitor", help="print live traffic")
    p.add_argument("file")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--send", action="append", metavar="MSG:F=V,F=V", help="send a message after connecting")
    p.add_argument("-v", "--verbose", action="store_true", help="show decoded fields")
    _add_transport_args(p)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("ports", help="list serial ports and CAN interfaces")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("examples", help="copy the example protocols to your Documents folder")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_examples)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (ProtocolError, TransportError, KeyError, ValueError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        sys.stderr.write(f"error: {message}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
