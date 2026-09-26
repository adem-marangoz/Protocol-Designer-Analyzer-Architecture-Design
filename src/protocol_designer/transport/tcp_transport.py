"""TCP and UDP transports.

TCP connects as a client by default; with ``options.listen = true`` it waits
for one incoming connection instead (useful to emulate a device).
"""

from __future__ import annotations

import select
import socket
import time
from typing import List, Optional

from ..protocol.model import TransportSettings
from .base import RawFrame, Transport, TransportError


class TcpTransport(Transport):
    def __init__(self, settings: TransportSettings):
        super().__init__(settings)
        self._sock: Optional[socket.socket] = None
        self._server: Optional[socket.socket] = None
        self._closed_by_peer = False

    @property
    def listening(self) -> bool:
        return bool(self.settings.options.get("listen"))

    def open(self) -> None:
        s = self.settings
        self._closed_by_peer = False
        try:
            if self.listening:
                self._server = socket.create_server((s.host or "0.0.0.0", s.tcp_port))
                self._server.settimeout(float(s.options.get("accept_timeout", 30)))
                self._sock, _ = self._server.accept()
            else:
                self._sock = socket.create_connection((s.host, s.tcp_port), timeout=5)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock.setblocking(False)
        except OSError as exc:
            self.close()
            raise TransportError(f"cannot connect to {s.host}:{s.tcp_port}: {exc}") from None

    @property
    def local_port(self) -> Optional[int]:
        sock = self._server or self._sock
        return sock.getsockname()[1] if sock else None

    def close(self) -> None:
        for sock in (self._sock, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._sock = self._server = None

    def is_connected(self) -> bool:
        return self._sock is not None and not self._closed_by_peer

    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        self._require_open()
        try:
            self._sock.setblocking(True)
            self._sock.sendall(bytes(data))
        except OSError as exc:
            raise TransportError(f"send failed: {exc}") from None
        finally:
            if self._sock is not None:
                self._sock.setblocking(False)

    def receive(self, timeout: float = 0.1) -> List[RawFrame]:
        self._require_open()
        try:
            ready, _, _ = select.select([self._sock], [], [], max(timeout, 0))
            if not ready:
                return []
            data = self._sock.recv(65536)
        except BlockingIOError:
            return []
        except OSError as exc:
            raise TransportError(f"receive failed: {exc}") from None
        if not data:
            self._closed_by_peer = True
            raise TransportError("connection closed by peer")
        return [RawFrame(data)]


class UdpTransport(Transport):
    """UDP datagrams to ``host:tcp_port``; ``options.local_port`` binds locally."""

    def __init__(self, settings: TransportSettings):
        super().__init__(settings)
        self._sock: Optional[socket.socket] = None

    def open(self) -> None:
        s = self.settings
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind(("0.0.0.0", int(s.options.get("local_port", 0))))
            self._sock.setblocking(False)
        except OSError as exc:
            self.close()
            raise TransportError(f"cannot open UDP socket: {exc}") from None

    @property
    def local_port(self) -> Optional[int]:
        return self._sock.getsockname()[1] if self._sock else None

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
        self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None

    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        self._require_open()
        try:
            self._sock.sendto(bytes(data), (self.settings.host, self.settings.tcp_port))
        except OSError as exc:
            raise TransportError(f"send failed: {exc}") from None

    def receive(self, timeout: float = 0.1) -> List[RawFrame]:
        self._require_open()
        frames: List[RawFrame] = []
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            remaining = max(deadline - time.monotonic(), 0)
            ready, _, _ = select.select([self._sock], [], [], remaining if not frames else 0)
            if not ready:
                return frames
            try:
                data, _ = self._sock.recvfrom(65536)
            except BlockingIOError:
                return frames
            except OSError as exc:
                raise TransportError(f"receive failed: {exc}") from None
            frames.append(RawFrame(data))
