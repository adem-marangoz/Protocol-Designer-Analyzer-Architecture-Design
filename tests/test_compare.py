import pytest

from protocol_designer.protocol import Decoder, Encoder
from protocol_designer.protocol.compare import values_equal


@pytest.fixture
def sensor_msg(tpms):
    data = Encoder(tpms).encode(
        "SENSOR_DATA",
        {"SENSOR_ID": 5, "PRESSURE": 2.4, "TEMPERATURE": -3.5, "STATUS": "ERROR", "FLAGS": {"Battery Low": 1}},
    ).data
    return Decoder(tpms).decode_any(data)


def test_numbers(sensor_msg):
    assert values_equal(sensor_msg.field("SENSOR_ID"), 5)
    assert values_equal(sensor_msg.field("SENSOR_ID"), "0x05")
    assert not values_equal(sensor_msg.field("SENSOR_ID"), 6)
    assert values_equal(sensor_msg.field("PRESSURE"), 2.4)
    assert values_equal(sensor_msg.field("TEMPERATURE"), "-3.5")
    assert not values_equal(sensor_msg.field("SENSOR_ID"), "abc")


def test_enums(sensor_msg):
    assert values_equal(sensor_msg.field("STATUS"), "ERROR")
    assert values_equal(sensor_msg.field("STATUS"), "error")
    assert values_equal(sensor_msg.field("STATUS"), 2)
    assert not values_equal(sensor_msg.field("STATUS"), "ACTIVE")


def test_bits(sensor_msg):
    assert values_equal(sensor_msg.field("FLAGS"), {"Battery Low": 1})
    assert not values_equal(sensor_msg.field("FLAGS"), {"Battery Low": 0})
    assert not values_equal(sensor_msg.field("FLAGS"), 3)


def test_bytes_strings_bools(tpms):
    enc, dec = Encoder(tpms), Decoder(tpms)
    info = dec.decode_any(enc.encode("DEVICE_INFO", {"NAME": "ABC"}).data)
    assert values_equal(info.field("NAME"), "abc")
    cfg = dec.decode_any(enc.encode("SET_CONFIG", {"ENABLED": False}).data)
    assert values_equal(cfg.field("ENABLED"), "false")
    assert not values_equal(cfg.field("ENABLED"), True)
    assert not values_equal(cfg.field("ENABLED"), "maybe")
