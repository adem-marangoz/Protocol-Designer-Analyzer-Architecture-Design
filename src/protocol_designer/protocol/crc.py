"""Checksum and CRC algorithms.

All CRCs are implemented with the parametric ("Rocksoft") model so new
algorithms can be added declaratively. Every catalogued algorithm is verified
against its standard check value (CRC of ASCII "123456789") in the tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import reduce
from typing import Callable, Dict, Optional

from .errors import DefinitionError


def _reflect(value: int, width: int) -> int:
    result = 0
    for _ in range(width):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


@dataclass(frozen=True)
class CrcAlgorithm:
    """A CRC or checksum algorithm producing a ``width``-bit result."""

    name: str
    width: int
    poly: int = 0
    init: int = 0
    refin: bool = False
    refout: bool = False
    xorout: int = 0
    check: Optional[int] = None
    description: str = ""
    custom: Optional[Callable[[bytes], int]] = None

    @property
    def size(self) -> int:
        """Size of the result in bytes."""
        return (self.width + 7) // 8

    def compute(self, data: bytes) -> int:
        if self.custom is not None:
            return self.custom(bytes(data)) & ((1 << self.width) - 1)
        return _crc_generic(bytes(data), self)

    # The table is built lazily and cached per algorithm instance.
    def table(self) -> list:
        cached = _TABLES.get(self)
        if cached is None:
            cached = _build_table(self)
            _TABLES[self] = cached
        return cached


_TABLES: Dict[CrcAlgorithm, list] = {}


def _build_table(alg: CrcAlgorithm) -> list:
    width = alg.width
    top = 1 << (width - 1)
    mask = (1 << width) - 1
    table = []
    for byte in range(256):
        if width >= 8:
            crc = byte << (width - 8)
            for _ in range(8):
                crc = ((crc << 1) ^ alg.poly) if crc & top else (crc << 1)
            table.append(crc & mask)
        else:
            table.append(0)
    return table


def _crc_generic(data: bytes, alg: CrcAlgorithm) -> int:
    width = alg.width
    mask = (1 << width) - 1
    crc = alg.init & mask
    if width >= 8:
        table = alg.table()
        shift = width - 8
        for byte in data:
            if alg.refin:
                byte = _reflect(byte, 8)
            crc = ((crc << 8) & mask) ^ table[((crc >> shift) ^ byte) & 0xFF]
    else:
        top = 1 << (width - 1)
        for byte in data:
            if alg.refin:
                byte = _reflect(byte, 8)
            for bit in range(7, -1, -1):
                incoming = (byte >> bit) & 1
                msb = 1 if crc & top else 0
                crc = (crc << 1) & mask
                if msb ^ incoming:
                    crc ^= alg.poly
    if alg.refout:
        crc = _reflect(crc, width)
    return (crc ^ alg.xorout) & mask


def _sum8(data: bytes) -> int:
    return sum(data) & 0xFF


def _sum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def _xor8(data: bytes) -> int:
    return reduce(lambda a, b: a ^ b, data, 0)


def _twos_complement8(data: bytes) -> int:
    return (-sum(data)) & 0xFF


_CATALOG = [
    CrcAlgorithm("CRC8", 8, 0x07, 0x00, False, False, 0x00, 0xF4, "CRC-8 (SMBus), poly 0x07"),
    CrcAlgorithm("CRC8_MAXIM", 8, 0x31, 0x00, True, True, 0x00, 0xA1, "CRC-8/MAXIM (Dallas 1-Wire)"),
    CrcAlgorithm("CRC8_SAE_J1850", 8, 0x1D, 0xFF, False, False, 0xFF, 0x4B, "CRC-8/SAE-J1850 (automotive)"),
    CrcAlgorithm("CRC8_AUTOSAR", 8, 0x2F, 0xFF, False, False, 0xFF, 0xDF, "CRC-8/AUTOSAR"),
    CrcAlgorithm("CRC8_CDMA2000", 8, 0x9B, 0xFF, False, False, 0x00, 0xDA, "CRC-8/CDMA2000"),
    CrcAlgorithm("CRC16_MODBUS", 16, 0x8005, 0xFFFF, True, True, 0x0000, 0x4B37, "CRC-16/MODBUS"),
    CrcAlgorithm("CRC16_CCITT_FALSE", 16, 0x1021, 0xFFFF, False, False, 0x0000, 0x29B1, "CRC-16/CCITT-FALSE"),
    CrcAlgorithm("CRC16_XMODEM", 16, 0x1021, 0x0000, False, False, 0x0000, 0x31C3, "CRC-16/XMODEM"),
    CrcAlgorithm("CRC16_KERMIT", 16, 0x1021, 0x0000, True, True, 0x0000, 0x2189, "CRC-16/KERMIT"),
    CrcAlgorithm("CRC16_ARC", 16, 0x8005, 0x0000, True, True, 0x0000, 0xBB3D, "CRC-16/ARC (IBM)"),
    CrcAlgorithm("CRC16_USB", 16, 0x8005, 0xFFFF, True, True, 0xFFFF, 0xB4C8, "CRC-16/USB"),
    CrcAlgorithm("CRC32", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xCBF43926, "CRC-32 (ISO-HDLC, Ethernet, zip)"),
    CrcAlgorithm("CRC32_MPEG2", 32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0x00000000, 0x0376E6E7, "CRC-32/MPEG-2"),
    CrcAlgorithm("CRC32C", 32, 0x1EDC6F41, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xE3069283, "CRC-32C (Castagnoli)"),
    CrcAlgorithm("SUM8", 8, check=0xDD, description="8-bit arithmetic sum", custom=_sum8),
    CrcAlgorithm("SUM16", 16, check=0x01DD, description="16-bit arithmetic sum", custom=_sum16),
    CrcAlgorithm("XOR8", 8, check=0x31, description="8-bit XOR of all bytes (LRC)", custom=_xor8),
    CrcAlgorithm("TWOS_COMPLEMENT8", 8, check=0x23, description="Two's complement of the 8-bit sum", custom=_twos_complement8),
]

ALGORITHMS: Dict[str, CrcAlgorithm] = {alg.name: alg for alg in _CATALOG}

_ALIASES = {
    "CRC8_SMBUS": "CRC8",
    "CRC16": "CRC16_MODBUS",
    "CRC16_CCITT": "CRC16_CCITT_FALSE",
    "CRC16_IBM": "CRC16_ARC",
    "CRC32_ISO_HDLC": "CRC32",
    "CHECKSUM8": "SUM8",
    "LRC": "XOR8",
}


def normalize_name(name: str) -> str:
    key = str(name).strip().upper().replace("-", "_").replace("/", "_").replace(" ", "_")
    key = re.sub(r"^CRC_(\d)", r"CRC\1", key)
    return _ALIASES.get(key, key)


def is_crc_name(name: str) -> bool:
    return normalize_name(name) in ALGORITHMS


def get_algorithm(name: str) -> CrcAlgorithm:
    key = normalize_name(name)
    try:
        return ALGORITHMS[key]
    except KeyError:
        raise DefinitionError(
            f"unknown CRC algorithm '{name}' (known: {', '.join(sorted(ALGORITHMS))})"
        ) from None


def custom_algorithm(spec: dict) -> CrcAlgorithm:
    """Build an algorithm from a parametric dict (width, poly, init, ...)."""
    from .values import parse_int

    try:
        width = parse_int(spec["width"])
        poly = parse_int(spec["poly"])
    except KeyError as exc:
        raise DefinitionError(f"custom CRC requires '{exc.args[0]}'") from None
    if width < 1 or width > 64:
        raise DefinitionError("custom CRC width must be between 1 and 64")
    return CrcAlgorithm(
        name=str(spec.get("name", f"CUSTOM_CRC{width}")).upper(),
        width=width,
        poly=poly,
        init=parse_int(spec.get("init", 0)),
        refin=bool(spec.get("refin", False)),
        refout=bool(spec.get("refout", False)),
        xorout=parse_int(spec.get("xorout", 0)),
        description="Custom CRC",
    )


def compute(name: str, data: bytes) -> int:
    return get_algorithm(name).compute(data)
