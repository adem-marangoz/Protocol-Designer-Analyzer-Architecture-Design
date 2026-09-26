"""Protocol engine: definition model, field parser, CRC, encoder, decoder, validator."""

from .decoder import DecodedBit, DecodedField, DecodedMessage, Decoder
from .encoder import EncodedFrame, Encoder
from .errors import DecodeError, DefinitionError, EncodeError, FrameMismatch, IncompleteFrame, ProtocolError
from .model import (
    BitDefinition,
    CheckDefinition,
    Direction,
    Encoding,
    Endianness,
    EnumDefinition,
    FieldDefinition,
    FieldType,
    FrameDefinition,
    ProtocolDefinition,
    SimulatorRule,
    TestDefinition,
    TestStep,
    TransportSettings,
    TransportType,
)
from .stream import StreamDecoder, StreamEvent
from .validator import Issue, has_errors, validate_protocol

__all__ = [
    "DecodedBit",
    "DecodedField",
    "DecodedMessage",
    "Decoder",
    "EncodedFrame",
    "Encoder",
    "DecodeError",
    "DefinitionError",
    "EncodeError",
    "FrameMismatch",
    "IncompleteFrame",
    "ProtocolError",
    "BitDefinition",
    "CheckDefinition",
    "Direction",
    "Encoding",
    "Endianness",
    "EnumDefinition",
    "FieldDefinition",
    "FieldType",
    "FrameDefinition",
    "ProtocolDefinition",
    "SimulatorRule",
    "TestDefinition",
    "TestStep",
    "TransportSettings",
    "TransportType",
    "StreamDecoder",
    "StreamEvent",
    "Issue",
    "has_errors",
    "validate_protocol",
]
