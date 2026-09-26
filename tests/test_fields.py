import pytest

from helpers import make_protocol

from protocol_designer.protocol import Encoder, Decoder
from protocol_designer.protocol.errors import DefinitionError, EncodeError
from protocol_designer.protocol.fields import fixed_size, scale_decimals
from protocol_designer.protocol.model import FieldDefinition, FieldType, Encoding


def one_field_protocol(field, endianness="BIG", enums=None):
    return make_protocol([{"name": "F", "fields": [field]}], enums=enums, endianness=endianness)


def roundtrip(field, value, expected_hex, endianness="BIG", enums=None, **decode_kw):
    p = one_field_protocol(field, endianness, enums)
    data = Encoder(p).encode("F", {field["name"]: value}).data
    assert data.hex().upper() == expected_hex
    msg = Decoder(p).decode("F", data)
    assert msg.valid, msg.errors
    return msg.field(field["name"])


@pytest.mark.parametrize(
    "ftype,value,big,little",
    [
        ("UINT8", 200, "C8", "C8"),
        ("UINT16", 0x1234, "1234", "3412"),
        ("UINT32", 0x12345678, "12345678", "78563412"),
        ("UINT64", 1, "0000000000000001", "0100000000000000"),
        ("INT8", -1, "FF", "FF"),
        ("INT16", -2, "FFFE", "FEFF"),
        ("INT32", -100000, "FFFE7960", "6079FEFF"),
        ("INT64", -1, "FFFFFFFFFFFFFFFF", "FFFFFFFFFFFFFFFF"),
    ],
)
def test_integer_types_both_endianness(ftype, value, big, little):
    assert roundtrip({"name": "X", "type": ftype}, value, big).value == value
    assert roundtrip({"name": "X", "type": ftype}, value, little, endianness="LITTLE").value == value


def test_field_endianness_overrides_protocol():
    f = {"name": "X", "type": "UINT16", "endianness": "LITTLE"}
    roundtrip(f, 0x0102, "0201", endianness="BIG")


@pytest.mark.parametrize("ftype,value", [("UINT8", 256), ("UINT8", -1), ("INT8", 128), ("INT16", -32769)])
def test_integer_out_of_range(ftype, value):
    p = one_field_protocol({"name": "X", "type": ftype})
    with pytest.raises(EncodeError):
        Encoder(p).encode("F", {"X": value})


def test_float32_and_float64():
    field = roundtrip({"name": "X", "type": "FLOAT32"}, 1.5, "3FC00000")
    assert field.value == 1.5
    field = roundtrip({"name": "X", "type": "FLOAT64"}, -2.25, "C002000000000000")
    assert field.value == -2.25
    field = roundtrip({"name": "X", "type": "FLOAT32"}, 1.5, "0000C03F", endianness="LITTLE")


def test_scaling_doc_example():
    # Section 10: raw 250, scale 0.1 -> 25.0 bar
    f = roundtrip({"name": "P", "type": "UINT16", "scale": 0.1, "unit": "bar"}, 25.0, "00FA")
    assert f.raw == 250
    assert f.value == pytest.approx(25.0)
    assert f.display == "25.0 bar"


def test_scaling_with_offset_and_negative():
    f = roundtrip({"name": "T", "type": "INT16", "scale": 0.5, "value_offset": -40}, -50.5, "FFEB")
    assert f.raw == -21  # (-50.5 - -40) / 0.5
    assert f.value == pytest.approx(-50.5)


def test_scaling_rounds_to_nearest_step():
    f = roundtrip({"name": "P", "type": "UINT8", "scale": 0.1}, 2.44, "18")
    assert f.value == pytest.approx(2.4)


def test_min_max_rejected_on_encode():
    p = one_field_protocol({"name": "P", "type": "UINT16", "scale": 0.1, "min": 0, "max": 50})
    with pytest.raises(EncodeError, match="above maximum"):
        Encoder(p).encode("F", {"P": 60})
    with pytest.raises(EncodeError, match="below minimum"):
        Encoder(p).encode("F", {"P": -1})


def test_enum_by_name_and_number():
    enums = {"STATUS": {"0x00": "OFF", "0x01": "ON", "0x02": "ERROR", "0x03": "NOT_AVAILABLE"}}
    field = {"name": "STATUS", "type": "ENUM", "enum": "STATUS"}
    f = roundtrip(field, "ERROR", "02", enums=enums)
    assert f.display == "ERROR"  # Section 11: show ERROR, not 0x02
    assert f.value == "ERROR"
    assert roundtrip(field, "error", "02", enums=enums).value == "ERROR"
    assert roundtrip(field, 3, "03", enums=enums).display == "NOT_AVAILABLE"


