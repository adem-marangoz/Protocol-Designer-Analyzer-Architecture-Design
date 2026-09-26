import json
import logging

import pytest

from protocol_designer import paths
from protocol_designer.application.expressions import ExpressionError, evaluate, substitute
from protocol_designer.application.logger import RX, TX, TrafficEntry, TrafficLog, setup_logging
from protocol_designer.application.packet_analyzer import PacketAnalyzer
from protocol_designer.application.packet_builder import PacketBuilder
from protocol_designer.application.protocol_manager import ProtocolManager, safe_filename
from protocol_designer.application.settings import AppSettings
from protocol_designer.protocol import Decoder, Encoder, validate_protocol, has_errors
from protocol_designer.protocol.errors import EncodeError


# ------------------------------------------------------------ expressions ---


def test_substitute():
    v = {"a": 5, "s": "hi", "b": b"\x01\x02"}
    assert substitute("${a}", v) == 5
    assert substitute("x=${a} ${s}", v) == "x=5 hi"
    assert substitute("${b}", v) == b"\x01\x02"
    assert substitute("data ${b}", v) == "data 01 02"
    assert substitute({"k": ["${a}"]}, v) == {"k": [5]}
    with pytest.raises(ExpressionError):
        substitute("${nope}", v)


@pytest.mark.parametrize("expr,expected", [
    ("1 + 2 * 3", 7), ("${x} ^ 0xFF", 0xF0), ("~0 & 0xFF", 0xFF), ("(${x} << 8) | 1", 0x0F01),
    ("-3", -3), ("7 // 2", 3), ("7 % 4", 3), ("10 / 4", 2.5), ("ACTIVE", "ACTIVE"), ("hello world", "hello world"), (5, 5),
])
def test_evaluate(expr, expected):
    assert evaluate(expr, {"x": 0x0F}) == expected


@pytest.mark.parametrize("expr", ["__import__('os')", "x.y", "[1]", "1 if 1 else 2", "1 / 0", "${nope} + 1"])
def test_evaluate_rejects(expr):
    with pytest.raises(ExpressionError):
        evaluate(expr, {"x": 1})


# ---------------------------------------------------------------- builder ---


def test_builder_inputs(tpms):
    inputs = {i.name: i for i in PacketBuilder(tpms).inputs(tpms.frame("SENSOR_DATA"))}
    assert list(inputs) == ["ADDRESS", "SENSOR_ID", "PRESSURE", "TEMPERATURE", "STATUS", "FLAGS"]
    assert inputs["STATUS"].kind == "enum" and inputs["STATUS"].options[1] == "ACTIVE"
    assert inputs["STATUS"].default == "OFF"
    assert inputs["FLAGS"].kind == "bits" and inputs["FLAGS"].bits[3]["options"][2] == "DRIVING"
    assert inputs["PRESSURE"].kind == "number" and inputs["PRESSURE"].hint == "UINT16, bar, 0..50"
    cfg = {i.name: i for i in PacketBuilder(tpms).inputs(tpms.frame("SET_CONFIG"))}
    assert cfg["ENABLED"].kind == "bool" and cfg["ENABLED"].default == "true"
    assert cfg["ALARM_PRESSURE"].default == "1.8"
    info = {i.name: i for i in PacketBuilder(tpms).inputs(tpms.frame("DEVICE_INFO"))}
    assert info["NAME"].kind == "text" and info["NAME"].size is None


def test_builder_build_from_text(tpms):
    b = PacketBuilder(tpms)
    enc = b.build("READ_SENSOR", {"ADDRESS": "0x01", "SENSOR_ID": "5"})
    assert enc.hex == "AA 01 10 01 05 94"
    enc = b.build("SENSOR_DATA", {"PRESSURE": "19.4", "STATUS": "ACTIVE", "FLAGS": {"RF Error": True}})
    msg = Decoder(tpms).decode_any(enc.data)
    assert msg.field("PRESSURE").value == pytest.approx(19.4)
    assert msg.field("FLAGS").bits[2].value == 1
    enc = b.build("SET_CONFIG", {"ENABLED": "no", "PERIOD": ""})
    assert Decoder(tpms).decode_any(enc.data).field("ENABLED").value is False
    with pytest.raises(EncodeError):
        b.build("READ_SENSOR", {"SENSOR_ID": "abc"})
    with pytest.raises(EncodeError):
        b.build("READ_SENSOR", {"NOPE": "1"})
    with pytest.raises(EncodeError):
        b.build("SET_CONFIG", {"ENABLED": "maybe"})


