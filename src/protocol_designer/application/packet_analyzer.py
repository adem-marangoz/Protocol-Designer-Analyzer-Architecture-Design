"""Packet analyzer: decode bytes pasted or loaded by the user."""

from __future__ import annotations

from typing import List, Optional, Union

from ..protocol.decoder import DecodedMessage, Decoder
from ..protocol.errors import DecodeError
from ..protocol.stream import StreamDecoder, StreamEvent
from ..protocol.model import ProtocolDefinition
from ..protocol.values import parse_hex_bytes


class PacketAnalyzer:
    def __init__(self, protocol: ProtocolDefinition):
        self.protocol = protocol
        self.decoder = Decoder(protocol)

    def analyze(
        self,
        data: Union[str, bytes],
        frame: Optional[str] = None,
        can_id: Optional[int] = None,
    ) -> List[StreamEvent]:
        """Decode one or more frames.

        * ``frame`` given – decode the bytes as that frame (constants are
          checked but a mismatch is reported instead of raised).
        * ``can_id`` given – the bytes are one CAN payload.
        * otherwise the bytes are treated as a stream that may contain several
          frames and garbage in between.
        """
        raw = parse_hex_bytes(data) if isinstance(data, str) else bytes(data)
        if not raw:
            return []
        if frame:
            return [StreamEvent("message", raw, self._force(frame, raw, can_id))]
        message_oriented = any(f.can_id is not None for f in self.protocol.frames)
        if can_id is not None or message_oriented:
            # CAN style: the bytes are exactly one message payload
            return [StreamEvent("message", raw, self.decoder.decode_any(raw, can_id))]
        stream = StreamDecoder(self.protocol)
        events = stream.feed(raw) + stream.flush()
        if len(events) == 1 and events[0].kind == "garbage":
            # Not streamable (e.g. no SOF): try as one complete frame.
            msg = self.decoder.identify(raw)
            if msg is not None:
                return [StreamEvent("message", raw, msg)]
        return events

    def _force(self, frame: str, raw: bytes, can_id: Optional[int]) -> DecodedMessage:
        try:
            return self.decoder.decode(frame, raw, can_id=can_id)
        except DecodeError as exc:
            try:
                msg = self.decoder.decode(frame, raw, check_constants=False, can_id=can_id)
                if not msg.errors:
                    msg.errors.append(str(exc))
                return msg
            except DecodeError as exc2:
                bad = DecodedMessage(self.protocol.frame(frame), raw, can_id=can_id)
                bad.errors.append(str(exc2))
                return bad
