"""Persistent user settings (``%APPDATA%\\ProtocolDesigner\\settings.json``)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import List, Optional

from .. import paths

log = logging.getLogger(__name__)

MAX_RECENT = 10


@dataclass
class AppSettings:
    recent_files: List[str] = field(default_factory=list)
    last_protocol: str = ""
    idle_flush_ms: int = 50
    monitor_max_rows: int = 5000
    show_garbage: bool = True
    theme: str = "system"  # system | light | dark
    window_geometry: str = ""
    window_state: str = ""
    accepted_license_version: str = ""

    def add_recent(self, path: str) -> None:
        path = str(Path(path))
        self.recent_files = [p for p in self.recent_files if Path(p) != Path(path)]
        self.recent_files.insert(0, path)
        del self.recent_files[MAX_RECENT:]
        self.last_protocol = path

    def remove_recent(self, path: str) -> None:
        self.recent_files = [p for p in self.recent_files if Path(p) != Path(path)]

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppSettings":
        path = path or paths.settings_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable settings file %s: %s", path, exc)
            return cls()
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in data.items() if k in known})
        if not isinstance(settings.recent_files, list):
            settings.recent_files = []
        return settings

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or paths.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".settings-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)
        return path
