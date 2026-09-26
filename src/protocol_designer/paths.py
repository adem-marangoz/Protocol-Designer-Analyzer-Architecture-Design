"""Where program files and user data live (design document Section 26.7).

Program files are read-only once installed, so everything the user creates is
stored separately::

    %APPDATA%\\ProtocolDesigner\\settings.json     user settings
    %LOCALAPPDATA%\\ProtocolDesigner\\logs\\        logs
    Documents\\ProtocolDesigner\\Protocols\\        user protocol definitions
    Documents\\ProtocolDesigner\\TestResults\\      test reports
    Documents\\ProtocolDesigner\\Generated\\        generated source code

Setting ``PROTOCOL_DESIGNER_HOME`` puts all of these under one directory
(used by the tests and for portable use).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import APP_SHORT_NAME

HOME_ENV = "PROTOCOL_DESIGNER_HOME"


def _override() -> Optional[Path]:
    value = os.environ.get(HOME_ENV)
    return Path(value) if value else None


def _documents_dir() -> Path:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5 (My Documents), honours folder redirection/OneDrive
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:  # noqa: BLE001 - fall back to the default location
            pass
    return Path.home() / "Documents"


def settings_dir() -> Path:
    base = _override()
    if base:
        return base / "settings"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_SHORT_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_SHORT_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_SHORT_NAME


def settings_file() -> Path:
    return settings_dir() / "settings.json"


def logs_dir() -> Path:
    base = _override()
    if base:
        return base / "logs"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_SHORT_NAME / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_SHORT_NAME
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP_SHORT_NAME / "logs"


def documents_dir() -> Path:
    base = _override()
    if base:
        return base / "Documents"
    return _documents_dir() / APP_SHORT_NAME


def protocols_dir() -> Path:
    return documents_dir() / "Protocols"


def test_results_dir() -> Path:
    return documents_dir() / "TestResults"


test_results_dir.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def generated_dir() -> Path:
    return documents_dir() / "Generated"


def ensure_user_dirs() -> None:
    for d in (settings_dir(), logs_dir(), protocols_dir(), test_results_dir(), generated_dir()):
        d.mkdir(parents=True, exist_ok=True)


def install_dir() -> Path:
    """Directory of the running program (the install folder when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]  # repository root in development


def resource_dir() -> Path:
    """Bundled read-only resources (icons, license texts)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "protocol_designer" / "resources"
    return Path(__file__).resolve().parent / "resources"


def resource(name: str) -> Path:
    return resource_dir() / name


def bundled_examples_dirs() -> List[Path]:
    candidates = [install_dir() / "protocols"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "protocols")
    return [c for c in candidates if c.is_dir()]


def copy_examples(overwrite: bool = False) -> List[Path]:
    """Copy the example protocols into the user's Protocols folder.

    Existing files are never overwritten unless ``overwrite`` is true, so the
    user's edits survive upgrades (Section 26.6).
    """
    target = protocols_dir()
    target.mkdir(parents=True, exist_ok=True)
    copied: List[Path] = []
    for source_dir in bundled_examples_dirs():
        for src in sorted(source_dir.glob("*.json")):
            dst = target / src.name
            if dst.exists() and not overwrite:
                continue
            shutil.copy2(src, dst)
            copied.append(dst)
        break  # first existing examples folder wins
    return copied


def first_run_setup() -> bool:
    """Create user folders and copy examples on first launch. Returns True on first run."""
    marker = settings_dir() / ".initialized"
    ensure_user_dirs()
    if marker.exists():
        return False
    copy_examples()
    marker.write_text("1", encoding="utf-8")
    return True
