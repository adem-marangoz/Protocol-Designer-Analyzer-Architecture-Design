"""CAN and CAN-FD transports based on python-can.

Any python-can interface works: ``virtual`` (in-process, no hardware),
``pcan``, ``vector``, ``kvaser``, ``ixxat``, ``slcan``, ``socketcan``, ...
Each received CAN message is exactly one protocol frame, so this transport is
message oriented and frames are identified by their CAN id.
"""

from __future__ import annotations

import time
from typing import List, Optional

from ..protocol.model import TransportSettings, TransportType
from .base import RawFrame, Transport, TransportError

try:
    import can
except ImportError:  # pragma: no cover
    can = None


def list_can_interfaces() -> List[str]:
    if can is None:  # pragma: no cover
        return []
    return sorted(can.interfaces.VALID_INTERFACES)


class CanTransport(Transport):
    message_oriented = True

    def __init__(self, settings: TransportSettings):
        super().__init__(settings)
        self._bus = None

    @property
    def fd(self) -> bool:
        return self.settings.type == TransportType.CANFD

    def open(self) -> None:
        if can is None:  # pragma: no cover
            raise TransportError("python-can is not installed")
        s = self.settings
        kwargs = dict(interface=s.can_interface, channel=s.can_channel, bitrate=s.can_bitrate)
        if self.fd:
            kwargs.update(fd=True, data_bitrate=s.can_data_bitrate)
        if s.can_interface == "virtual":
            kwargs["receive_own_messages"] = bool(s.options.get("receive_own_messages", False))
        kwargs.update(s.options.get("can_kwargs", {}))
        try:
            self._bus = can.Bus(**kwargs)
        except Exception as exc:  # noqa: BLE001 - python-can raises many types
            self._bus = None
            raise TransportError(f"cannot open CAN {s.can_interface}:{s.can_channel}: {exc}") from None

    def close(self) -> None:
        bus, self._bus = self._bus, None
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def is_connected(self) -> bool:
        return self._bus is not None

    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        self._require_open()
        if can_id is None:
            raise TransportError("a CAN id is required to send on a CAN bus")
        limit = 64 if self.fd else 8
        if len(data) > limit:
            raise TransportError(f"{len(data)} bytes exceed the {limit}-byte {'CAN-FD' if self.fd else 'CAN'} payload")
        msg = can.Message(
            arbitration_id=can_id,
            data=bytes(data),
            is_extended_id=extended or can_id > 0x7FF,
            is_fd=self.fd,
            bitrate_switch=self.fd,
        )
        try:
            self._bus.send(msg, timeout=1.0)
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"CAN send failed: {exc}") from None

    def receive(self, timeout: float = 0.1) -> List[RawFrame]:
        self._require_open()
        frames: List[RawFrame] = []
        wait = max(timeout, 0)
        while True:
            try:
                msg = self._bus.recv(timeout=wait)
            except Exception as exc:  # noqa: BLE001
                raise TransportError(f"CAN receive failed: {exc}") from None
            if msg is None or msg.is_error_frame:
                return frames
            frames.append(
                RawFrame(
                    bytes(msg.data),
                    timestamp=time.time(),
                    can_id=msg.arbitration_id,
                    extended=msg.is_extended_id,
                    is_fd=msg.is_fd,
                )
            )
            wait = 0  # drain what is already queued, then return
