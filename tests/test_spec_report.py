"""Protocol specification export (PDF / HTML)."""

import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from helpers import make_protocol

from protocol_designer.application.spec_report import build_spec_html, export, export_html, export_pdf, frame_offsets
from protocol_designer.storage import load_protocol

EXAMPLES = ["tpms_rs485", "tpms_can", "ecu_bootloader"]


def pdf_text(path):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    text = "\n".join(page.extract_text() for page in reader.pages)
    # pypdf reports word gaps as tabs and keeps typographic ligatures ("ﬁ")
    return len(reader.pages), unicodedata.normalize("NFKC", text).replace("\t", " ")


def export_with_cli(protocol_file, out):
    """Export exactly like a user: `pdcli export` in its own process, on the native Qt platform.

    The test session forces QT_QPA_PLATFORM=offscreen, which on Windows has no
    system fonts; the real program never runs that way (on a headless Linux box
    it selects offscreen by itself, where fonts are available).
    """
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "protocol_designer", "cli", "export", str(protocol_file), "--out", str(out)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return Path(out)


def test_html_contains_every_part_of_the_protocol(tpms):
    doc = build_spec_html(tpms, "tpms_rs485.json")
    for text in [
        "TPMS RS485 Protocol", "tpms_rs485.json",                        # title page
        "1. Transport", "115200 baud", "2. Message overview",            # transport + overview
        "3. Enumerations", "SENSOR_STATUS", "NOT_AVAILABLE", "Used by: SENSOR_DATA.STATUS",
        "Byte layout", "bytes 5–6", "Bit field FLAGS", "0x18", "0 = SLEEP, 1 = PARKED",
        "value = raw × 0.1", "unit bar", "enum SENSOR_STATUS", "UINT16 BE",
        "length of SENSOR_ID", "CRC8 over SOF … SENSOR_ID",
        "has a variable size given by the LENGTH field",                  # DEVICE_INFO.NAME
        "AA 01 10 01 00 8F",                                              # example packet
        "Checksum / CRC algorithms", "poly 0x7", "0xF4",                  # CRC parameters + check
        "Tests", "Sensor Read", "PRESSURE min 1.5, max 3.5",
        "Device simulator rules", "SENSOR_DATA", "Validation", "No problems found",
        '&quot;Sensor Active&quot;: true',                              # simulator values as JSON (escaped)
    ]:
        assert text in doc, text
    for frame in tpms.frames:
        assert f"Message {frame.name}" in doc


def test_section_numbers_match_contents(tpms):
    doc = build_spec_html(tpms)
    assert "4.1</td><td>Message READ_SENSOR" in doc and "<h2>4.1 Message READ_SENSOR</h2>" in doc
    assert "<h2>5. Checksum / CRC algorithms</h2>" in doc
    assert "<h2>8. Validation</h2>" in doc


def test_little_endian_and_can_details(protocols_dir):
    can = build_spec_html(load_protocol(protocols_dir / "tpms_can.json"))
    assert "0x300 (11-bit standard)" in can and "29-bit extended" in can
    assert "UINT16 LE" in can and "CRC8_SAE_J1850" in can
    assert "recognised by its CAN identifier" in can
    boot = build_spec_html(load_protocol(protocols_dir / "ecu_bootloader.json"))
    assert "CRC16_MODBUS" in boot and "0x4B37" in boot and "BYTES[var]" in boot
    assert "Response to" in boot


def test_html_is_escaped():
    p = make_protocol([{"name": "M", "description": "<script>x</script>", "fields": [{"name": "A&B", "type": "UINT8"}]}])
    p.name = "<Evil>"
    doc = build_spec_html(p)
    assert "<script>" not in doc and "&lt;script&gt;" in doc
    assert "&lt;Evil&gt;" in doc and "A&amp;B" in doc


def test_invalid_protocol_still_exports_with_its_problems(tmp_path):
    p = make_protocol([{"name": "M", "fields": [{"name": "X", "type": "UINT8"}, {"name": "X", "type": "UINT8"},
                                                 {"name": "C", "type": "UINT8", "encoding": "CONSTANT", "value": "0x1FF"}]}])
    doc = build_spec_html(p)
    assert "duplicate field name" in doc and "No example" in doc
    assert export_pdf(p, tmp_path / "bad.pdf").stat().st_size > 1000


def test_frame_offsets_after_variable_field(protocols_dir):
    frame = load_protocol(protocols_dir / "tpms_rs485.json").frame("DEVICE_INFO")
    offs = {f.name: (o, s) for f, o, s in frame_offsets(frame)}
    assert offs["SERIAL"] == (6, 4)
    assert offs["NAME"] == (10, None)
    assert offs["CRC"] == (None, 1)


def test_pdf_export_in_process(tmp_path, tpms):
    path = export_pdf(tpms, tmp_path / "t.pdf", "tpms_rs485.json")
    assert path.read_bytes()[:5] == b"%PDF-"
    pages, _ = pdf_text(path)
    assert pages >= len(tpms.frames) + 2


@pytest.mark.parametrize("name", EXAMPLES)
def test_pdf_export(tmp_path, protocols_dir, name):
    protocol = load_protocol(protocols_dir / f"{name}.json")
    path = export_with_cli(protocols_dir / f"{name}.json", tmp_path / f"{name}.pdf")
    assert path.read_bytes()[:5] == b"%PDF-"
    pages, text = pdf_text(path)
    assert pages >= len(protocol.frames) + 2  # title/overview + one page per message + CRC
    assert f"Page 1 of {pages}" in text and f"Page {pages} of {pages}" in text
    for frame in protocol.frames:
        assert frame.name in text
    assert "Checksum / CRC algorithms" in text


def test_pdf_text_details(tmp_path, protocols_dir):
    _, text = pdf_text(export_with_cli(protocols_dir / "tpms_rs485.json", tmp_path / "t.pdf"))
    for needle in ["Sensor State", "0 = SLEEP", "AA 01 10 01 00 8F", "CRC8", "Protocol Specification"]:
        assert needle in text, needle


def test_export_by_extension(tmp_path, tpms):
    html_path = export(tpms, tmp_path / "spec.html")
    assert html_path.read_text(encoding="utf-8").startswith("<html>")
    assert export(tpms, tmp_path / "spec.pdf").read_bytes()[:4] == b"%PDF"
    assert export_html(tpms, tmp_path / "sub" / "x.htm").exists()
