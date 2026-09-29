"""GUI tests (offscreen Qt). Modal dialogs are replaced so nothing blocks."""

import json
import shutil

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from protocol_designer import paths  # noqa: E402
from protocol_designer.application.settings import AppSettings  # noqa: E402
from protocol_designer.protocol.model import Encoding, TransportType  # noqa: E402
from protocol_designer.ui.about import AboutDialog  # noqa: E402
from protocol_designer.ui.context import AppContext  # noqa: E402
from protocol_designer.ui.main_window import PAGES, MainWindow  # noqa: E402


class Dialogs:
    def __init__(self):
        self.messages = []
        self.question_answer = QMessageBox.StandardButton.Discard
        self.save_path = ""
        self.open_path = ""
        self.text_answer = ("", False)

    def record(self, kind):
        def fn(parent, title, text, *args, **kwargs):
            self.messages.append((kind, title, text))
            if kind == "question":
                return self.question_answer
            return QMessageBox.StandardButton.Ok
        return fn


@pytest.fixture
def dialogs(monkeypatch):
    d = Dialogs()
    for kind in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(d.record(kind)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (d.save_path, "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (d.open_path, "")))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: d.text_answer))
    return d


@pytest.fixture
def window(qtbot, dialogs, protocols_dir, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for f in protocols_dir.glob("*.json"):
        shutil.copy(f, work / f.name)
    ctx = AppContext(AppSettings())
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    assert w.open_file(str(work / "tpms_rs485.json"), ask=False)
    w.show()
    w.work_dir = work
    yield w
    ctx.disconnect_session()
    w.pages["tests"].stop()


def errors(dialogs):
    return [m for m in dialogs.messages if m[0] in ("warning", "critical")]


# -------------------------------------------------------------- window ---


def test_all_pages_open(window, qtbot):
    for cls in PAGES:
        window.show_page(cls.key)
        assert window.current_page.key == cls.key
    assert "TPMS RS485 Protocol" in window.windowTitle()
    assert window.nav.count() == len(PAGES)


def test_dashboard_shows_protocol(window):
    dash = window.pages["dashboard"]
    dash.refresh()
    assert "TPMS RS485 Protocol" in dash.name.text()
    assert "7 message(s)" in dash.counts.text()
    assert dash.state.text().startswith("✓")
    assert dash.recent.count() >= 1


def test_open_invalid_file_reports_error(window, dialogs, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert not window.open_file(str(bad), ask=False)
    assert errors(dialogs) and "invalid JSON" in errors(dialogs)[0][2]


def test_open_via_dialog(window, dialogs):
    dialogs.open_path = str(window.work_dir / "ecu_bootloader.json")
    window.open_dialog()
    assert window.ctx.protocol.name == "ECU Bootloader"


def test_new_save_as_and_unsaved_prompt(window, dialogs, tmp_path):
    window.ctx.protocol.name = "Changed"
    window.ctx.mark_modified()
    assert window.windowTitle().startswith("tpms_rs485.json *")
    dialogs.question_answer = QMessageBox.StandardButton.Cancel
    window.new_protocol()
    assert window.ctx.protocol.name == "Changed"  # cancelled
    dialogs.question_answer = QMessageBox.StandardButton.Discard
    window.new_protocol()
    assert window.ctx.protocol.name == "New Protocol"
    assert window.ctx.protocol.has_frame("READ_SENSOR")
    dialogs.save_path = str(tmp_path / "saved.pdproj")
    assert window.save()  # no path yet -> Save As
    assert (tmp_path / "saved.pdproj").exists()
    assert not window.ctx.manager.modified
    assert window.ctx.settings.recent_files[0].endswith("saved.pdproj")


def test_close_with_unsaved_changes_can_save(window, dialogs):
    window.ctx.protocol.description = "edited"
    window.ctx.mark_modified()
    dialogs.question_answer = QMessageBox.StandardButton.Save
    assert window.close()
    text = (window.work_dir / "tpms_rs485.json").read_text(encoding="utf-8")
    assert "edited" in text


def test_about_dialog(qtbot):
    dlg = AboutDialog(None, "license")
    qtbot.addWidget(dlg)
    assert dlg.tabs.count() == 3
    assert dlg.tabs.currentIndex() == 1
    assert "LICENSE" in dlg.tabs.widget(1).toPlainText().upper()


# ------------------------------------------------------------ protocol ---


def test_protocol_page_edits(window, dialogs):
    page = window.pages["protocols"]
    page.name.setText("Renamed Protocol")
    page.name.editingFinished.emit()
    assert window.ctx.protocol.name == "Renamed Protocol"
    assert window.ctx.manager.modified

    page.t_type.setCurrentText("TCP")
    assert window.ctx.protocol.transport.type == TransportType.TCP
    page.t_host.setText("192.168.1.10")
    page.t_host.editingFinished.emit()
    assert window.ctx.protocol.transport.host == "192.168.1.10"
    page.t_options.setText("[1]")
    page.t_options.editingFinished.emit()
    assert errors(dialogs)  # options must be an object


def test_protocol_page_messages(window, dialogs):
    page = window.pages["protocols"]
    dialogs.text_answer = ("ping request", True)
    page._add_message()
    assert window.ctx.protocol.has_frame("PING_REQUEST")
    assert page.selected_message() == "PING_REQUEST"
    page._duplicate_message()
    assert window.ctx.protocol.has_frame("PING_REQUEST_COPY")
    dialogs.question_answer = QMessageBox.StandardButton.Yes
    page._remove_message()
    assert not window.ctx.protocol.has_frame("PING_REQUEST_COPY")
    page._select("PING_REQUEST")
    page._open_selected()
    assert window.current_page.key == "messages"
    assert window.pages["messages"].selector.currentText() == "PING_REQUEST"


def test_enum_editor(window, dialogs):
    page = window.pages["protocols"]
    dialogs.text_answer = ("gear", True)
    page._add_enum()
    assert "GEAR" in window.ctx.protocol.enums
    page._add_enum_value()
    assert page.apply_enum()
    assert window.ctx.protocol.enums["GEAR"].values == {0: "OFF", 1: "ON", 2: "VALUE_2"}
    page.enum_table.item(0, 0).setText("0x01")
    assert not page.apply_enum()  # duplicate value
    assert "used twice" in errors(dialogs)[-1][2]


def test_simulator_rules_editor(window, dialogs):
    page = window.pages["protocols"]
    page.sim_edit.setPlainText('[{"on": "READ_SENSOR", "reply": "SENSOR_DATA", "values": {"PRESSURE": 3.3}}]')
    assert page.apply_simulator()
    assert window.ctx.protocol.simulator[0].values == {"PRESSURE": 3.3}
    page.sim_edit.setPlainText("{broken")
    assert not page.apply_simulator()


# ------------------------------------------------------------- message ---


def test_message_editor_field_edit(window, dialogs):
    window.show_page("messages", "SENSOR_DATA")
    page = window.pages["messages"]
    assert page.selector.currentText() == "SENSOR_DATA"
    page._select_field("PRESSURE")
    assert page.e_name.text() == "PRESSURE" and page.e_scale.text() == "0.1"
    page.e_scale.setText("0.01")
    page.e_unit.setText("kPa")
    assert page.apply_field()
    f = window.ctx.protocol.frame("SENSOR_DATA").field("PRESSURE")
    assert f.scale == 0.01 and f.unit == "kPa"
    assert "Scale 0.01" in page.table.item(5, 5).text()
    page.e_name.setText("PRESS")
    assert page.apply_field()
    assert window.ctx.protocol.frame("SENSOR_DATA").has_field("PRESS")


def test_message_editor_rejects_invalid_field(window, dialogs):
    window.show_page("messages", "READ_SENSOR")
    page = window.pages["messages"]
    page._select_field("ADDRESS")
    page.e_encoding.setCurrentText("CONSTANT")
    page.e_value.setText("")
    assert not page.apply_field()
    assert "CONSTANT field needs a value" in errors(dialogs)[-1][2]
    page.e_encoding.setCurrentText("VALUE")
    page.e_type.setCurrentText("UINT16")
    page.e_size.setValue(3)
    assert not page.apply_field()
    page.e_size.setValue(0)
    page.e_scale.setText("0")
    assert not page.apply_field()


def test_message_editor_add_bitfield(window, dialogs):
    window.show_page("messages", "READ_SENSOR")
    page = window.pages["messages"]
    page._select_field("SENSOR_ID")
    page._add_field()
    new = page.current_field()
    assert new.name == "FIELD"
    page.e_type.setCurrentText("BITFIELD")
    page._add_bit()
    page._add_bit()
    assert page.apply_field()
    bits = window.ctx.protocol.frame("READ_SENSOR").field("FIELD").bits
    assert [(b.name, b.start) for b in bits] == [("Bit 0", 0), ("Bit 1", 1)]
    assert "7 bytes" in page.preview.toPlainText()


def test_message_properties(window, dialogs):
    window.show_page("messages", "ACK")
    page = window.pages["messages"]
    page.m_name.setText("acknowledge")
    page.m_can.setText("0x123")
    assert page.apply_message()
    frame = window.ctx.protocol.frame("ACKNOWLEDGE")
    assert frame.can_id == 0x123
    assert window.ctx.protocol.simulator[1].reply == "ACKNOWLEDGE"
    page.m_id.setText("xyz")
    assert not page.apply_message()


# ------------------------------------------------------------- builder ---


def test_builder_builds_doc_packet(window, dialogs):
    window.show_page("builder", "READ_SENSOR")
    page = window.pages["builder"]
    page.set_value("ADDRESS", "1")
    page.set_value("SENSOR_ID", "5")
    assert page.build()
    assert page.output.text() == "AA 01 10 01 05 94"
    assert page.layout_table.item(5, 3).text() == "0x94"
    page.set_value("SENSOR_ID", "99")
    assert not page.build()
    assert "above maximum" in page.status.text()
    assert not page.send()


def test_builder_bits_and_enums(window):
    window.show_page("builder", "SENSOR_DATA")
    page = window.pages["builder"]
    page.set_value("STATUS", "ERROR")
    page.set_value("FLAGS", {"RF Error": True, "Sensor State": "ALARM"})
    page.set_value("PRESSURE", "2.5")
    assert page.build()
    values = page.values()
    assert values["FLAGS"]["RF Error"] is True and values["STATUS"] == "ERROR"
    flags_row = [r for r in range(page.layout_table.rowCount()) if page.layout_table.item(r, 0).text() == "FLAGS"][0]
    assert "Sensor State=ALARM" in page.layout_table.item(flags_row, 3).text()


# ------------------------------------------------------------ analyzer ---


def test_analyzer(window):
    window.show_page("analyzer")
    page = window.pages["analyzer"]
    page.input.setPlainText("00 AA 01 10 01 05 94 AA 01 10 01 05 00")
    assert page.analyze() == 2
    assert page.tree.topLevelItem(0).text(0).startswith("??")  # garbage first, in order
    assert page.tree.topLevelItem(1).text(1) == "VALID"
    assert page.tree.topLevelItem(2).text(1) == "CRC ERROR"
    assert "1 valid, 1 invalid, 1 unrecognised" in page.summary.text()
    page.frame.setCurrentText("ACK")
    page.input.setPlainText("AA 01 80 01 20 C6")
    assert page.analyze() == 1
    page.input.setPlainText("zz")
    assert page.analyze() == 0 and page.summary.text().startswith("✗")


# ------------------------------------------------------------- monitor ---


def test_monitor_connect_send_receive(window, qtbot, dialogs):
    window.show_page("monitor")
    page = window.pages["monitor"]
    page.toggle_connection()
    qtbot.waitUntil(lambda: window.ctx.connected, timeout=3000)
    qtbot.waitUntil(lambda: page.btn_connect.text() == "Disconnect", timeout=3000)
    page.send_msg.setCurrentText("READ_SENSOR")
    assert page.send_selected_message()
    qtbot.waitUntil(lambda: any(e.name == "SENSOR_DATA" for e in page.model.rows), timeout=3000)
    page.raw.setText("AA 01 30 00 F1")
    assert page.send_raw()
    qtbot.waitUntil(lambda: any(e.name == "DEVICE_INFO" for e in page.model.rows), timeout=3000)
    row = next(i for i, e in enumerate(page.model.rows) if e.name == "SENSOR_DATA")
    page.table.selectRow(row)
    assert page.tree.topLevelItem(0).text(0) == "SENSOR_DATA"
    page.show_tx.setChecked(False)
    assert all(e.direction != "TX" for e in page.model.rows)
    # the builder can send while connected
    window.show_page("builder", "GET_INFO")
    assert window.pages["builder"].send()
    page.clear()
    assert page.model.rowCount() == 0
    window.toggle_connection()
    qtbot.waitUntil(lambda: not window.ctx.connected, timeout=3000)
    assert "Disconnected" in window.conn_status.text()


def test_monitor_connection_failure(window, qtbot, dialogs):
    window.ctx.protocol.transport.type = TransportType.UART
    window.ctx.protocol.transport.port = "/dev/nonexistent-xyz"
    page = window.pages["monitor"]
    page.toggle_connection()
    qtbot.waitUntil(lambda: bool(errors(dialogs)), timeout=3000)
    assert "Cannot connect" in errors(dialogs)[0][2]
    assert page.btn_connect.text() == "Connect"


def test_monitor_export(window, qtbot, dialogs, tmp_path, monkeypatch):
    page = window.pages["monitor"]
    page.toggle_connection()
    qtbot.waitUntil(lambda: window.ctx.connected, timeout=3000)
    page.send_selected_message()
    qtbot.waitUntil(lambda: len(page.model.rows) >= 3, timeout=3000)
    target = tmp_path / "traffic.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    page._export()
    assert target.read_text(encoding="utf-8").startswith("time,direction")


# --------------------------------------------------------------- tests ---


def test_run_all_tests(window, qtbot):
    window.show_page("tests")
    page = window.pages["tests"]
    assert page.tests.count() == 3
    assert page.run([t.name for t in window.ctx.protocol.tests])
    qtbot.waitUntil(lambda: not page.running and len(page.reports) == 3, timeout=10000)
    assert page.result_label.text().startswith("RESULT: PASS")
    assert page.results.topLevelItemCount() == 3
    first = page.results.topLevelItem(0)
    assert first.child(0).text(0) == "✓ Connection"
    assert "PASS" in first.text(1)
    assert list(paths.test_results_dir().glob("*.html"))
    assert page.last_report_path.exists()


def test_failing_test_and_stop(window, qtbot):
    page = window.pages["tests"]
    page.tests.setCurrentRow(0)
    data = json.loads(page.editor.toPlainText())
    data["steps"][1]["checks"][0]["equals"] = 6
    page.editor.setPlainText(json.dumps(data))
    assert page.apply()
    page.run(["Sensor Read"])
    qtbot.waitUntil(lambda: not page.running and bool(page.reports), timeout=5000)
    assert "FAIL" in page.result_label.text()
    data["steps"] = [{"action": "delay", "delay_ms": 10000}]
    page.editor.setPlainText(json.dumps(data))
    assert page.apply()
    page.run(["Sensor Read"])
    qtbot.waitUntil(lambda: page.running, timeout=2000)
    page.stop()
    qtbot.waitUntil(lambda: not page.running, timeout=3000)
    assert page.reports[-1].aborted


def test_test_editor_operations(window, dialogs):
    page = window.pages["tests"]
    page._new_test()
    assert page.tests.currentItem().text() == "New Test"
    page.template.setCurrentText("delay")
    page._insert_step()
    assert json.loads(page.editor.toPlainText())["steps"][-1] == {"action": "delay", "delay_ms": 100}
    assert page.apply()
    assert window.ctx.protocol.test("New Test").steps[-1].delay_ms == 100
    dialogs.text_answer = ("Smoke", True)
    page._rename_test()
    assert window.ctx.protocol.tests[-1].name == "Smoke"
    page.editor.setPlainText("[1, 2]")
    assert not page.apply()
    page.editor.setPlainText('{"name": "Smoke", "steps": [{"action": "send", "message": "NOPE"}]}')
    assert page.apply()  # saved, but the problem is reported
    assert "unknown message" in errors(dialogs)[-1][2]
    dialogs.question_answer = QMessageBox.StandardButton.Yes
    page._delete_test()
    assert not any(t.name == "Smoke" for t in window.ctx.protocol.tests)


# ------------------------------------------------------------- codegen ---


@pytest.mark.parametrize("language,expected", [("c", {"x.h", "x.c"}), ("cpp", {"x.h", "x.c", "x.hpp"}),
                                                ("python", {"x.py"}), ("csharp", {"x.cs"})])
def test_codegen_page(window, tmp_path, language, expected):
    window.show_page("codegen")
    page = window.pages["codegen"]
    page.basename.setText("x")
    page.out_dir.setText(str(tmp_path / "gen"))
    page.language.setCurrentIndex(page.language.findData(language))
    assert page.preview()
    assert {page.tabs.tabText(i) for i in range(page.tabs.count())} == expected
    written = page.save()
    assert {p.name for p in written} == expected


def test_codegen_page_reports_protocol_errors(window, dialogs):
    frame = window.ctx.protocol.frame("ACK")
    frame.fields.append(frame.fields[0])  # duplicate field name -> invalid
    page = window.pages["codegen"]
    assert not page.preview()
    assert page.tabs.tabText(0) == "Error"
    assert page.save() == []


# ---------------------------------------------------------------- logs ---


def test_logs_page(window, qtbot):
    window.ctx.connect_session()
    window.ctx.session.send_message("READ_SENSOR", {})
    qtbot.waitUntil(lambda: len(window.ctx.traffic) >= 3, timeout=3000)
    window.show_page("logs")
    page = window.pages["logs"]
    assert "READ_SENSOR" in page.traffic.toPlainText()


def test_open_initial_prefers_argument(qtbot, dialogs, protocols_dir):
    ctx = AppContext(AppSettings())
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    w.open_initial(str(protocols_dir / "tpms_can.json"))
    assert ctx.protocol.name == "TPMS CAN Protocol"
    w2 = MainWindow(AppContext(AppSettings()))
    qtbot.addWidget(w2)
    paths.copy_examples()
    w2.open_initial(None)  # falls back to a protocol in the user's folder
    assert w2.ctx.manager.path is not None


def test_field_encoding_constants_listed(window):
    page = window.pages["messages"]
    assert [page.e_encoding.itemText(i) for i in range(page.e_encoding.count())] == [e.value for e in Encoding]


def test_export_specification_from_gui(window, dialogs, tmp_path):
    target = tmp_path / "exports" / "tpms_spec"
    dialogs.save_path = str(target)  # no extension: .pdf is added
    dialogs.question_answer = QMessageBox.StandardButton.No  # don't open a viewer
    saved = window.export_specification()
    assert saved == target.with_suffix(".pdf")
    assert saved.read_bytes()[:4] == b"%PDF"
    assert any(m[1] == "Specification exported" for m in dialogs.messages)
    dash = window.pages["dashboard"]
    assert "Export specification (PDF)…" in dash.action_buttons
    dialogs.save_path = ""  # cancelled dialog
    assert window.export_specification() is None
