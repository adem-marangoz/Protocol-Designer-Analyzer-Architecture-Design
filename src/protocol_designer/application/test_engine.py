"""Test engine (Section 19) and sequences (Section 23).

A test is a list of steps executed against a live :class:`Session`::

    send      – build and transmit a message
    send_raw  – transmit raw hex bytes
    expect    – wait for a message and verify its fields
    transact  – send a message and verify its ``expected_response``
    delay     – wait
    set       – assign a variable (supports ${var} and arithmetic)
    log       – write a note to the report

Every check produces a ✓/✗ line; the test passes when all checks pass.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import __version__, paths
from ..protocol.compare import values_equal
from ..protocol.decoder import DecodedMessage
from ..protocol.errors import ProtocolError
from ..protocol.model import CheckDefinition, ProtocolDefinition, TestDefinition, TestStep
from ..protocol.values import format_number, parse_hex_bytes, parse_number
from ..transport.base import TransportError
from .expressions import ExpressionError, evaluate, substitute
from .logger import RX, TrafficEntry
from .session import Session

PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"
ABORTED = "ABORTED"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    @property
    def mark(self) -> str:
        return "✓" if self.passed else "✗"


@dataclass
class StepResult:
    index: int
    action: str
    description: str
    checks: List[CheckResult] = field(default_factory=list)
    error: str = ""
    duration_ms: float = 0.0
    tx: str = ""
    rx: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)


@dataclass
class TestReport:
    __test__ = False  # not a pytest test class

    test: str
    protocol: str
    transport: str
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    steps: List[StepResult] = field(default_factory=list)
    connection: Optional[CheckResult] = None
    variables: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    aborted: bool = False

    @property
    def checks(self) -> List[CheckResult]:
        out = [self.connection] if self.connection else []
        for step in self.steps:
            out.extend(step.checks)
            if step.error:
                out.append(CheckResult(f"Step {step.index} {step.action}", False, step.error))
        return out

    @property
    def result(self) -> str:
        if self.aborted:
            return ABORTED
        if self.connection and not self.connection.passed:
            return ERROR
        if any(step.error for step in self.steps):
            return FAIL
        return PASS if all(c.passed for c in self.checks) else FAIL

    @property
    def passed(self) -> bool:
        return self.result == PASS

    @property
    def duration_ms(self) -> float:
        end = self.finished or time.time()
        return (end - self.started) * 1000.0

    # ------------------------------------------------------------ output ---

    def to_text(self) -> str:
        lines = [f"Test: {self.test}", f"Protocol: {self.protocol}", f"Transport: {self.transport}", ""]
        for check in self.checks:
            detail = f"  ({check.detail})" if check.detail else ""
            lines.append(f"{check.mark} {check.name}{detail}")
        for note in self.logs:
            lines.append(f"  • {note}")
        lines += ["", f"RESULT: {self.result}  ({self.duration_ms:.0f} ms)"]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test": self.test,
            "protocol": self.protocol,
            "transport": self.transport,
            "result": self.result,
            "started": _dt.datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
            "duration_ms": round(self.duration_ms, 1),
            "tool_version": __version__,
            "connection": None if not self.connection else vars(self.connection),
            "steps": [
                {
                    "index": s.index,
                    "action": s.action,
                    "description": s.description,
                    "passed": s.passed,
                    "error": s.error,
                    "duration_ms": round(s.duration_ms, 1),
                    "tx": s.tx,
                    "rx": s.rx,
                    "checks": [vars(c) for c in s.checks],
                }
                for s in self.steps
            ],
            "variables": {k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in self.variables.items()},
            "log": list(self.logs),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def to_html(self) -> str:
        colour = {"PASS": "#1a7f37", "FAIL": "#cf222e", "ERROR": "#cf222e", "ABORTED": "#9a6700"}[self.result]
        rows = []
        for step in self.steps:
            checks = "".join(
                f'<li class="{"ok" if c.passed else "bad"}">{c.mark} {html.escape(c.name)}'
                f'{" — " + html.escape(c.detail) if c.detail else ""}</li>'
                for c in step.checks
            )
            error = f'<div class="bad">{html.escape(step.error)}</div>' if step.error else ""
            traffic = "".join(
                f"<div class=mono>{label} {html.escape(v)}</div>" for label, v in (("TX", step.tx), ("RX", step.rx)) if v
            )
            rows.append(
                f"<tr><td>{step.index}</td><td>{html.escape(step.action)}</td>"
                f"<td>{html.escape(step.description)}{traffic}<ul>{checks}</ul>{error}</td>"
                f"<td>{step.duration_ms:.0f} ms</td></tr>"
            )
        conn = ""
        if self.connection:
            conn = f"<p class={'ok' if self.connection.passed else 'bad'}>{self.connection.mark} Connection — {html.escape(self.connection.detail)}</p>"
        logs = "".join(f"<li>{html.escape(n)}</li>" for n in self.logs)
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(self.test)} – {self.result}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1f2328}}
h1{{margin:0 0 4px}} .result{{font-size:22px;font-weight:700;color:{colour}}}
table{{border-collapse:collapse;width:100%;margin-top:12px}} td,th{{border:1px solid #d0d7de;padding:6px 8px;vertical-align:top;text-align:left}}
th{{background:#f6f8fa}} ul{{margin:4px 0;padding-left:18px}} .ok{{color:#1a7f37}} .bad{{color:#cf222e}}
.mono{{font-family:Consolas,monospace;font-size:12px;color:#57606a}} .meta{{color:#57606a}}
</style></head><body>
<h1>{html.escape(self.test)}</h1>
<div class="meta">Protocol: {html.escape(self.protocol)} · Transport: {html.escape(self.transport)} ·
{_dt.datetime.fromtimestamp(self.started).strftime('%Y-%m-%d %H:%M:%S')} · {self.duration_ms:.0f} ms</div>
<p class="result">RESULT: {self.result}</p>{conn}
<table><tr><th>#</th><th>Action</th><th>Details</th><th>Time</th></tr>{''.join(rows)}</table>
{'<h3>Log</h3><ul>' + logs + '</ul>' if logs else ''}
<p class="meta">Generated by Protocol Designer &amp; Analyzer {__version__}</p>
</body></html>
"""

    def save(self, directory: Optional[Path] = None, fmt: str = "html") -> Path:
        directory = Path(directory) if directory else paths.test_results_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.fromtimestamp(self.started).strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.test)
        path = directory / f"{stamp}_{safe}_{self.result}.{fmt}"
        text = {"html": self.to_html, "json": self.to_json, "txt": self.to_text}[fmt]()
        path.write_text(text, encoding="utf-8")
        return path


