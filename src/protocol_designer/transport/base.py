"""Transport interface (ITransport).

A transport only moves bytes. It knows ports, baud rates and CAN ids, but
never what a byte means – that is the protocol engine's job (Section 3).
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from ..protocol.model import TransportSettings


class TransportError(Exception):
    """Opening, sending or receiving failed."""


@dataclass
class RawFrame:
    data: bytes
    timestamp: float = field(default_factory=time.time)
    can_id: Optional[int] = None
    extended: bool = False
    is_fd: bool = False


class Transport(ABC):
    #: True when every received RawFrame is exactly one protocol frame (CAN).
    message_oriented = False

    def __init__(self, settings: TransportSettings):
        self.settings = settings
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------------

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    # -- data ------------------------------------------------------------------

    @abstractmethod
    def send(self, data: bytes, can_id: Optional[int] = None, extended: bool = False) -> None: ...

    @abstractmethod
    def receive(self, timeout: float = 0.1) -> List[RawFrame]:
        """Wait up to ``timeout`` seconds and return the frames/chunks received."""

    # -- helpers ---------------------------------------------------------------

    @property
    def description(self) -> str:
        return self.settings.summary()

    def _require_open(self) -> None:
        if not self.is_connected():
            raise TransportError(f"{self.description}: not connected")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False
