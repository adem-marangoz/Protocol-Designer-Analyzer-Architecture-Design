"""Comparison of decoded field values with user written expectations."""

from __future__ import annotations

from typing import Any

from .values import parse_bool, parse_hex_bytes, parse_number


def values_equal(field, expected: Any) -> bool:
    """Compare a decoded field with a user written value (number, enum name, hex...)."""
    actual = field.value
    if isinstance(actual, bool):
        try:
            return actual == parse_bool(expected)
        except ValueError:
            return False
    if isinstance(actual, str):
        if isinstance(expected, str) and actual.upper() == expected.strip().upper():
            return True
        # enum field: compare with the raw number
        if isinstance(field.raw, int):
            try:
                return field.raw == parse_number(expected)
            except ValueError:
                return False
        return False
    if isinstance(actual, (bytes, bytearray)):
        try:
            return bytes(actual) == parse_hex_bytes(expected)
        except ValueError:
            return False
    if isinstance(actual, dict):
        return isinstance(expected, dict) and all(actual.get(k) == v for k, v in expected.items())
    try:
        number = parse_number(expected)
    except ValueError:
        return False
    if isinstance(actual, float) or isinstance(number, float):
        return abs(float(actual) - float(number)) <= 1e-9 * max(1.0, abs(float(number)))
    return actual == number