def test_enum_unknown_value_warns():
    p = one_field_protocol({"name": "S", "type": "ENUM", "enum": {"0": "OFF"}})
    msg = Decoder(p).decode("F", b"\x07")
    assert msg.valid
    assert msg.field("S").display == "UNKNOWN(0x07)"
    assert msg.warnings


def test_enum_unknown_name_rejected():
    p = one_field_protocol({"name": "S", "type": "ENUM", "enum": {"0": "OFF"}})
    with pytest.raises(EncodeError):
        Encoder(p).encode("F", {"S": "BOGUS"})


def test_inline_enum_on_integer_field():
    f = roundtrip({"name": "M", "type": "UINT16", "enum": {"0x0100": "MODE_A"}}, "MODE_A", "0100")
    assert f.display == "MODE_A"


def test_bitfield_doc_example():
    # Section 12: STATUS = 0x35 -> Active ✓, Battery ✗, RF ✓, State 2
    field = {
        "name": "STATUS",
        "type": "BITFIELD",
        "bits": [
            {"name": "Sensor Active", "bits": "0"},
            {"name": "Battery Low", "bits": "1"},
            {"name": "RF Error", "bits": "2"},
            {"name": "Sensor State", "bits": "3-4"},
            {"name": "Reserved", "bits": "5-7"},
        ],
    }
    f = roundtrip(field, 0x35, "35")
    bits = {b.name: b for b in f.bits}
    assert bits["Sensor Active"].display == "✓"
    assert bits["Battery Low"].display == "✗"
    assert bits["RF Error"].display == "✓"
    assert bits["Sensor State"].value == 2
    assert bits["Reserved"].value == 1
    assert f.display == "0x35"


def test_bitfield_from_dict_and_enum_bits():
    field = {
        "name": "FLAGS",
        "type": "BITFIELD",
        "size": 2,
        "bits": [{"name": "A", "start": 0}, {"name": "MODE", "start": 8, "length": 3, "enum": {"5": "FAST"}}],
    }
    f = roundtrip(field, {"A": True, "MODE": "FAST"}, "0501")
    assert f.value == {"A": 1, "MODE": 5}
    assert f.bits[1].display == "FAST"


def test_bitfield_value_too_large():
    p = one_field_protocol({"name": "B", "type": "BITFIELD", "bits": [{"name": "X", "start": 0, "length": 2}]})
    with pytest.raises(EncodeError):
        Encoder(p).encode("F", {"B": {"X": 4}})
    with pytest.raises(EncodeError):
        Encoder(p).encode("F", {"B": {"NOPE": 1}})


def test_boolean():
    assert roundtrip({"name": "B", "type": "BOOLEAN"}, True, "01").value is True
    assert roundtrip({"name": "B", "type": "BOOLEAN"}, "false", "00").display == "false"


def test_fixed_bytes_and_string_padding():
    assert roundtrip({"name": "D", "type": "BYTES", "size": 4}, "12 34", "12340000").value == b"\x12\x34\x00\x00"
    f = roundtrip({"name": "S", "type": "STRING", "size": 6}, "ABC", "414243000000")
    assert f.value == "ABC"
    assert f.display == '"ABC"'
    p = one_field_protocol({"name": "S", "type": "STRING", "size": 2})
    with pytest.raises(EncodeError):
        Encoder(p).encode("F", {"S": "TOO LONG"})


def test_utf8_string():
    f = roundtrip({"name": "S", "type": "STRING", "size": 4, "string_encoding": "utf-8"}, "é", "C3A90000")
    assert f.value == "é"


def test_fixed_size_rules():
    assert fixed_size(FieldDefinition("a", FieldType.UINT32)) == 4
    assert fixed_size(FieldDefinition("a", FieldType.BYTES)) is None
    assert fixed_size(FieldDefinition("a", FieldType.ENUM, size=2)) == 2
    assert fixed_size(FieldDefinition("a", FieldType.UINT8, encoding=Encoding.CRC, crc="CRC16_MODBUS", size=None)) == 2
    with pytest.raises(DefinitionError):
        fixed_size(FieldDefinition("a", FieldType.UINT16, size=3))
    with pytest.raises(DefinitionError):
        fixed_size(FieldDefinition("a", FieldType.ENUM, size=5))


def test_scale_decimals():
    assert scale_decimals(0.1) == 1
    assert scale_decimals(0.25) == 2
    assert scale_decimals(1) == 0
    assert scale_decimals(0.001) == 3
