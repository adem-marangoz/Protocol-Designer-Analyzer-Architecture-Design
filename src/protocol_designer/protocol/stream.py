"""Frame detector for byte-stream transports (UART, RS485, TCP).

Bytes arrive in arbitrary chunks. The detector keeps a buffer, looks for the
start of a known frame (its constants, e.g. SOF), waits until the whole frame
has arrived (using its LENGTH field when it has a variable part), validates it
and emits it. Bytes that do not start any frame are reported as garbage so
nothing is silently lost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from .decoder import DecodedMessage, Decoder
from .errors import DecodeError, DefinitionError, FrameMismatch, IncompleteFrame
from .layout import compute_layout, is_streamable
from .model import ProtocolDefinition
from .values import to_hex


@dataclass
class StreamEvent:
    kind: str  # "message" | "garbage"
    data: bytes
    message: Optional[DecodedMessage] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def hex(self) -> str:
        return to_hex(self.data)


class StreamDecoder:
    def __init__(self, protocol: ProtocolDefinition, max_frame_size: int = 4096):
        self.protocol = protocol
        self.decoder = Decoder(protocol)
        self.max_frame_size = max_frame_size
        self.buffer = bytearray()
        self._garbage = bytearray()
        self._frames = []
        for fdef in self.decoder.candidate_frames():
            try:
                if fdef.fields and is_streamable(fdef):
                    self._frames.append(fdef)
            except DefinitionError:
                continue

    @property
    def pending(self) -> bytes:
        return bytes(self.buffer)

    def reset(self) -> None:
        self.buffer.clear()
        self._garbage.clear()

    def feed(self, data: bytes) -> List[StreamEvent]:
        self.buffer.extend(data)
        return self._process(final=False)

    def flush(self) -> List[StreamEvent]:
        """Emit whatever is left (call after an idle timeout or on close)."""
        events = self._process(final=True)
        if self.buffer:
            self._garbage.extend(self.buffer)
            self.buffer.clear()
        self._emit_garbage(events)
        return events

    def _emit_garbage(self, events: List[StreamEvent]) -> None:
        if self._garbage:
            events.append(StreamEvent("garbage", bytes(self._garbage)))
            self._garbage.clear()

    def _process(self, final: bool) -> List[StreamEvent]:
        events: List[StreamEvent] = []
        while self.buffer:
            data = bytes(self.buffer)
            valid: Optional[DecodedMessage] = None
            invalid: Optional[DecodedMessage] = None
            need_more = False
            for fdef in self._frames:
                try:
                    spans = compute_layout(fdef, data, self.protocol, complete=False)
                except IncompleteFrame as exc:
                    if exc.needed <= self.max_frame_size:
                        need_more = True
                    continue
                except (FrameMismatch, DecodeError, DefinitionError):
                    continue
                total = spans[-1].end if spans else 0
                if total == 0:
                    continue
                msg = self.decoder.decode_spans(fdef, data[:total], spans)
                if msg.valid:
                    valid = msg
                    break
                if invalid is None or len(msg.errors) < len(invalid.errors):
                    invalid = msg

            if valid is not None:
                self._consume(events, valid)
                continue
            if need_more and not final:
                break
            if invalid is not None:
                self._consume(events, invalid)
                continue
            # Nothing starts here: this byte is garbage, resynchronise.
            self._garbage.append(self.buffer.pop(0))
        return events

    def _consume(self, events: List[StreamEvent], msg: DecodedMessage) -> None:
        self._emit_garbage(events)
        del self.buffer[: len(msg.data)]
        events.append(StreamEvent("message", msg.data, msg, msg.timestamp))