# --------------------------------------------------------------- analyzer ---


def test_analyzer_stream_with_garbage(tpms):
    enc = Encoder(tpms)
    text = "00 " + enc.encode("READ_SENSOR", {"SENSOR_ID": 5}).hex + " " + enc.encode("ACK", {}).hex
    events = PacketAnalyzer(tpms).analyze(text)
    assert [e.kind for e in events] == ["garbage", "message", "message"]
    assert events[1].message.name == "READ_SENSOR"


def test_analyzer_forced_frame(tpms):
    a = PacketAnalyzer(tpms)
    ev = a.analyze("AA 01 10 01 05 94", frame="READ_SENSOR")[0]
    assert ev.message.valid
    ev = a.analyze("AB 01 10 01 05 94", frame="READ_SENSOR")[0]
    assert not ev.message.valid and ev.message.field("SOF").valid is False
    ev = a.analyze("AA 01", frame="READ_SENSOR")[0]
    assert not ev.message.valid and ev.message.errors
    assert a.analyze("") == []


def test_analyzer_can_payload(protocols_dir):
    from protocol_designer.storage import load_protocol

    p = load_protocol(protocols_dir / "tpms_can.json")
    data = Encoder(p).encode("WHEEL_PRESSURE", {"PRESSURE": 2.2}).data
    ev = PacketAnalyzer(p).analyze(data, can_id=0x300)[0]
    assert ev.message.name == "WHEEL_PRESSURE" and ev.message.valid
    # without the CAN id the payload is still recognised as a complete frame
    assert PacketAnalyzer(p).analyze(data)[0].message.name == "WHEEL_PRESSURE"


# ---------------------------------------------------------------- manager ---


def test_manager_file_lifecycle(tmp_path, protocols_dir):
    m = ProtocolManager()
    changes = []
    m.subscribe(lambda: changes.append(m.modified))
    assert m.title == "Untitled"
    m.open(protocols_dir / "tpms_rs485.json")
    assert m.protocol.name == "TPMS RS485 Protocol" and not m.modified
    m.add_frame("PING")
    assert m.modified and m.title.endswith("*")
    path = m.save(tmp_path / "copy.json")
    assert path.exists() and not m.modified
    m.new("Fresh")
    assert m.protocol.name == "Fresh" and m.path is None
    saved = m.save()
    assert saved.parent == paths.protocols_dir() and saved.name == "fresh.json"
    assert m.user_protocols() == [saved]
    assert changes


def test_manager_editing(tpms):
    m = ProtocolManager(tpms)
    frame = m.add_frame()
    assert frame.name == "NEW_MESSAGE" and frame.id == 1
    assert m.add_frame().name == "NEW_MESSAGE_2"
    assert not has_errors(validate_protocol(m.protocol))
    f = m.add_field(frame.name)
    assert frame.fields[-2] is f and frame.fields[-1].name == "CRC"
    assert m.add_field(frame.name).name == "FIELD_2"
    m.move_field(frame.name, "FIELD", -10)
    assert frame.fields[0].name == "FIELD"
    m.remove_field(frame.name, "FIELD")
    dup = m.duplicate_frame("READ_SENSOR")
    assert dup.name == "READ_SENSOR_COPY"
    m.rename_frame("SENSOR_DATA", "SENSOR_DATA_V2")
    assert m.protocol.frame("READ_SENSOR").expected_response == "SENSOR_DATA_V2"
    assert m.protocol.simulator[0].reply == "SENSOR_DATA_V2"
    assert m.protocol.test("Sensor Read").steps[1].message == "SENSOR_DATA_V2"
    with pytest.raises(ValueError):
        m.rename_frame("ACK", "READ_SENSOR")
    with pytest.raises(ValueError):
        m.rename_frame("ACK", "  ")
    m.remove_frame("SENSOR_DATA_V2")
    assert m.protocol.frame("READ_SENSOR").expected_response is None
    m.move_frame("ACK", -100)
    assert m.protocol.frames[0].name == "ACK"
    assert m.add_test("Sensor Read").name == "Sensor Read 2"


