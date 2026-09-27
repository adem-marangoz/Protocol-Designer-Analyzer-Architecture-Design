"""Helpers for parsing and formatting user supplied values."""

from __future__ import annotations

import re
from typing import Any, Union

_HEX_CLEAN = re.compile(r"(0x|0X|[\s,:;\-_])")


def parse_int(value: Any) -> int:
    """Parse ints written as 170, "170", "0xAA", "0b1010", "0o17" or "AAh"."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"expected an integer, got {value!r}")
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace("_", "")
        if not text:
            raise ValueError("empty value")
        if text.lower().endswith("h") and re.fullmatch(r"-?[0-9a-fA-F]+[hH]", text):
            return int(text[:-1], 16)
        try:
            return int(text, 0)
        except ValueError:
            # "010" is rejected by int(x, 0); accept plain decimals with leading zeros.
            if re.fullmatch(r"-?\d+", text):
                return int(text, 10)
            raise ValueError(f"invalid integer: {value!r}") from None
    raise ValueError(f"invalid integer: {value!r}")


def parse_number(value: Any) -> Union[int, float]:
    """Parse an int or float from user input."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return int(value)
    try:
        return parse_int(value)
    except ValueError:
        pass
    try:
        return float(str(value).strip())
    except ValueError:
        raise ValueError(f"invalid number: {value!r}") from None


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "y", "t", "✓"):
        return True
    if text in ("0", "false", "no", "off", "n", "f", "", "✗"):
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def parse_hex_bytes(value: Any) -> bytes:
    """Parse "AA 01 10", "0xAA,0x01", "AA0110" or a list of ints into bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, (list, tuple)):
        ints = [parse_int(v) for v in value]
        if any(not 0 <= v <= 255 for v in ints):
            raise ValueError("byte values must be between 0 and 255")
        return bytes(ints)
    if value is None:
        return b""
    text = str(value).strip()
    if not text:
        return b""
    tokens = [t for t in re.split(r"[\s,;:\-]+", text) if t]
    if len(tokens) > 1:
        out = bytearray()
        for tok in tokens:
            tok = tok[2:] if tok.lower().startswith("0x") else tok
            if len(tok) > 2:
                out.extend(bytes.fromhex(tok if len(tok) % 2 == 0 else "0" + tok))
            else:
                out.append(int(tok, 16))
        return bytes(out)
    compact = _HEX_CLEAN.sub("", text)
    if len(compact) % 2:
        compact = "0" + compact
    try:
        return bytes.fromhex(compact)
    except ValueError:
        raise ValueError(f"invalid hex bytes: {value!r}") from None


def to_hex(data: bytes, sep: str = " ") -> str:
    return sep.join(f"{b:02X}" for b in data)


def format_number(value: Union[int, float], decimals: int = 6) -> str:
    """Format a physical value without float noise (25.000000001 -> 25)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"
