import pytest

from helpers import crc_field, header, make_protocol

from protocol_designer.protocol import Decoder, Encoder
from protocol_designer.protocol.errors import DecodeError, FrameMismatch


def test_doc_decode_read_sensor(tpms):
    # Section 13: AA 01 10 01 05 <crc> -> READ_SENSOR, address 1, sensor 5, CRC valid
    msg = Decoder(tpms).decode_any(bytes.fromhex("AA0110010594"))
    assert msg.name == "READ_SENSOR"
    assert msg.valid
    assert msg.field("ADDRESS").value == 1
    assert msg.field("SENSOR_ID").value == 5
    assert msg.crc_status == "VALID"
    assert msg.length_valid is True


def test_doc_live_monitor_sensor_data(tpms):
    data = Encoder(tpms).encode(
        "SENSOR_DATA",
        {"ADDRESS": 1, "SENSOR_ID": 5, "PRESSURE": 19.4, "TEMPERATURE": 25.0, "STATUS": "ACTIVE"},
    ).data
    msg = Decoder(tpms).decode_any(data)
    assert msg.name == "SENSOR_DATA"
    assert msg.field("PRESSURE").value == pytest.approx(19.4)
    assert msg.field("PRESSURE").display == "19.4 bar"
    assert msg.field("TEMPERATURE").display == "25.0 °C"
    assert msg.field("STATUS").display == "ACTIVE"
    text = msg.summary()
    assert "SENSOR_DATA" in text and "VALID" in text


def test_invalid_crc_is_reported_not_raised(tpms):
    msg = Decoder(tpms).decode("READ_SENSOR", bytes.fromhex("AA0110010500"))
    assert msg.crc_valid is False
    assert not msg.valid
    assert msg.crc_status == "INVALID"
    assert msg.field("CRC").error == "expected 0x94"
    assert any("CRC" in e for e in msg.errors)


def test_wrong_length_is_reported(tpms):
    data = Encoder(tpms).encode("READ_SENSOR", {"SENSOR_ID": 5, "LENGTH": 3}).data
    msg = Decoder(tpms).decode("READ_SENSOR", data)
    assert msg.length_valid is False
    assert not msg.valid


def test_constant_mismatch_raises(tpms):
    with pytest.raises(FrameMismatch):
        Decoder(tpms).decode("READ_SENSOR", bytes.fromhex("AB0110010594"))


def test_force_decode_without_constant_check(tpms):
    msg = Decoder(tpms).decode("READ_SENSOR", bytes.fromhex("AB0110010594"), check_constants=False)
    assert not msg.valid
    assert msg.field("SOF").valid is False


def test_too_short(tpms):
    with pytest.raises(DecodeError):
        Decoder(tpms).decode("READ_SENSOR", bytes.fromhex("AA0110"))


def test_trailing_bytes_reported(tpms):
    msg = Decoder(tpms).decode("READ_SENSOR", bytes.fromhex("AA0110010594FF"))
    assert not msg.valid
    assert "trailing" in msg.errors[0]


def test_identify_all_frames_roundtrip(tpms):
    enc = Encoder(tpms)
    dec = Decoder(tpms)
    for frame in tpms.frames:
        data = enc.encode(frame.name, {}).data
        msg = dec.decode_any(data)
        assert msg.name == frame.name, frame.name
        assert msg.valid, (frame.name, msg.errors)


def test_unknown_bytes(tpms):
    msg = Decoder(tpms).decode_any(b"\x01\x02\x03")
    assert msg.name == "UNKNOWN"
    assert not msg.valid
    assert msg.frame is None


def test_identify_prefers_valid_but_returns_best_invalid(tpms):
    msg = Decoder(tpms).identify(bytes.fromhex("AA0110010500"))
    assert msg is not None and msg.name == "READ_SENSOR" and msg.crc_valid is False


def test_variable_string(tpms):
    data = Encoder(tpms).encode("DEVICE_INFO", {"FW_MAJOR": 2, "FW_MINOR": 1, "SERIAL": 0x12345678, "NAME": "GATEWAY-01"}).data
    msg = Decoder(tpms).decode_any(data)
    assert msg.name == "DEVICE_INFO"
    assert msg.field("NAME").value == "GATEWAY-01"
    assert msg.field("SERIAL").raw_bytes == bytes.fromhex("78563412")
    assert msg.field("SERIAL").value == 0x12345678


def test_variable_without_length_field_uses_remaining_bytes():
    p = make_protocol([
        {"name": "M", "fields": [{"name": "ID", "type": "UINT8", "encoding": "CONSTANT", "value": 7},
                                  {"name": "DATA", "type": "BYTES"},
                                  {"name": "END", "type": "UINT8", "encoding": "CONSTANT", "value": "0xFF"}]}
    ])
    msg = Decoder(p).decode("M", bytes.fromhex("07010203FF"))
    assert msg.valid
    assert msg.field("DATA").value == b"\x01\x02\x03"


def test_range_violation_is_a_warning(tpms):
    data = Encoder(tpms).encode("SENSOR_DATA", {"PRESSURE": 600}, raw=True).data
    msg = Decoder(tpms).decode_any(data)
    assert msg.valid  # structurally valid
    assert msg.warnings and "maximum" in msg.warnings[0]
    assert msg.field("PRESSURE").error


def test_values_property_and_field_lookup(tpms):
    msg = Decoder(tpms).decode_any(bytes.fromhex("AA0110010594"))
    assert msg.values["SENSOR_ID"] == 5
    assert msg.has_field("CRC")
    with pytest.raises(KeyError):
        msg.field("NOPE")


def test_length_field_smaller_than_fixed_part():
    p = make_protocol([
        {"name": "M", "fields": header("0x01") + [{"name": "A", "type": "UINT16"}, {"name": "V", "type": "BYTES"}, crc_field()]}
    ])
    with pytest.raises(FrameMismatch):
        Decoder(p).decode("M", bytes.fromhex("AA0001010000FF"))
