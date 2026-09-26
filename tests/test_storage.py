import json

import pytest

from protocol_designer.protocol import Decoder, Encoder
from protocol_designer.protocol.errors import DefinitionError
from protocol_designer.protocol.model import Encoding, FieldType, TransportType
from protocol_designer.storage import dumps, list_protocol_files, load_protocol, loads, protocol_to_dict, save_protocol

DOC_EXAMPLE = """
{
  "protocol": {"name": "Example RS485 Protocol", "version": "1.0"},
  "transport": {"type": "RS485", "baudrate": 115200, "data_bits": 8, "parity": "NONE", "stop_bits": 1},
  "frames": [
    {
      "name": "READ_SENSOR", "id": 16, "direction": "TX",
      "fields": [
        {"name": "SOF", "offset": 0, "size": 1, "type": "UINT8", "encoding": "CONSTANT", "value": "0xAA"},
        {"name": "ADDRESS", "offset": 1, "size": 1, "type": "UINT8"},
        {"name": "COMMAND", "offset": 2, "size": 1, "type": "UINT8", "encoding": "CONSTANT", "value": "0x10"},
        {"name": "LENGTH", "offset": 3, "size": 1, "type": "UINT8", "encoding": "AUTO"},
        {"name": "SENSOR_ID", "offset": 4, "size": 1, "type": "UINT8"},
        {"name": "CRC", "offset": 5, "size": 1, "type": "UINT8", "encoding": "CRC8"}
      ]
    }
  ]
}
"""


def test_design_document_json_loads_and_works():
    p = loads(DOC_EXAMPLE)
    assert p.name == "Example RS485 Protocol"
    assert p.transport.type == TransportType.RS485
    frame = p.frame("READ_SENSOR")
    assert frame.id == 16
    assert frame.field("LENGTH").encoding == Encoding.LENGTH
    assert frame.field("CRC").encoding == Encoding.CRC and frame.field("CRC").crc == "CRC8"
    data = Encoder(p).encode("READ_SENSOR", {"ADDRESS": 1, "SENSOR_ID": 5}).data
    assert data == bytes.fromhex("AA0110010594")
    assert Decoder(p).decode_any(data).valid


def test_roundtrip_examples(protocols_dir, tmp_path):
    for path in list_protocol_files(protocols_dir):
        original = load_protocol(path)
        out = save_protocol(original, tmp_path / path.name)
        again = load_protocol(out)
        assert protocol_to_dict(again) == protocol_to_dict(original), path.name
        assert again == original, path.name


def test_pdproj_extension(tmp_path, tpms):
    path = save_protocol(tpms, tmp_path / "project.pdproj")
    assert load_protocol(path).name == tpms.name
    assert [p.name for p in list_protocol_files(tmp_path)] == ["project.pdproj"]


def test_shorthands():
    p = loads(json.dumps({
        "protocol": {"name": "S"},
        "transport": {"type": "serial", "stop_bits": 1.5},
        "enums": [{"name": "E", "values": [{"value": 1, "name": "ONE"}]}],
        "messages": [{"name": "M", "fields": [
            {"name": "A", "type": "u16"},
            {"name": "B", "type": "BITFIELD", "bits": [{"name": "x", "bits": "4-6"}, {"name": "y", "bit": 0}]},
            {"name": "C", "type": "UINT16", "encoding": "crc-16/modbus"},
        ]}],
    }))
    assert p.transport.type == TransportType.UART
    assert p.transport.stop_bits == 1.5
    assert p.enums["E"].values == {1: "ONE"}
    m = p.frame("M")
    assert m.field("A").type == FieldType.UINT16
    assert (m.field("B").bits[0].start, m.field("B").bits[0].length) == (4, 3)
    assert m.field("C").crc == "CRC16_MODBUS"


@pytest.mark.parametrize(
    "text,match",
    [
        ("{bad json", "invalid JSON"),
        ("[]", "JSON object"),
        ('{"frames": [{"name": "M", "fields": [{"name": "A", "type": "UINT7"}]}]}', "invalid type"),
        ('{"frames": [{"name": "M", "fields": [{"name": "A", "encoding": "MAGIC"}]}]}', "unknown encoding"),
        ('{"frames": [{"name": "M", "direction": "UP", "fields": []}]}', "invalid direction"),
        ('{"format_version": 99}', "newer"),
        ('{"transport": {"type": "PIGEON"}}', "invalid transport type"),
        ('{"frames": [{"name": "M", "fields": [{"name": "A", "type": "BITFIELD", "bits": [{"name": "x", "bits": "a"}]}]}]}', "invalid bit range"),
    ],
)
def test_errors(text, match):
    with pytest.raises(DefinitionError, match=match):
        loads(text)


def test_missing_file(tmp_path):
    with pytest.raises(DefinitionError, match="cannot read"):
        load_protocol(tmp_path / "nope.json")


def test_unknown_transport_keys_preserved(tmp_path):
    p = loads('{"transport": {"type": "RS485", "rts_toggle": true}}')
    assert p.transport.options == {"rts_toggle": True}
    again = loads(dumps(p))
    assert again.transport.options == {"rts_toggle": True}


def test_atomic_save_does_not_leave_temp_files(tmp_path, tpms):
    save_protocol(tpms, tmp_path / "a.json")
    save_protocol(tpms, tmp_path / "a.json")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.json"]


def test_bom_is_accepted(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + DOC_EXAMPLE.encode())
    assert load_protocol(path).name == "Example RS485 Protocol"