ProgressCallback = Callable[[str, Any], None]


class TestEngine:
    """Runs :class:`TestDefinition` objects against a :class:`Session`."""

    __test__ = False  # not a pytest test class

    def __init__(self, session: Session, progress: Optional[ProgressCallback] = None):
        self.session = session
        self.protocol: ProtocolDefinition = session.protocol
        self.progress = progress
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _emit(self, kind: str, payload: Any) -> None:
        if self.progress:
            self.progress(kind, payload)

    # ---------------------------------------------------------------- run ---

    def run(self, test: TestDefinition, variables: Optional[Dict[str, Any]] = None) -> TestReport:
        self._cancel.clear()
        report = TestReport(test.name, self.protocol.name, self.session.description)
        report.variables = dict(variables or {})
        self._emit("started", report)

        opened_here = False
        if not self.session.connected:
            try:
                self.session.connect()
                opened_here = True
                report.transport = self.session.description
                report.connection = CheckResult("Connection", True, self.session.description)
            except (TransportError, ProtocolError) as exc:
                report.connection = CheckResult("Connection", False, str(exc))
                report.finished = time.time()
                self._emit("finished", report)
                return report
        else:
            report.connection = CheckResult("Connection", True, self.session.description)

        inbox = self.session.subscribe()
        try:
            for index, step in enumerate(test.steps, 1):
                if self._cancel.is_set():
                    report.aborted = True
                    break
                result = self._run_step(index, step, test, report, inbox)
                report.steps.append(result)
                self._emit("step", result)
        finally:
            self.session.unsubscribe(inbox)
            if opened_here:
                self.session.disconnect()
            report.finished = time.time()
        self._emit("finished", report)
        return report

    def _run_step(self, index: int, step: TestStep, test: TestDefinition, report: TestReport, inbox) -> StepResult:
        start = time.monotonic()
        result = StepResult(index, step.action, _describe(step))
        try:
            action = step.action
            if action == "send":
                self._do_send(step, report, result, inbox)
            elif action == "send_raw":
                data = parse_hex_bytes(substitute(step.data, report.variables))
                _drain(inbox)
                self.session.send_raw(data)
                result.tx = _hex(data)
                result.checks.append(CheckResult("TX packet", True, _hex(data)))
            elif action == "expect":
                timeout = step.timeout_ms if step.timeout_ms is not None else test.timeout_ms
                self._do_expect(step.message, step.checks, timeout, report, result, inbox)
            elif action == "transact":
                frame = self.protocol.frame(step.message)
                if not frame.expected_response:
                    raise ProtocolError(f"message '{frame.name}' has no expected response")
                self._do_send(step, report, result, inbox)
                timeout = step.timeout_ms if step.timeout_ms is not None else test.timeout_ms
                self._do_expect(frame.expected_response, step.checks, timeout, report, result, inbox)
            elif action == "delay":
                self._sleep(step.delay_ms / 1000.0)
            elif action == "set":
                report.variables[step.variable] = evaluate(step.value, report.variables)
                report.logs.append(f"{step.variable} = {_fmt(report.variables[step.variable])}")
            elif action == "log":
                report.logs.append(str(substitute(step.text, report.variables)))
            else:
                raise ProtocolError(f"unknown action '{action}'")
        except (ProtocolError, TransportError, ExpressionError, ValueError, KeyError) as exc:
            result.error = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
        result.duration_ms = (time.monotonic() - start) * 1000.0
        return result

    def _do_send(self, step: TestStep, report: TestReport, result: StepResult, inbox) -> None:
        values = substitute(step.values, report.variables)
        _drain(inbox)  # responses must arrive after this request
        encoded = self.session.send_message(step.message, values)
        result.tx = encoded.hex
        result.checks.append(CheckResult("TX packet", True, f"{step.message}: {encoded.hex}"))

    def _do_expect(
        self,
        message: str,
        checks: List[CheckDefinition],
        timeout_ms: int,
        report: TestReport,
        result: StepResult,
        inbox,
    ) -> None:
        msg = self._wait_for(message, timeout_ms, inbox)
        if msg is None:
            result.checks.append(CheckResult("RX packet", False, f"no {message} within {timeout_ms} ms"))
            return
        result.rx = msg.hex
        result.checks.append(CheckResult("RX packet", True, f"{message}: {msg.hex}"))
        if msg.crc_valid is not None:
            result.checks.append(CheckResult("CRC", bool(msg.crc_valid), msg.crc_status))
        structural = [e for e in msg.errors if "CRC" not in e]
        if structural:
            result.checks.append(CheckResult("Frame", False, "; ".join(structural)))
        for check in checks:
            result.checks.append(self._check(msg, check, report))

    def _wait_for(self, name: str, timeout_ms: int, inbox) -> Optional[DecodedMessage]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            if self._cancel.is_set():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                entry: TrafficEntry = inbox.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if entry.direction == RX and entry.message is not None and entry.message.name == name:
                return entry.message

    def _check(self, msg: DecodedMessage, check: CheckDefinition, report: TestReport) -> CheckResult:
        label = _label(check.field)
        if not msg.has_field(check.field):
            return CheckResult(label, False, f"message has no field {check.field}")
        f = msg.field(check.field)
        actual = f.raw if check.raw else f.value
        shown = f.display if not check.raw else str(f.raw)
        problems: List[str] = []
        try:
            if check.equals is not None:
                expected = substitute(check.equals, report.variables)
                ok = (actual == parse_number(expected)) if check.raw else values_equal(f, expected)
                if not ok:
                    problems.append(f"expected {_fmt(expected)}")
            if check.not_equals is not None:
                expected = substitute(check.not_equals, report.variables)
                ok = (actual == parse_number(expected)) if check.raw else values_equal(f, expected)
                if ok:
                    problems.append(f"must not be {_fmt(expected)}")
            if check.minimum is not None or check.maximum is not None:
                if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                    problems.append("not a number")
                else:
                    if check.minimum is not None and actual < check.minimum:
                        problems.append(f"below {format_number(check.minimum)}")
                    if check.maximum is not None and actual > check.maximum:
                        problems.append(f"above {format_number(check.maximum)}")
                    label = f"{label} range"
        except (ValueError, ExpressionError) as exc:
            problems.append(str(exc))
        if check.save_as:
            # bit fields are saved as their raw register value, everything else as displayed
            report.variables[check.save_as] = f.raw if (check.raw or f.bits) else f.value
        detail = shown if not problems else f"{shown}: {', '.join(problems)}"
        return CheckResult(label, not problems, detail)

    def _sleep(self, seconds: float) -> None:
        self._cancel.wait(max(seconds, 0))


def _drain(inbox) -> None:
    while True:
        try:
            inbox.get_nowait()
        except queue.Empty:
            return


def _hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def _fmt(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value > 255:
        return f"{value} (0x{value:X})"
    return str(value)


def _label(field_name: str) -> str:
    return field_name.replace("_", " ").title().replace("Id", "ID").replace("Crc", "CRC")


def _describe(step: TestStep) -> str:
    a = step.action
    if a in ("send", "transact"):
        vals = ", ".join(f"{k}={v}" for k, v in step.values.items())
        return f"{a.title()} {step.message}" + (f" ({vals})" if vals else "")
    if a == "expect":
        return f"Wait for {step.message}"
    if a == "send_raw":
        return f"Send raw {step.data}"
    if a == "delay":
        return f"Wait {step.delay_ms} ms"
    if a == "set":
        return f"{step.variable} = {step.value}"
    if a == "log":
        return step.text
    return a
