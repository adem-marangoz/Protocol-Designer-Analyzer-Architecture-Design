import pytest

from helpers import crc_field, header, make_protocol

from protocol_designer.protocol import Encoder
from protocol_designer.protocol.crc import compute
from protocol_designer.protocol.errors import EncodeError


def test_doc_read_sensor_packet(tpms):
    # Section 14: Address 1, Sensor ID 5 -> AA 01 10 01 05 XX
    enc = Encoder(tpms).encode("READ_SENSOR", {"ADDRESS": 1, "SENSOR_ID": 5})
    assert enc.hex == "AA 01 10 01 05 94"
    assert enc.values["LENGTH"] == 1
    assert enc.values["CRC"] == compute("CRC8", bytes.fromhex("AA01100105"))
    assert enc.field_bytes("SENSOR_ID") == b"\x05"
    assert [s.offset for s in enc.spans] == [0, 1, 2, 3, 4, 5]


def test_length_is_payload_size(tpms):
    enc = Encoder(tpms).encode("SET_CONFIG", {"SENSOR_ID": 1, "PERIOD": 250, "ALARM_PRESSURE": 2.0, "ENABLED": False})
    # payload: SENSOR_ID(1) + PERIOD(2) + ALARM(2) + ENABLED(1) = 6
    assert enc.data[3] == 6
    assert enc.data[-1] == compute("CRC8", enc.data[:-1])


def test_defaults_are_used(tpms):
    enc = Encoder(tpms).encode("SET_CONFIG", {"SENSOR_ID": 3})
    assert enc.data[1] == 1  # ADDRESS default
    assert enc.field_bytes("PERIOD") == (1000).to_bytes(2, "big")
    assert enc.field_bytes("ALARM_PRESSURE") == (18).to_bytes(2, "big")
    assert enc.field_bytes("ENABLED") == b"\x01"


def test_strict_mode_requires_values(tpms):
    with pytest.raises(EncodeError, match="missing value"):
        Encoder(tpms).encode("READ_SENSOR", {"ADDRESS": 1}, strict=True)


def test_unknown_field_rejected(tpms):
    with pytest.raises(EncodeError, match="no field"):
        Encoder(tpms).encode("READ_SENSOR", {"SENSOR": 1})


def test_unknown_frame(tpms):
    with pytest.raises(KeyError):
        Encoder(tpms).encode("NOPE", {})


def test_raw_mode_skips_scaling(tpms):
    enc = Encoder(tpms).encode("SENSOR_DATA", {"PRESSURE": 194}, raw=True)
    assert enc.field_bytes("PRESSURE") == (194).to_bytes(2, "big")


def test_crc_and_length_override_for_fault_injection(tpms):
    enc = Encoder(tpms).encode("READ_SENSOR", {"SENSOR_ID": 5, "CRC": 0x00, "LENGTH": 9})
    assert enc.hex == "AA 01 10 09 05 00"


def test_variable_payload_with_length():
    p = make_protocol([
        {"name": "DATA", "fields": header("0x40") + [{"name": "PAYLOAD", "type": "BYTES"}, crc_field()]}
    ])
    enc = Encoder(p).encode("DATA", {"ADDRESS": 2, "PAYLOAD": "12 34 56 78"})
    # Section 6 example: AA 01 10 04 12 34 56 78 XX
    assert enc.hex.startswith("AA 02 40 04 12 34 56 78")
    empty = Encoder(p).encode("DATA", {"ADDRESS": 2, "PAYLOAD": ""})
    assert empty.data[3] == 0 and len(empty.data) == 5


def test_crc16_little_endian_and_custom_range():
    p = make_protocol([
        {
            "name": "M",
            "fields": [
                {"name": "SOF", "type": "UINT8", "encoding": "CONSTANT", "value": "0x55"},
                {"name": "CMD", "type": "UINT8", "encoding": "CONSTANT", "value": 1},
                {"name": "VAL", "type": "UINT16"},
                {"name": "CRC", "type": "UINT16", "encoding": "CRC", "crc": "CRC16_MODBUS",
                 "endianness": "LITTLE", "crc_from": "CMD"},
            ],
        }
    ])
    enc = Encoder(p).encode("M", {"VAL": 0x1234})
    expected = compute("CRC16_MODBUS", bytes.fromhex("011234"))
    assert enc.data[-2:] == expected.to_bytes(2, "little")


def test_length_adjust_and_explicit_range():
    p = make_protocol([
        {
            "name": "M",
            "fields": [
                {"name": "LEN", "type": "UINT16", "encoding": "LENGTH", "length_from": "A", "length_to": "CRC",
                 "length_adjust": 2},
                {"name": "A", "type": "UINT8"},
                {"name": "B", "type": "UINT32"},
                crc_field(),
            ],
        }
    ])
    enc = Encoder(p).encode("M", {"A": 1, "B": 2})
    assert int.from_bytes(enc.data[:2], "big") == 1 + 4 + 1 + 2


def test_multiple_crcs():
    p = make_protocol([
        {
            "name": "M",
            "fields": [
                {"name": "H", "type": "UINT16"},
                {"name": "HCRC", "type": "UINT8", "encoding": "CRC8"},
                {"name": "D", "type": "UINT16"},
                {"name": "DCRC", "type": "UINT8", "encoding": "CRC8", "crc_from": "D"},
            ],
        }
    ])
    enc = Encoder(p).encode("M", {"H": 0x0102, "D": 0x0304})
    assert enc.data[2] == compute("CRC8", b"\x01\x02")
    assert enc.data[5] == compute("CRC8", b"\x03\x04")


def test_scaled_value_out_of_raw_range():
    p = make_protocol([{"name": "M", "fields": [{"name": "P", "type": "UINT8", "scale": 0.1}]}])
    with pytest.raises(EncodeError):
        Encoder(p).encode("M", {"P": 30})  # raw 300 > 255


def test_invalid_value_text():
    p = make_protocol([{"name": "M", "fields": [{"name": "P", "type": "UINT8"}]}])
    with pytest.raises(EncodeError):
        Encoder(p).encode("M", {"P": "abc"})
    with pytest.raises(EncodeError):
        Encoder(p).encode("M", {"P": 1.5})
