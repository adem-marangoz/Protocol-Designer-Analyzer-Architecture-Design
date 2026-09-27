"""Application logging and the traffic log shown by the monitor.

* :func:`setup_logging` writes the program log to the logs folder with
  rotation, so support problems can be diagnosed.
* :class:`TrafficLog` keeps every TX/RX frame of a session (with its decoded
  form) and exports it to CSV or text.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import logging
import logging.handlers
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, List, Optional

from .. import paths
from ..protocol.decoder import DecodedMessage
from ..protocol.values import to_hex

TX = "TX"
RX = "RX"
INFO = "INFO"
ERROR = "ERROR"


def setup_logging(level: int = logging.INFO, directory: Optional[Path] = None) -> Path:
    directory = directory or paths.logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    logfile = directory / "protocol_designer.log"
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_protocol_designer", False):
            root.removeHandler(handler)
            handler.close()
    handler = logging.handlers.RotatingFileHandler(logfile, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler._protocol_designer = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    return logfile


@dataclass
class TrafficEntry:
    direction: str  # TX | RX | INFO | ERROR
    data: bytes = b""
    message: Optional[DecodedMessage] = None
    note: str = ""
    timestamp: float = field(default_factory=lambda: _dt.datetime.now().timestamp())
    can_id: Optional[int] = None

    @property
    def time_text(self) -> str:
        return _dt.datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S.%f")[:-3]

    @property
    def hex(self) -> str:
        return to_hex(self.data)

    @property
    def name(self) -> str:
        if self.message is not None:
            return self.message.name
        if self.direction in (TX, RX):
            return "??" if self.data else ""
        return ""

    @property
    def status(self) -> str:
        if self.message is None:
            return "UNKNOWN" if self.direction in (TX, RX) and self.data else ""
        if self.message.valid:
            return "WARN" if self.message.warnings else "OK"
        if self.message.crc_valid is False:
            return "CRC ERROR"
        return "ERROR"


class TrafficLog:
    """Bounded, thread-safe list of traffic entries with change listeners."""

    def __init__(self, capacity: int = 100_000):
        self._entries: Deque[TrafficEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._listeners: List[Callable[[TrafficEntry], None]] = []

    def add(self, entry: TrafficEntry) -> TrafficEntry:
        with self._lock:
            self._entries.append(entry)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(entry)
            except Exception:  # noqa: BLE001 - a broken listener must not stop logging
                logging.getLogger(__name__).exception("traffic listener failed")
        return entry

    def subscribe(self, listener: Callable[[TrafficEntry], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[TrafficEntry], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def entries(self) -> List[TrafficEntry]:
        with self._lock:
            return list(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ----------------------------------------------------------- export ---

    def to_csv(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["time", "direction", "can_id", "message", "status", "raw", "decoded", "note"])
        for e in self.entries:
            decoded = ""
            if e.message is not None:
                decoded = "; ".join(f"{f.name}={f.display}" for f in e.message.fields)
            writer.writerow([
                _dt.datetime.fromtimestamp(e.timestamp).isoformat(timespec="milliseconds"),
                e.direction,
                "" if e.can_id is None else f"0x{e.can_id:X}",
                e.name,
                e.status,
                e.hex,
                decoded,
                e.note,
            ])
        return out.getvalue()

    def to_text(self) -> str:
        lines = []
        for e in self.entries:
            can = f" [0x{e.can_id:X}]" if e.can_id is not None else ""
            head = f"{e.time_text}  {e.direction:<5}{can} {e.hex}"
            if e.note:
                head += f"  {e.note}"
            lines.append(head.rstrip())
            if e.message is not None:
                lines.extend("    " + line for line in e.message.summary().splitlines())
        return "\n".join(lines) + ("\n" if lines else "")

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self.to_csv() if path.suffix.lower() == ".csv" else self.to_text()
        path.write_text(text, encoding="utf-8", newline="")
        return path
