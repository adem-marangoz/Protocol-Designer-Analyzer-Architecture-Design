"""Exceptions raised by the protocol engine."""


class ProtocolError(Exception):
    """Base class for all protocol engine errors."""


class DefinitionError(ProtocolError):
    """The protocol definition itself is malformed."""


class EncodeError(ProtocolError):
    """A message could not be encoded from the supplied values."""


class DecodeError(ProtocolError):
    """Raw bytes could not be decoded as the requested frame."""


class FrameMismatch(DecodeError):
    """The bytes do not belong to the frame (e.g. a constant differs)."""


class IncompleteFrame(DecodeError):
    """More bytes are needed before the frame can be decoded."""

    def __init__(self, needed: int, message: str = ""):
        super().__init__(message or f"incomplete frame: {needed} byte(s) required")
        self.needed = needed
