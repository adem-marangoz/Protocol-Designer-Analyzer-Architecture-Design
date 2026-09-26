"""Live session: a protocol connected to a transport.

The session owns a background reader thread. Received bytes go through the
frame detector (stream transports) or straight to the decoder (CAN), and each
result is appended to the traffic log and delivered to subscribers – the Live
Monitor and the Test Engine. The GUI never talks to the transport directly
(Section 21).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ..protocol.decoder import Decoder
from ..protocol.encoder import EncodedFrame, Encoder
from ..protocol.model import ProtocolDefinition
from ..protocol.stream import StreamDecoder
from ..transport.base import Transport, TransportError
from ..transport.factory import create_transport
from .logger import ERROR, INFO, RX, TX, TrafficEntry, TrafficLog

log = logging.getLogger(__name__)

StateListener = Callable[[bool, str], None]


class Session:
    def __init__(
        self,
        protocol: ProtocolDefinition,
        transport: Optional[Transport] = None,
        traffic: Optional[TrafficLog] = None,
        idle_flush_ms: int = 50,
    ):
        self.protocol = protocol
        self.transport = transport
        self.traffic = traffic if traffic is not None else TrafficLog()
        self.idle_flush_s = idle_flush_ms / 1000.0
        self.encoder = Encoder(protocol)
        self.decoder = Decoder(protocol)
        self.stream = StreamDecoder(protocol)
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._subscribers: List["queue.Queue[TrafficEntry]"] = []
        self._sub_lock = threading.Lock()
        self._state_listeners: List[StateListener] = []
        self._send_lock = threading.Lock()
        self.last_error = ""

    # ------------------------------------------------------------- state ---

    @property
    def connected(self) -> bool:
        return self.transport is not None and self.transport.is_connected() and self._running.is_set()

    @property
    def description(self) -> str:
        if self.transport is not None:
            return self.transport.description
        return self.protocol.transport.summary()

    def on_state_changed(self, listener: StateListener) -> None:
        self._state_listeners.append(listener)

    def _emit_state(self, connected: bool, message: str) -> None:
        for listener in list(self._state_listeners):
            try:
                listener(connected, message)
            except Exception:  # noqa: BLE001
                log.exception("state listener failed")

    # -------------------------------------------------------- lifecycle ---

    def connect(self) -> None:
        if self.connected:
            return
        if self.transport is None:
            self.transport = create_transport(self.protocol.transport, self.protocol)
        try:
            self.transport.open()
        except TransportError as exc:
            self.last_error = str(exc)
            self._log(ERROR, note=f"Connection failed: {exc}")
            self._emit_state(False, str(exc))
            raise
        self.stream.reset()
        self.last_error = ""
        self._log(INFO, note=f"Connected: {self.transport.description}")
        self._running.set()
        self._emit_state(True, f"Connected: {self.transport.description}")
        self._thread = threading.Thread(target=self._reader, name="session-reader", daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        was_running = self._running.is_set()
        self._running.clear()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self.transport is not None:
            self.transport.close()
        self._flush_stream()
        if was_running:
            self._log(INFO, note="Disconnected")
            self._emit_state(False, "Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()
        return False

    # ------------------------------------------------------------ sending ---

    def send_message(self, frame_name: str, values: Optional[Dict[str, Any]] = None, *, raw: bool = False) -> EncodedFrame:
        encoded = self.encoder.encode(frame_name, values or {}, raw=raw)
        frame = encoded.frame
        self._send(encoded.data, frame.can_id, frame.can_extended)
        return encoded

    def send_raw(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        self._send(bytes(data), can_id, extended)

    def _send(self, data: bytes, can_id: Optional[int], extended: bool) -> None:
        if not self.connected:
            raise TransportError("not connected")
        with self._send_lock:
            self.transport.send(data, can_id=can_id, extended=extended)
        message = self.decoder.decode_any(data, can_id) if data else None
        entry = TrafficEntry(TX, data, message, can_id=can_id)
        self._publish(entry)

    # ---------------------------------------------------------- receiving ---

    def subscribe(self) -> "queue.Queue[TrafficEntry]":
        q: "queue.Queue[TrafficEntry]" = queue.Queue()
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue[TrafficEntry]") -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self, entry: TrafficEntry) -> None:
        self.traffic.add(entry)
        with self._sub_lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(entry)

    def _log(self, direction: str, note: str) -> None:
        self._publish(TrafficEntry(direction, note=note))

    def _reader(self) -> None:
        last_rx = time.monotonic()
        transport = self.transport
        while self._running.is_set():
            try:
                frames = transport.receive(0.02)
            except TransportError as exc:
                if not self._running.is_set():
                    break
                self.last_error = str(exc)
                self._log(ERROR, note=f"Receive error: {exc}")
                self._running.clear()
                try:
                    transport.close()
                except Exception:  # noqa: BLE001
                    pass
                self._flush_stream()
                self._emit_state(False, f"Connection lost: {exc}")
                return
            now = time.monotonic()
            try:
                self._process(frames, transport)
            except Exception as exc:  # noqa: BLE001 - keep the reader alive, report the problem
                log.exception("error while processing received data")
                self._log(ERROR, note=f"Internal error while decoding: {exc}")
            if frames:
                last_rx = now
            if self.stream.pending and now - last_rx >= self.idle_flush_s:
                self._flush_stream()

    def _process(self, frames, transport: Transport) -> None:
        for frame in frames:
            if transport.message_oriented:
                msg = self.decoder.decode_any(frame.data, frame.can_id)
                self._publish(TrafficEntry(RX, frame.data, msg, timestamp=frame.timestamp, can_id=frame.can_id))
            else:
                for event in self.stream.feed(frame.data):
                    self._publish_event(event)

    def _flush_stream(self) -> None:
        for event in self.stream.flush():
            self._publish_event(event)

    def _publish_event(self, event) -> None:
        note = "" if event.kind == "message" else "no matching frame"
        self._publish(TrafficEntry(RX, event.data, event.message, note=note, timestamp=event.timestamp))
