import pytest

from helpers import crc_field, header, make_protocol

from protocol_designer.protocol import has_errors, validate_protocol
from protocol_designer.storage import load_protocol


def messages(p, severity=None):
    return [str(i) for i in validate_protocol(p) if severity is None or i.severity == severity]


@pytest.mark.parametrize("name", ["tpms_rs485.json", "tpms_can.json", "ecu_bootloader.json"])
def test_examples_are_valid(protocols_dir, name):
    issues = validate_protocol(load_protocol(protocols_dir / name))
    assert not has_errors(issues), [str(i) for i in issues]


def test_duplicate_names():
    p = make_protocol([
        {"name": "A", "fields": [{"name": "X", "type": "UINT8"}, {"name": "X", "type": "UINT8"}]},
        {"name": "A", "fields": [{"name": "Y", "type": "UINT8"}]},
    ])
    errs = messages(p, "ERROR")
    assert any("duplicate frame name" in e for e in errs)
    assert any("duplicate field name" in e for e in errs)


def test_offset_mismatch():
    p = make_protocol([{"name": "A", "fields": [{"name": "X", "type": "UINT16", "offset": 0},
                                                 {"name": "Y", "type": "UINT8", "offset": 1}]}])
    assert any("declared offset 1" in e for e in messages(p, "ERROR"))


def test_bad_constant_and_enum_and_crc():
    p = make_protocol([{"name": "A", "fields": [
        {"name": "C", "type": "UINT8", "encoding": "CONSTANT", "value": "0x1FF"},
        {"name": "E", "type": "ENUM", "enum": "MISSING"},
        {"name": "CRC", "type": "UINT8", "encoding": "CRC", "crc": "CRC16_MODBUS"},
    ]}])
    errs = messages(p, "ERROR")
    assert any("invalid constant" in e for e in errs)
    assert any("unknown enumeration" in e for e in errs)
    assert any("CRC16_MODBUS" in e for e in errs)


def test_bitfield_errors():
    p = make_protocol([{"name": "A", "fields": [{"name": "B", "type": "BITFIELD", "bits": [
        {"name": "X", "start": 0, "length": 3}, {"name": "Y", "start": 2}, {"name": "Z", "start": 8}]}]}])
    errs = messages(p, "ERROR")
    assert any("overlaps" in e for e in errs)
    assert any("outside 8-bit" in e for e in errs)


def test_two_variable_fields():
    p = make_protocol([{"name": "A", "fields": [{"name": "X", "type": "BYTES"}, {"name": "Y", "type": "STRING"}]}])
    assert any("only one variable-size" in e for e in messages(p, "ERROR"))


def test_variable_without_length_warns():
    p = make_protocol([{"name": "A", "fields": [{"name": "X", "type": "BYTES"}]}])
    assert any("no LENGTH field" in w for w in messages(p, "WARNING"))
    assert not has_errors(validate_protocol(p))


def test_ambiguous_frames_warning():
    p = make_protocol([
        {"name": "A", "fields": header("0x01") + [crc_field()]},
        {"name": "B", "fields": header("0x01") + [crc_field()]},
    ])
    assert any("cannot be distinguished" in w for w in messages(p, "WARNING"))


def test_crc_range_errors():
    p = make_protocol([{"name": "A", "fields": [
        {"name": "CRC", "type": "UINT8", "encoding": "CRC8"},
        {"name": "X", "type": "UINT8"},
        {"name": "CRC2", "type": "UINT8", "encoding": "CRC8", "crc_from": "NOPE"},
    ]}])
    errs = messages(p, "ERROR")
    assert any("covers no bytes" in e for e in errs)
    assert any("NOPE" in e for e in errs)


def test_min_max_and_scale():
    p = make_protocol([{"name": "A", "fields": [{"name": "X", "type": "UINT8", "min": 5, "max": 1, "scale": 0}]}])
    errs = messages(p, "ERROR")
    assert any("minimum is greater" in e for e in errs)
    assert any("scale cannot be 0" in e for e in errs)


def test_tests_and_simulator_references():
    p = make_protocol(
        [{"name": "A", "fields": [{"name": "X", "type": "UINT8"}]}],
        tests=[{"name": "T", "steps": [
            {"action": "send", "message": "NOPE"},
            {"action": "send", "message": "A", "values": {"Y": 1}},
            {"action": "fly"},
            {"action": "transact", "message": "A"},
        ]}],
        simulator=[{"on": "A", "reply": "NOPE"}, {"on": "A", "reply": "A", "values": {"Q": 1}}],
    )
    errs = messages(p, "ERROR")
    assert any("unknown message 'NOPE'" in e for e in errs)
    assert any("has no field 'Y'" in e for e in errs)
    assert any("unknown action" in e for e in errs)
    assert any("no expected response" in e for e in errs)
    assert any("unknown reply" in e for e in errs)
    assert any("has no field 'Q'" in e for e in errs)


def test_empty_protocol():
    p = make_protocol([])
    assert any("no frames" in w for w in messages(p, "WARNING"))
    p = make_protocol([{"name": "A", "fields": []}])
    assert any("no fields" in e for e in messages(p, "ERROR"))
