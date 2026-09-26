import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from protocol_designer.storage import load_protocol  # noqa: E402

PROTOCOLS_DIR = ROOT / "protocols"


@pytest.fixture
def protocols_dir() -> Path:
    return PROTOCOLS_DIR


@pytest.fixture
def tpms():
    return load_protocol(PROTOCOLS_DIR / "tpms_rs485.json")


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path, monkeypatch):
    """Never touch the real user's settings/documents during tests."""
    monkeypatch.setenv("PROTOCOL_DESIGNER_HOME", str(tmp_path / "user_home"))
    yield
