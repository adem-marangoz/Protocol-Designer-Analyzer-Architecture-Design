"""Protocol definition model.

These dataclasses are the in-memory form of a protocol file. They contain no
encoding logic; the :mod:`encoder` and :mod:`decoder` modules interpret them.
Keeping the model declarative is what lets one engine serve every protocol.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class FieldType(str, Enum):
    UINT8 = "UINT8"
    UINT16 = "UINT16"
    UINT32 = "UINT32"
    UINT64 = "UINT64"
    INT8 = "INT8"
    INT16 = "INT16"
    INT32 = "INT32"
    INT64 = "INT64"
    FLOAT32 = "FLOAT32"
    FLOAT64 = "FLOAT64"
    BITFIELD = "BITFIELD"
    BYTES = "BYTES"
    STRING = "STRING"
    ENUM = "ENUM"
    BOOLEAN = "BOOLEAN"


INTEGER_SIZES = {
    FieldType.UINT8: (1, False),
    FieldType.UINT16: (2, False),
    FieldType.UINT32: (4, False),
    FieldType.UINT64: (8, False),
    FieldType.INT8: (1, True),
    FieldType.INT16: (2, True),
    FieldType.INT32: (4, True),
    FieldType.INT64: (8, True),
}
FLOAT_SIZES = {FieldType.FLOAT32: 4, FieldType.FLOAT64: 8}
# Types stored as an unsigned integer of a configurable size (1, 2, 4 or 8 bytes).
UNSIGNED_CONTAINER_TYPES = (FieldType.BITFIELD, FieldType.ENUM, FieldType.BOOLEAN)
VARIABLE_CAPABLE_TYPES = (FieldType.BYTES, FieldType.STRING)


class Encoding(str, Enum):
    VALUE = "VALUE"  # supplied by the user
    CONSTANT = "CONSTANT"  # fixed value, also used to identify frames
    LENGTH = "LENGTH"  # computed from the size of a range of fields
    CRC = "CRC"  # computed checksum over a range of fields


class Endianness(str, Enum):
    BIG = "BIG"
    LITTLE = "LITTLE"


class Direction(str, Enum):
    TX = "TX"
    RX = "RX"
    BOTH = "BOTH"


class TransportType(str, Enum):
    UART = "UART"
    RS485 = "RS485"
    TCP = "TCP"
    UDP = "UDP"
    CAN = "CAN"
    CANFD = "CANFD"
    LOOPBACK = "LOOPBACK"
    SIMULATOR = "SIMULATOR"


MESSAGE_ORIENTED_TRANSPORTS = (TransportType.CAN, TransportType.CANFD)


@dataclass
class EnumDefinition:
    name: str
    values: Dict[int, str] = field(default_factory=dict)
    description: str = ""

    def name_of(self, value: int) -> Optional[str]:
        return self.values.get(value)

    def value_of(self, name: str) -> Optional[int]:
        target = str(name).strip().upper()
        for value, label in self.values.items():
            if label.upper() == target:
                return value
        return None


@dataclass
class BitDefinition:
    """A group of bits inside a BITFIELD field. ``start`` is the LSB index."""

    name: str
    start: int
    length: int = 1
    enum: Optional[Union[str, Dict[int, str]]] = None
    description: str = ""

    @property
    def mask(self) -> int:
        return ((1 << self.length) - 1) << self.start

    @property
    def is_flag(self) -> bool:
        return self.length == 1 and self.enum is None


@dataclass
class FieldDefinition:
    name: str
    type: FieldType = FieldType.UINT8
    size: Optional[int] = None  # bytes; None = type default; 0 = variable
    offset: Optional[int] = None  # informational; checked by the validator
    encoding: Encoding = Encoding.VALUE
    value: Any = None  # constant value when encoding is CONSTANT
    endianness: Optional[Endianness] = None  # None = protocol default
    # CRC fields
    crc: Optional[Union[str, Dict[str, Any]]] = None
    crc_from: Optional[str] = None  # first field covered (inclusive); None = frame start
    crc_to: Optional[str] = None  # last field covered (inclusive); None = field before CRC
    # LENGTH fields
    length_from: Optional[str] = None  # None = field after the LENGTH field
    length_to: Optional[str] = None  # None = field before the first following CRC
    length_adjust: int = 0
    # Presentation / validation
    scale: float = 1.0
    value_offset: float = 0.0
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum: Optional[Union[str, Dict[int, str]]] = None
    bits: List[BitDefinition] = field(default_factory=list)
    default: Any = None
    string_encoding: str = "ascii"
    description: str = ""

    @property
    def is_variable(self) -> bool:
        return self.type in VARIABLE_CAPABLE_TYPES and not self.size

    @property
    def is_scaled(self) -> bool:
        return self.scale != 1.0 or self.value_offset != 0.0


@dataclass
class FrameDefinition:
    name: str
    id: Optional[int] = None
    direction: Direction = Direction.BOTH
    fields: List[FieldDefinition] = field(default_factory=list)
    description: str = ""
    can_id: Optional[int] = None
    can_extended: bool = False
    expected_response: Optional[str] = None

    def field(self, name: str) -> FieldDefinition:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"frame '{self.name}' has no field '{name}'")

    def field_index(self, name: str) -> int:
        for i, f in enumerate(self.fields):
            if f.name == name:
                return i
        raise KeyError(f"frame '{self.name}' has no field '{name}'")

    def has_field(self, name: str) -> bool:
        return any(f.name == name for f in self.fields)

    @property
    def user_fields(self) -> List[FieldDefinition]:
        """Fields whose value the user supplies when building a packet."""
        return [f for f in self.fields if f.encoding == Encoding.VALUE]


@dataclass
class TransportSettings:
    type: TransportType = TransportType.RS485
    port: str = ""
    baudrate: int = 115200
    data_bits: int = 8
    parity: str = "NONE"
    stop_bits: float = 1
    host: str = "127.0.0.1"
    tcp_port: int = 5000
    can_interface: str = "virtual"
    can_channel: str = "0"
    can_bitrate: int = 500000
    can_data_bitrate: int = 2000000
    timeout_ms: int = 100
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def message_oriented(self) -> bool:
        return self.type in MESSAGE_ORIENTED_TRANSPORTS

    def summary(self) -> str:
        t = self.type
        if t in (TransportType.UART, TransportType.RS485):
            parity = (self.parity or "N")[0].upper()
            stop = int(self.stop_bits) if float(self.stop_bits).is_integer() else self.stop_bits
            return f"{self.port or '(no port)'} | {self.baudrate} | {self.data_bits}{parity}{stop} | {t.value}"
        if t in (TransportType.TCP, TransportType.UDP):
            return f"{t.value} {self.host}:{self.tcp_port}"
        if t in (TransportType.CAN, TransportType.CANFD):
            return f"{t.value} {self.can_interface}:{self.can_channel} @ {self.can_bitrate}"
        return t.value


@dataclass
class CheckDefinition:
    """A verification applied to a received message field."""

    field: str
    equals: Any = None
    not_equals: Any = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    raw: bool = False  # compare the raw value instead of the physical value
    save_as: Optional[str] = None  # store the value into a test variable


@dataclass
class TestStep:
    __test__ = False  # not a pytest test class
    action: str  # send | send_raw | expect | transact | delay | log | set
    message: Optional[str] = None
    values: Dict[str, Any] = field(default_factory=dict)
    data: str = ""
    timeout_ms: Optional[int] = None
    checks: List[CheckDefinition] = field(default_factory=list)
    text: str = ""
    delay_ms: int = 0
    variable: str = ""
    value: Any = None


@dataclass
class TestDefinition:
    __test__ = False  # not a pytest test class
    name: str
    description: str = ""
    timeout_ms: int = 500
    steps: List[TestStep] = field(default_factory=list)


@dataclass
class SimulatorRule:
    """When the simulated device receives ``on``, it replies with ``reply``."""

    on: str
    reply: Optional[str] = None
    values: Dict[str, Any] = field(default_factory=dict)
    copy: List[str] = field(default_factory=list)  # fields copied from the request
    delay_ms: int = 0
    match: Dict[str, Any] = field(default_factory=dict)  # request field filter


@dataclass
class ProtocolDefinition:
    name: str = "New Protocol"
    version: str = "1.0"
    description: str = ""
    endianness: Endianness = Endianness.BIG
    transport: TransportSettings = field(default_factory=TransportSettings)
    enums: Dict[str, EnumDefinition] = field(default_factory=dict)
    frames: List[FrameDefinition] = field(default_factory=list)
    tests: List[TestDefinition] = field(default_factory=list)
    simulator: List[SimulatorRule] = field(default_factory=list)

    def frame(self, name: str) -> FrameDefinition:
        for f in self.frames:
            if f.name == name:
                return f
        raise KeyError(f"protocol has no frame '{name}'")

    def has_frame(self, name: str) -> bool:
        return any(f.name == name for f in self.frames)

    def test(self, name: str) -> TestDefinition:
        for t in self.tests:
            if t.name == name:
                return t
        raise KeyError(f"protocol has no test '{name}'")

    def clone(self) -> "ProtocolDefinition":
        return copy.deepcopy(self)
