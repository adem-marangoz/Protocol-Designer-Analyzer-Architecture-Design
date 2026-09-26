"""Shared state for all GUI pages and the bridge from worker threads to Qt.

Pages never talk to transports directly: they use the :class:`AppContext`,
which owns the protocol manager, the live session and the traffic log.
Session callbacks arrive on the reader thread; :class:`AppContext` re-emits
them as Qt signals, which Qt delivers on the GUI thread.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

from ..application.logger import TrafficEntry, TrafficLog
from ..application.protocol_manager import ProtocolManager
from ..application.session import Session
from ..application.settings import AppSettings

log = logging.getLogger(__name__)


class AppContext(QObject):
    protocol_changed = Signal()  # a different protocol was loaded / created
    protocol_edited = Signal()  # the current protocol was modified
    traffic_entry = Signal(object)  # TrafficEntry, delivered on the GUI thread
    connection_changed = Signal(bool, str)
    status_message = Signal(str)
    navigate = Signal(str, object)  # page key, optional argument (e.g. message name)

    def __init__(self, settings: Optional[AppSettings] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.settings = settings or AppSettings()
        self.manager = ProtocolManager()
        self.traffic = TrafficLog()
        self.session: Optional[Session] = None
        self.traffic.subscribe(self._on_traffic)
        self.manager.subscribe(self._on_manager_changed)
        self._loaded_id = id(self.manager.protocol)

    # ------------------------------------------------------------ protocol ---

    @property
    def protocol(self):
        return self.manager.protocol

    def _on_manager_changed(self) -> None:
        if id(self.manager.protocol) != self._loaded_id:
            self._loaded_id = id(self.manager.protocol)
            self.disconnect_session()
            self.protocol_changed.emit()
        else:
            self.protocol_edited.emit()

    def mark_modified(self) -> None:
        self.manager.mark_modified()

    # ------------------------------------------------------------- session ---

    @property
    def connected(self) -> bool:
        return self.session is not None and self.session.connected

    def connect_session(self) -> None:
        """Open the transport configured in the protocol. Raises TransportError."""
        self.disconnect_session()
        session = Session(self.protocol, traffic=self.traffic, idle_flush_ms=self.settings.idle_flush_ms)
        session.on_state_changed(self._on_state)
        self.session = session
        try:
            session.connect()
        except Exception:
            self.session = None
            raise

    def disconnect_session(self) -> None:
        if self.session is not None:
            session, self.session = self.session, None
            session.disconnect()

    def ensure_session(self) -> Session:
        """A session for the test runner (connected on demand by the engine)."""
        if self.session is None:
            self.session = Session(self.protocol, traffic=self.traffic, idle_flush_ms=self.settings.idle_flush_ms)
            self.session.on_state_changed(self._on_state)
        return self.session

    def _on_state(self, connected: bool, message: str) -> None:
        self.connection_changed.emit(connected, message)

    def _on_traffic(self, entry: TrafficEntry) -> None:
        self.traffic_entry.emit(entry)
