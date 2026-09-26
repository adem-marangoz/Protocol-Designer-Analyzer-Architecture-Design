import pytest

from protocol_designer.protocol.values import (
    format_number,
    parse_bool,
    parse_hex_bytes,
    parse_int,
    parse_number,
    to_hex,
)


@pytest.mark.parametrize(
    "text,expected",
    [(170, 170), ("170", 170), ("0xAA", 170), ("0XaA", 170), ("AAh", 170), ("0b1010", 10),
     ("0o17", 15), ("010", 10), ("-5", -5), ("1_000", 1000), (True, 1), (3.0, 3)],
)
def test_parse_int(text, expected):
    assert parse_int(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "1.5", 2.5, None, [1]])
def test_parse_int_rejects(bad):
    with pytest.raises(ValueError):
        parse_int(bad)


def test_parse_number():
    assert parse_number("25.5") == 25.5
    assert parse_number("0x10") == 16
    assert parse_number(-3) == -3
    with pytest.raises(ValueError):
        parse_number("x")


@pytest.mark.parametrize("text", ["1", "true", "YES", "on", True, 1])
def test_parse_bool_true(text):
    assert parse_bool(text) is True


@pytest.mark.parametrize("text", ["0", "false", "no", "off", False, 0, ""])
def test_parse_bool_false(text):
    assert parse_bool(text) is False


def test_parse_bool_rejects():
    with pytest.raises(ValueError):
        parse_bool("maybe")


@pytest.mark.parametrize(
    "text",
    ["AA 01 10", "aa0110", "0xAA,0x01,0x10", "AA-01-10", "AA:01:10", "  AA 1 10 ", [0xAA, 1, 0x10], b"\xaa\x01\x10"],
)
def test_parse_hex_bytes(text):
    assert parse_hex_bytes(text) == b"\xaa\x01\x10"


def test_parse_hex_bytes_edge_cases():
    assert parse_hex_bytes("") == b""
    assert parse_hex_bytes(None) == b""
    assert parse_hex_bytes("ABC") == b"\x0a\xbc"
    with pytest.raises(ValueError):
        parse_hex_bytes("zz")
    with pytest.raises(ValueError):
        parse_hex_bytes([256])


def test_to_hex_and_format_number():
    assert to_hex(b"\xaa\x01") == "AA 01"
    assert to_hex(b"\xaa\x01", "") == "AA01"
    assert format_number(25.000000001) == "25"
    assert format_number(19.4) == "19.4"
    assert format_number(-0.0) == "0"
    assert format_number(7) == "7"
