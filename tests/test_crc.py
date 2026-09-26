import pytest

from protocol_designer.protocol import crc
from protocol_designer.protocol.errors import DefinitionError

CHECK_INPUT = b"123456789"


@pytest.mark.parametrize("name", sorted(crc.ALGORITHMS))
def test_catalog_check_values(name):
    alg = crc.ALGORITHMS[name]
    assert alg.check is not None
    assert alg.compute(CHECK_INPUT) == alg.check, f"{name} check value"


def test_sizes():
    assert crc.get_algorithm("CRC8").size == 1
    assert crc.get_algorithm("CRC16_MODBUS").size == 2
    assert crc.get_algorithm("CRC32").size == 4


def test_aliases_and_normalisation():
    assert crc.get_algorithm("crc16").name == "CRC16_MODBUS"
    assert crc.get_algorithm("CRC-16/CCITT").name == "CRC16_CCITT_FALSE"
    assert crc.get_algorithm("lrc").name == "XOR8"
    assert crc.is_crc_name("crc8")
    assert not crc.is_crc_name("UINT8")


def test_unknown_algorithm():
    with pytest.raises(DefinitionError):
        crc.get_algorithm("CRC99")


def test_doc_example_packet():
    # Design document Section 13: AA 01 10 01 05 + CRC8
    assert crc.compute("CRC8", bytes.fromhex("AA01100105")) == 0x94


def test_empty_input():
    assert crc.compute("CRC8", b"") == 0
    assert crc.compute("CRC16_MODBUS", b"") == 0xFFFF


def test_custom_algorithm_matches_catalog():
    custom = crc.custom_algorithm({"width": 16, "poly": "0x1021", "init": "0xFFFF"})
    assert custom.compute(CHECK_INPUT) == 0x29B1
    small = crc.custom_algorithm({"width": 5, "poly": "0x05", "init": "0x1F", "refin": True, "refout": True, "xorout": "0x1F"})
    assert small.compute(CHECK_INPUT) == 0x19  # CRC-5/USB
    with pytest.raises(DefinitionError):
        crc.custom_algorithm({"poly": 7})
    with pytest.raises(DefinitionError):
        crc.custom_algorithm({"width": 0, "poly": 7})


def test_crc_detects_single_bit_errors():
    data = bytearray(b"\xaa\x01\x11\x07\x05\x00\xc2\x00\xfa\x01\x15")
    good = crc.compute("CRC16_MODBUS", data)
    for i in range(len(data) * 8):
        corrupted = bytearray(data)
        corrupted[i // 8] ^= 1 << (i % 8)
        assert crc.compute("CRC16_MODBUS", corrupted) != good
