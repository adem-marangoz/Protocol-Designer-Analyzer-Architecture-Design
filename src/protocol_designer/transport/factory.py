"""Create the transport described by a protocol's transport settings."""

from __future__ import annotations

from typing import Optional

from ..protocol.model import ProtocolDefinition, TransportSettings, TransportType
from .base import Transport, TransportError
from .can_transport import CanTransport
from .memory_transport import LoopbackTransport, SimulatorTransport
from .serial_transport import RS485Transport, SerialTransport
from .tcp_transport import TcpTransport, UdpTransport


def create_transport(settings: TransportSettings, protocol: Optional[ProtocolDefinition] = None) -> Transport:
    t = settings.type
    if t == TransportType.UART:
        return SerialTransport(settings)
    if t == TransportType.RS485:
        return RS485Transport(settings)
    if t == TransportType.TCP:
        return TcpTransport(settings)
    if t == TransportType.UDP:
        return UdpTransport(settings)
    if t in (TransportType.CAN, TransportType.CANFD):
        return CanTransport(settings)
    if t == TransportType.LOOPBACK:
        return LoopbackTransport(settings)
    if t == TransportType.SIMULATOR:
        if protocol is None:
            raise TransportError("the simulator needs a protocol definition")
        return SimulatorTransport(settings, protocol)
    raise TransportError(f"unsupported transport '{t}'")  # pragma: no cover