def test_safe_filename():
    assert safe_filename("TPMS RS485 Protocol") == "tpms_rs485_protocol"
    assert safe_filename("a/b:c") == "a_b_c"
    assert safe_filename("") == "protocol"


# --------------------------------------------------------------- settings ---


def test_settings_roundtrip(tmp_path):
    s = AppSettings.load(tmp_path / "missing.json")
    assert s.recent_files == []
    for i in range(12):
        s.add_recent(str(tmp_path / f"p{i}.json"))
    s.add_recent(str(tmp_path / "p5.json"))
    assert len(s.recent_files) == 10 and s.recent_files[0].endswith("p5.json")
    assert s.last_protocol.endswith("p5.json")
    s.remove_recent(str(tmp_path / "p5.json"))
    assert not any(p.endswith("p5.json") for p in s.recent_files)
    path = s.save(tmp_path / "settings.json")
    again = AppSettings.load(path)
    assert again == s


def test_settings_default_location_and_bad_file(tmp_path):
    s = AppSettings()
    path = s.save()
    assert path == paths.settings_file()
    path.write_text("{not json")
    assert AppSettings.load() == AppSettings()
    path.write_text(json.dumps({"theme": "dark", "unknown": 1, "recent_files": "bad"}))
    loaded = AppSettings.load()
    assert loaded.theme == "dark" and loaded.recent_files == []


# ------------------------------------------------------------------ paths ---


def test_paths_under_override(tmp_path):
    home = paths._override()
    assert home is not None
    for d in (paths.settings_dir(), paths.logs_dir(), paths.protocols_dir(), paths.test_results_dir()):
        assert str(d).startswith(str(home))


def test_first_run_copies_examples_once(protocols_dir):
    assert paths.first_run_setup() is True
    copied = sorted(p.name for p in paths.protocols_dir().glob("*.json"))
    assert copied == sorted(p.name for p in protocols_dir.glob("*.json"))
    user_file = paths.protocols_dir() / "tpms_rs485.json"
    user_file.write_text("edited")
    assert paths.first_run_setup() is False
    paths.copy_examples()
    assert user_file.read_text() == "edited"  # user edits survive
    paths.copy_examples(overwrite=True)
    assert user_file.read_text() != "edited"


def test_resources_exist():
    assert paths.resource_dir().is_dir()


# ----------------------------------------------------------------- logger ---


def test_traffic_log_and_exports(tmp_path, tpms):
    log = TrafficLog(capacity=3)
    seen = []
    log.subscribe(seen.append)
    log.subscribe(lambda e: 1 / 0)  # a broken listener must not break logging
    data = bytes.fromhex("AA0110010594")
    msg = Decoder(tpms).decode_any(data)
    log.add(TrafficEntry(TX, data, msg))
    log.add(TrafficEntry(RX, b"\x00", None, note="no matching frame"))
    log.add(TrafficEntry(RX, bytes.fromhex("AA0110010500"), Decoder(tpms).decode_any(bytes.fromhex("AA0110010500"))))
    log.add(TrafficEntry("INFO", note="hello"))
    assert len(log) == 3 and len(seen) == 4
    assert [e.status for e in log.entries] == ["UNKNOWN", "CRC ERROR", ""]
    csv_text = log.save(tmp_path / "t.csv").read_text()
    assert csv_text.splitlines()[0].startswith("time,direction")
    txt = log.save(tmp_path / "t.txt").read_text()
    assert "no matching frame" in txt and "READ_SENSOR" in txt
    log.clear()
    assert len(log) == 0


def test_setup_logging(tmp_path):
    logfile = setup_logging(directory=tmp_path)
    logging.getLogger("x").info("hello log")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "hello log" in logfile.read_text()
    setup_logging(directory=tmp_path)  # idempotent: no duplicate handlers
    assert sum(1 for h in logging.getLogger().handlers if getattr(h, "_protocol_designer", False)) == 1
