"""In-memory transports: loopback and a protocol-driven device simulator.

The simulator lets the whole tool – builder, monitor, test engine – be used
without hardware. It answers requests according to the protocol file's
``simulator`` rules, e.g. "on READ_SENSOR reply SENSOR_DATA with PRESSURE=2.4".
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from ..protocol.decoder import DecodedMessage, Decoder
from ..protocol.encoder import Encoder
from ..protocol.errors import ProtocolError
from ..protocol.model import ProtocolDefinition, SimulatorRule, TransportSettings
from ..protocol.stream import StreamDecoder
from ..protocol.compare import values_equal
from .base import RawFrame, Transport, TransportError


class _QueueTransport(Transport):
    """Receive queue where each frame becomes available at a given time."""

    def __init__(self, settings: TransportSettings):
        super().__init__(settings)
        self._connected = False
        self._cond = threading.Condition()
        self._items: List[tuple] = []  # (due monotonic time, sequence, RawFrame)
        self._seq = 0

    def open(self) -> None:
        with self._cond:
            self._items.clear()
            self._connected = True

    def close(self) -> None:
        with self._cond:
            self._connected = False
            self._cond.notify_all()

    def is_connected(self) -> bool:
        return self._connected

    def _enqueue(self, frame: RawFrame, delay_s: float = 0.0) -> None:
        with self._cond:
            self._seq += 1
            self._items.append((time.monotonic() + delay_s, self._seq, frame))
            self._items.sort(key=lambda item: (item[0], item[1]))
            self._cond.notify_all()

    def inject(self, data: bytes, can_id: Optional[int] = None, delay_s: float = 0.0) -> None:
        """Make ``data`` appear as received (used by tests and demos)."""
        self._enqueue(RawFrame(bytes(data), can_id=can_id), delay_s)

    def receive(self, timeout: float = 0.1) -> List[RawFrame]:
        self._require_open()
        deadline = time.monotonic() + max(timeout, 0)
        with self._cond:
            while True:
                now = time.monotonic()
                due = [item for item in self._items if item[0] <= now]
                if due:
                    self._items = [item for item in self._items if item[0] > now]
                    frames = []
                    for _, _, frame in due:
                        frame.timestamp = time.time()
                        frames.append(frame)
                    return frames
                if now >= deadline or not self._connected:
                    return []
                next_due = self._items[0][0] if self._items else deadline
                self._cond.wait(max(min(deadline, next_due) - now, 0))


class LoopbackTransport(_QueueTransport):
    """Everything sent is received back unchanged."""

    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        self._require_open()
        self._enqueue(RawFrame(bytes(data), can_id=can_id, extended=extended))


class SimulatorTransport(_QueueTransport):
    """A virtual device that replies according to the protocol's simulator rules."""

    def __init__(self, settings: TransportSettings, protocol: ProtocolDefinition):
        super().__init__(settings)
        self.protocol = protocol
        self.encoder = Encoder(protocol)
        self.decoder = Decoder(protocol)
        self._stream = StreamDecoder(protocol)
        # CAN-style protocols (frames carry CAN ids) exchange whole messages.
        self.message_oriented = any(f.can_id is not None for f in protocol.frames)
        self.received: List[DecodedMessage] = []

    @property
    def description(self) -> str:
        return f"SIMULATOR ({self.protocol.name})"

    def open(self) -> None:
        super().open()
        self._stream.reset()
        self.received = []

    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        self._require_open()
        if self.message_oriented:
            requests = [self.decoder.decode_any(bytes(data), can_id)]
        else:
            requests = [e.message for e in self._stream.feed(bytes(data)) if e.kind == "message"]
        for request in requests:
            if request is None or not request.valid:
                continue
            self.received.append(request)
            self._respond(request)

    def _respond(self, request: DecodedMessage) -> None:
        for rule in self.protocol.simulator:
            if rule.on != request.name or not _matches(rule, request):
                continue
            if not rule.reply:
                return  # rule says: stay silent
            reply_def = self.protocol.frame(rule.reply)
            values: Dict[str, Any] = {}
            for name in rule.copy:
                if request.has_field(name):
                    values[name] = _copyable(request, name)
            values.update(rule.values)
            try:
                encoded = self.encoder.encode(reply_def, values)
            except ProtocolError as exc:
                raise TransportError(f"simulator cannot build {rule.reply}: {exc}") from None
            frame = RawFrame(encoded.data, can_id=reply_def.can_id, extended=reply_def.can_extended)
            self._enqueue(frame, rule.delay_ms / 1000.0)
            return


def _copyable(msg: DecodedMessage, name: str) -> Any:
    """Value of a request field in a form the encoder accepts again."""
    f = msg.field(name)
    if f.bits:
        return {b.name: b.value for b in f.bits}
    if isinstance(f.value, (bytes, bytearray)):
        return bytes(f.value)
    return f.value


def _matches(rule: SimulatorRule, request: DecodedMessage) -> bool:
    for name, expected in rule.match.items():
        if not request.has_field(name):
            return False
        if not values_equal(request.field(name), expected):
            return False
    return True
