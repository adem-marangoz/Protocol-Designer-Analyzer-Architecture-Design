"""UART and RS485 transports based on pyserial.

``port`` may be a device name (``COM3``, ``/dev/ttyUSB0``) or any pyserial
URL such as ``loop://`` (loopback, used by the tests), ``socket://host:port``
or ``rfc2217://host:port``.
"""

from __future__ import annotations

import time
from typing import List, Optional

from ..protocol.model import TransportSettings
from .base import RawFrame, Transport, TransportError

try:  # pyserial is a hard dependency, but keep import errors readable
    import serial
    import serial.rs485
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None

_PARITY = {
    "NONE": "N", "N": "N",
    "EVEN": "E", "E": "E",
    "ODD": "O", "O": "O",
    "MARK": "M", "M": "M",
    "SPACE": "S", "S": "S",
}


def list_serial_ports() -> List[str]:
    if list_ports is None:  # pragma: no cover
        return []
    return sorted(p.device for p in list_ports.comports())


class SerialTransport(Transport):
    """Plain UART (RS232 / TTL / USB-CDC)."""

    def __init__(self, settings: TransportSettings):
        super().__init__(settings)
        self._serial = None

    def _serial_kwargs(self) -> dict:
        s = self.settings
        parity = _PARITY.get(str(s.parity).upper())
        if parity is None:
            raise TransportError(f"invalid parity '{s.parity}'")
        if s.data_bits not in (5, 6, 7, 8):
            raise TransportError(f"invalid data bits {s.data_bits}")
        if float(s.stop_bits) not in (1, 1.5, 2):
            raise TransportError(f"invalid stop bits {s.stop_bits}")
        return dict(
            baudrate=s.baudrate,
            bytesize=s.data_bits,
            parity=parity,
            stopbits=float(s.stop_bits) if s.stop_bits == 1.5 else int(s.stop_bits),
            timeout=0,
            write_timeout=2,
        )

    def open(self) -> None:
        if serial is None:  # pragma: no cover
            raise TransportError("pyserial is not installed")
        if not self.settings.port:
            raise TransportError("no serial port selected")
        kwargs = self._serial_kwargs()
        try:
            self._serial = serial.serial_for_url(self.settings.port, **kwargs)
            self._configure()
        except (serial.SerialException, ValueError, OSError) as exc:
            self._serial = None
            raise TransportError(f"cannot open {self.settings.port}: {exc}") from None

    def _configure(self) -> None:
        """Hook for subclasses (RS485 direction control)."""

    def close(self) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            try:
                port.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        self._require_open()
        try:
            self._serial.write(bytes(data))
            self._serial.flush()
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"write failed: {exc}") from None

    def _read_available(self) -> bytes:
        try:
            waiting = self._serial.in_waiting
            return self._serial.read(waiting) if waiting else b""
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"read failed: {exc}") from None

    def receive(self, timeout: float = 0.1) -> List[RawFrame]:
        self._require_open()
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            data = self._read_available()
            if data:
                # give the rest of a burst a moment to arrive, like an inter-byte timeout
                time.sleep(0.002)
                data += self._read_available()
                return [RawFrame(data)]
            if time.monotonic() >= deadline:
                return []
            time.sleep(0.002)


class RS485Transport(SerialTransport):
    """Half-duplex RS485.

    Options (``transport.options``):

    * ``rts_toggle`` – drive RTS for the driver-enable pin (adapters without
      automatic direction control). Uses the OS RS485 mode when available.
    * ``local_echo`` – the adapter echoes what it transmits; remove the echo so
      the analyzer does not show our own frames as received.
    """

    def __init__(self, settings: TransportSettings):
        super().__init__(settings)
        self._echo = bytearray()

    def _configure(self) -> None:
        if self.settings.options.get("rts_toggle") and hasattr(self._serial, "rs485_mode"):
            try:
                self._serial.rs485_mode = serial.rs485.RS485Settings(
                    rts_level_for_tx=True, rts_level_for_rx=False
                )
            except (ValueError, OSError, serial.SerialException):
                # Not supported by this driver: fall back to plain serial.
                pass

    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None:
        if self.settings.options.get("local_echo"):
            self._echo.extend(data)
        super().send(data)

    def receive(self, timeout: float = 0.1) -> List[RawFrame]:
        frames = super().receive(timeout)
        if not self._echo or not frames:
            return frames
        out = []
        for frame in frames:
            data = bytearray(frame.data)
            while data and self._echo and data[0] == self._echo[0]:
                del data[0]
                del self._echo[0]
            if data:
                self._echo.clear()  # echo lost or already consumed
                out.append(RawFrame(bytes(data), frame.timestamp))
        return out
