import json
import threading
import time

import pytest

from protocol_designer.application.session import Session
from protocol_designer.application.test_engine import ABORTED, ERROR, FAIL, PASS, TestEngine
from protocol_designer.protocol.model import CheckDefinition, TestDefinition, TestStep, TransportType
from protocol_designer.storage import load_protocol


def run(protocol, test, **kw):
    return TestEngine(Session(protocol), **kw).run(test)


@pytest.mark.parametrize("name", ["tpms_rs485.json", "tpms_can.json", "ecu_bootloader.json"])
def test_all_example_tests_pass(protocols_dir, name):
    protocol = load_protocol(protocols_dir / name)
    for test in protocol.tests:
        report = run(protocol, test)
        assert report.result == PASS, report.to_text()


def test_doc_style_report(tpms):
    report = run(tpms, tpms.test("Sensor Read"))
    text = report.to_text()
    for line in ["✓ Connection", "✓ TX packet", "✓ RX packet", "✓ CRC", "✓ Sensor ID", "✓ Pressure range",
                 "✓ Temperature range", "RESULT: PASS"]:
        assert line in text


def test_failing_check(tpms):
    test = TestDefinition("bad", steps=[
        TestStep("transact", "READ_SENSOR", values={"SENSOR_ID": 1},
                 checks=[CheckDefinition("PRESSURE", minimum=3.0), CheckDefinition("STATUS", equals="ERROR"),
                         CheckDefinition("SENSOR_ID", not_equals=1)]),
    ])
    report = run(tpms, test)
    assert report.result == FAIL
    failed = [c for c in report.checks if not c.passed]
    assert [c.name for c in failed] == ["Pressure range", "Status", "Sensor ID"]
    assert "below 3" in failed[0].detail
    assert "expected ERROR" in failed[1].detail


def test_timeout_when_no_reply(tpms):
    test = TestDefinition("silent", timeout_ms=100, steps=[
        TestStep("send", "READ_SENSOR"), TestStep("expect", "ACK")])
    start = time.monotonic()
    report = run(tpms, test)
    assert report.result == FAIL
    assert "no ACK within 100 ms" in report.to_text()
    assert time.monotonic() - start < 1.0


def test_crc_failure_detected_with_loopback(tpms):
    p = tpms.clone()
    p.transport.type = TransportType.LOOPBACK
    test = TestDefinition("crc", steps=[
        TestStep("send_raw", data="AA 01 10 01 05 00"),
        TestStep("expect", "READ_SENSOR", timeout_ms=300),
    ])
    report = run(p, test)
    assert report.result == FAIL
    crc = [c for c in report.checks if c.name == "CRC"][0]
    assert not crc.passed and crc.detail == "INVALID"


def test_connection_error(tpms):
    p = tpms.clone()
    p.transport.type = TransportType.UART
    p.transport.port = "/dev/nonexistent-xyz"
    report = run(p, p.tests[0])
    assert report.result == ERROR
    assert not report.connection.passed
    assert "✗ Connection" in report.to_text()


def test_variables_and_expressions(tpms):
    test = TestDefinition("vars", steps=[
        TestStep("set", variable="sensor", value="2 + 3"),
        TestStep("set", variable="mask", value="${sensor} << 4 | 0x0F"),
        TestStep("transact", "READ_SENSOR", values={"SENSOR_ID": "${sensor}"},
                 checks=[CheckDefinition("SENSOR_ID", equals="${sensor}", save_as="got")]),
        TestStep("log", text="got ${got}, mask ${mask}"),
        TestStep("set", variable="word", value="ACTIVE"),
    ])
    report = run(tpms, test)
    assert report.result == PASS, report.to_text()
    assert report.variables["mask"] == 0x5F
    assert report.variables["got"] == 5
    assert report.variables["word"] == "ACTIVE"
    assert "got 5, mask 95" in report.logs


def test_step_errors_are_reported(tpms):
    test = TestDefinition("errors", steps=[
        TestStep("send", "READ_SENSOR", values={"SENSOR_ID": "${missing}"}),
        TestStep("send", "READ_SENSOR", values={"SENSOR_ID": 99}),
        TestStep("send", "NOPE"),
        TestStep("jump"),
        TestStep("transact", "ACK"),
    ])
    report = run(tpms, test)
    assert report.result == FAIL
    errors = [s.error for s in report.steps]
    assert "undefined variable 'missing'" in errors[0]
    assert "above maximum" in errors[1]
    assert "NOPE" in errors[2]
    assert "unknown action" in errors[3]
    assert "no expected response" in errors[4]


def test_delay_and_cancel(tpms):
    test = TestDefinition("long", steps=[TestStep("delay", delay_ms=5000), TestStep("log", text="never")])
    engine = TestEngine(Session(tpms))
    threading.Timer(0.1, engine.cancel).start()
    start = time.monotonic()
    report = engine.run(test)
    assert time.monotonic() - start < 2.0
    assert report.result == ABORTED
    assert report.logs == []


def test_progress_callbacks(tpms):
    events = []
    run(tpms, tpms.test("Sensor Read"), progress=lambda kind, payload: events.append(kind))
    assert events == ["started", "step", "step", "finished"]


def test_existing_connection_is_kept_open(tpms):
    session = Session(tpms)
    session.connect()
    try:
        report = TestEngine(session).run(tpms.test("Sensor Read"))
        assert report.passed
        assert session.connected
    finally:
        session.disconnect()


def test_report_formats(tmp_path, tpms):
    report = run(tpms, tpms.test("Device Info"))
    html_path = report.save(tmp_path, "html")
    assert html_path.suffix == ".html" and "PASS" in html_path.name
    assert "RESULT: PASS" in html_path.read_text(encoding="utf-8")
    data = json.loads(report.save(tmp_path, "json").read_text(encoding="utf-8"))
    assert data["result"] == PASS and data["steps"][0]["checks"][0]["name"] == "TX packet"
    assert "RESULT: PASS" in report.save(tmp_path, "txt").read_text(encoding="utf-8")


def test_default_report_folder(tpms):
    from protocol_designer import paths

    path = run(tpms, tpms.test("Sensor Read")).save()
    assert path.parent == paths.test_results_dir()


def test_raw_checks(tpms):
    test = TestDefinition("raw", steps=[TestStep("transact", "READ_SENSOR", checks=[
        CheckDefinition("PRESSURE", equals=24, raw=True), CheckDefinition("STATUS", equals=1, raw=True)])])
    assert run(tpms, test).passed
