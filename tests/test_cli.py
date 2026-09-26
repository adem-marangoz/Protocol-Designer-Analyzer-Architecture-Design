import json

import pytest

from protocol_designer.cli import main


@pytest.fixture
def tpms_file(protocols_dir):
    return str(protocols_dir / "tpms_rs485.json")


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_validate(capsys, tpms_file):
    code, out, _ = run(capsys, "validate", tpms_file)
    assert code == 0 and "0 error(s)" in out


def test_validate_errors(capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"frames": [{"name": "A", "fields": []}]}))
    code, out, _ = run(capsys, "validate", str(bad))
    assert code == 1 and "no fields" in out


def test_info(capsys, tpms_file):
    code, out, _ = run(capsys, "info", tpms_file)
    assert code == 0 and "READ_SENSOR  id=0x10  TX" in out and "CRC8" in out


def test_encode_and_decode(capsys, tpms_file):
    code, out, _ = run(capsys, "encode", tpms_file, "READ_SENSOR", "ADDRESS=1", "SENSOR_ID=5")
    assert code == 0 and out.strip() == "AA 01 10 01 05 94"
    code, out, _ = run(capsys, "encode", tpms_file, "SENSOR_DATA", 'FLAGS={"Battery Low": 1}', "PRESSURE=24", "--raw")
    assert code == 0
    code, out, _ = run(capsys, "decode", tpms_file, "AA 01 10 01 05 94")
    assert code == 0 and "READ_SENSOR" in out and "CRC: VALID" in out
    code, out, _ = run(capsys, "decode", tpms_file, "AA 01 10 01 05 00")
    assert code == 1 and "CRC: INVALID" in out
    code, out, _ = run(capsys, "decode", tpms_file, "01 02")
    assert code == 1 and "no matching frame" in out


def test_encode_errors(capsys, tpms_file):
    code, _, err = run(capsys, "encode", tpms_file, "READ_SENSOR", "SENSOR_ID=99")
    assert code == 2 and "above maximum" in err
    code, _, err = run(capsys, "encode", tpms_file, "NOPE")
    assert code == 2 and "NOPE" in err
    with pytest.raises(SystemExit):
        main(["encode", tpms_file, "READ_SENSOR", "oops"])


def test_crc(capsys):
    code, out, _ = run(capsys, "crc", "CRC16_MODBUS", "313233343536373839")
    assert code == 0 and out.strip() == "0x4B37"
    code, out, _ = run(capsys, "crc", "--list")
    assert "CRC32" in out


def test_test_command_with_reports(capsys, tmp_path, tpms_file):
    code, out, _ = run(capsys, "test", tpms_file, "--report", str(tmp_path), "--format", "json")
    assert code == 0 and "3/3 test(s) passed" in out
    assert len(list(tmp_path.glob("*.json"))) == 3
    code, out, _ = run(capsys, "test", tpms_file, "--test", "Sensor Read")
    assert code == 0 and "1/1" in out


def test_test_command_failure(capsys, tpms_file):
    code, out, _ = run(capsys, "test", tpms_file, "--transport", "LOOPBACK", "--test", "Sensor Read")
    assert code == 1 and "RESULT: FAIL" in out


def test_generate(capsys, tmp_path, tpms_file):
    code, out, _ = run(capsys, "generate", tpms_file, "--lang", "c", "--out", str(tmp_path), "--name", "tpms")
    assert code == 0
    assert (tmp_path / "tpms.h").exists() and (tmp_path / "tpms.c").exists()


def test_monitor_with_send(capsys, tpms_file):
    code, out, _ = run(capsys, "monitor", tpms_file, "--seconds", "0.3", "--send", "READ_SENSOR:SENSOR_ID=5", "-v")
    assert code == 0
    assert "TX" in out and "SENSOR_DATA" in out and "PRESSURE" in out


def test_ports_and_examples(capsys):
    code, out, _ = run(capsys, "ports")
    assert code == 0 and "virtual" in out
    code, out, _ = run(capsys, "examples")
    assert code == 0 and "example(s) copied" in out


def test_missing_file(capsys, tmp_path):
    code, _, err = run(capsys, "validate", str(tmp_path / "none.json"))
    assert code == 2 and "cannot read" in err
