"""Test Runner (Section 19): define, run and report protocol tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..application.test_engine import PASS, StepResult, TestEngine, TestReport
from ..protocol.validator import ERROR, validate_protocol
from ..storage.json_loader import parse_test
from ..storage.json_writer import test_to_dict
from .widgets import ERROR_COLOR, OK_COLOR, WARN_COLOR, Page, confirm, hint_label, mono_font

STEP_TEMPLATES = {
    "send": {"action": "send", "message": "", "values": {}},
    "expect": {"action": "expect", "message": "", "timeout_ms": 500, "checks": [{"field": "", "equals": 0}]},
    "transact": {"action": "transact", "message": "", "values": {}, "checks": [{"field": "", "min": 0, "max": 100}]},
    "send_raw": {"action": "send_raw", "data": "AA 01 10 01 05 94"},
    "delay": {"action": "delay", "delay_ms": 100},
    "set": {"action": "set", "variable": "x", "value": "${seed} ^ 0x5A5A5A5A"},
    "log": {"action": "log", "text": "value is ${x}"},
}


class _Signals(QObject):
    step = Signal(object)
    finished = Signal(object)


class TestPage(Page):
    __test__ = False  # not a pytest test class
    key = "tests"
    title = "Test Runner"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.layout_.addWidget(hint_label(
            "A test sends messages, waits for responses and verifies fields. Steps: send, expect, transact "
            "(send + wait for the message's expected response), send_raw, delay, set (variables such as "
            "${seed}), log. Reports are saved to Documents\\ProtocolDesigner\\TestResults."
        ))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.layout_.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Tests"))
        self.tests = QListWidget()
        self.tests.currentTextChanged.connect(self._show_test)
        lv.addWidget(self.tests, 1)
        row = QHBoxLayout()
        self.btn_new = QPushButton("+ New")
        self.btn_rename = QPushButton("Rename")
        self.btn_delete = QPushButton("Delete")
        for b in (self.btn_new, self.btn_rename, self.btn_delete):
            row.addWidget(b)
        lv.addLayout(row)
        split.addWidget(left)

        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.addWidget(QLabel("Definition (JSON)"))
        self.editor = QPlainTextEdit()
        self.editor.setFont(mono_font())
        mv.addWidget(self.editor, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Insert step:"))
        self.template = QComboBox()
        self.template.addItems(list(STEP_TEMPLATES))
        self.btn_insert = QPushButton("Insert")
        self.btn_apply = QPushButton("Apply changes")
        row.addWidget(self.template)
        row.addWidget(self.btn_insert)
        row.addStretch(1)
        row.addWidget(self.btn_apply)
        mv.addLayout(row)
        split.addWidget(mid)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.btn_run = QPushButton("RUN TEST")
        self.btn_run_all = QPushButton("Run all")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_run_all)
        row.addWidget(self.btn_stop)
        rv.addLayout(row)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Check", "Details"])
        self.results.setColumnWidth(0, 220)
        rv.addWidget(self.results, 1)
        self.result_label = QLabel()
        font = self.result_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.result_label.setFont(font)
        rv.addWidget(self.result_label)
        row = QHBoxLayout()
        self.btn_open_report = QPushButton("Open last report")
        self.btn_open_report.setEnabled(False)
        self.btn_open_folder = QPushButton("Open results folder")
        row.addWidget(self.btn_open_report)
        row.addWidget(self.btn_open_folder)
        rv.addLayout(row)
        split.addWidget(right)
        split.setSizes([200, 420, 420])

        self.btn_new.clicked.connect(self._new_test)
        self.btn_rename.clicked.connect(self._rename_test)
        self.btn_delete.clicked.connect(self._delete_test)
        self.btn_insert.clicked.connect(self._insert_step)
        self.btn_apply.clicked.connect(self.apply)
        self.btn_run.clicked.connect(lambda: self.run([self.tests.currentItem().text()] if self.tests.currentItem() else []))
        self.btn_run_all.clicked.connect(lambda: self.run([t.name for t in self.ctx.protocol.tests]))
        self.btn_stop.clicked.connect(self.stop)
        self.btn_open_report.clicked.connect(lambda: self._open(self.last_report_path))
        self.btn_open_folder.clicked.connect(lambda: self._open(paths.test_results_dir()))

        self.signals = _Signals()
        self.signals.step.connect(self._on_step)
        self.signals.finished.connect(self._on_finished)
        self.engine: Optional[TestEngine] = None
        self.thread: Optional[threading.Thread] = None
        self.reports: List[TestReport] = []
        self.last_report_path: Optional[Path] = None
        self._queue: List[str] = []
        self._busy = False
        self._current_item: Optional[QTreeWidgetItem] = None
        self.on_protocol_changed()

    # ------------------------------------------------------------ editing ---

    def on_protocol_changed(self) -> None:
        current = self.tests.currentItem().text() if self.tests.currentItem() else ""
        self.tests.blockSignals(True)
        self.tests.clear()
        self.tests.addItems([t.name for t in self.ctx.protocol.tests])
        self.tests.blockSignals(False)
        items = self.tests.findItems(current, Qt.MatchFlag.MatchExactly) if current else []
        if items:
            self.tests.setCurrentItem(items[0])
        elif self.tests.count():
            self.tests.setCurrentRow(0)
        self._show_test(self.tests.currentItem().text() if self.tests.currentItem() else "")

    def _show_test(self, name: str) -> None:
        if not name or not any(t.name == name for t in self.ctx.protocol.tests):
            self.editor.setPlainText("")
            return
        self.editor.setPlainText(json.dumps(test_to_dict(self.ctx.protocol.test(name)), indent=2, ensure_ascii=False))

    def _new_test(self) -> None:
        test = self.ctx.manager.add_test()
        first = self.ctx.protocol.frames[0].name if self.ctx.protocol.frames else ""
        test.steps = [parse_test({"steps": [dict(STEP_TEMPLATES["send"], message=first)]}).steps[0]] if first else []
        self.ctx.mark_modified()
        self.on_protocol_changed()
        self.tests.setCurrentItem(self.tests.findItems(test.name, Qt.MatchFlag.MatchExactly)[0])

    def _rename_test(self) -> None:
        item = self.tests.currentItem()
        if not item:
            return
        name, ok = QInputDialog.getText(self, "Rename test", "Test name:", text=item.text())
        name = name.strip()
        if ok and name and name != item.text():
            if any(t.name == name for t in self.ctx.protocol.tests):
                self.error(f"A test named '{name}' already exists.")
                return
            self.ctx.protocol.test(item.text()).name = name
            self.ctx.mark_modified()
            self.on_protocol_changed()
            self.tests.setCurrentItem(self.tests.findItems(name, Qt.MatchFlag.MatchExactly)[0])

    def _delete_test(self) -> None:
        item = self.tests.currentItem()
        if item and confirm(self, "Delete test", f"Delete test '{item.text()}'?"):
            self.ctx.protocol.tests.remove(self.ctx.protocol.test(item.text()))
            self.ctx.mark_modified()
            self.on_protocol_changed()

    def _insert_step(self) -> None:
        try:
            data = json.loads(self.editor.toPlainText() or "{}")
        except ValueError as exc:
            self.error(f"The definition is not valid JSON: {exc}")
            return
        step = json.loads(json.dumps(STEP_TEMPLATES[self.template.currentText()]))
        if "message" in step and self.ctx.protocol.frames:
            step["message"] = self.ctx.protocol.frames[0].name
        data.setdefault("steps", []).append(step)
        self.editor.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))

    def apply(self) -> bool:
        item = self.tests.currentItem()
        if item is None:
            return False
        try:
            data = json.loads(self.editor.toPlainText())
            if not isinstance(data, dict):
                raise ValueError("the definition must be a JSON object")
            new = parse_test(data)
        except (ValueError, AttributeError, TypeError) as exc:
            self.error(f"Invalid test definition: {exc}")
            return False
        if not new.name:
            new.name = item.text()
        if new.name != item.text() and any(t.name == new.name for t in self.ctx.protocol.tests):
            self.error(f"A test named '{new.name}' already exists.")
            return False
        tests = self.ctx.protocol.tests
        tests[tests.index(self.ctx.protocol.test(item.text()))] = new
        self.ctx.mark_modified()
        problems = [str(i) for i in validate_protocol(self.ctx.protocol)
                    if i.location.startswith(f"test {new.name}") and i.severity == ERROR]
        self.on_protocol_changed()
        self.tests.setCurrentItem(self.tests.findItems(new.name, Qt.MatchFlag.MatchExactly)[0])
        if problems:
            self.error("Saved, but the test has problems:\n" + "\n".join(problems), "Test problems")
        return True

    # ------------------------------------------------------------ running ---

    @property
    def running(self) -> bool:
        """True until the last report has been processed on the GUI thread."""
        return self._busy

    def run(self, names: List[str]) -> bool:
        if self.running or not names:
            return False
        self._busy = True
        self._queue = list(names)
        self.results.clear()
        self.reports = []
        self.result_label.setText("Running…")
        self.result_label.setStyleSheet("")
        self._set_running(True)
        self._start_next()
        return True

    def _start_next(self) -> None:
        name = self._queue.pop(0)
        test = self.ctx.protocol.test(name)
        session = self.ctx.ensure_session()
        self._current_item = QTreeWidgetItem([f"Test: {test.name}", "running…"])
        font = self._current_item.font(0)
        font.setBold(True)
        self._current_item.setFont(0, font)
        self.results.addTopLevelItem(self._current_item)
        self._current_item.setExpanded(True)
        self.engine = TestEngine(session, progress=self._progress)

        def work(engine=self.engine, test=test):
            report = engine.run(test)
            self.signals.finished.emit(report)

        self.thread = threading.Thread(target=work, name="test-runner", daemon=True)
        self.thread.start()

    def _progress(self, kind: str, payload) -> None:  # worker thread
        if kind == "step":
            self.signals.step.emit(payload)

    def _add_check(self, parent: QTreeWidgetItem, mark: str, name: str, detail: str, ok: bool) -> None:
        item = QTreeWidgetItem([f"{mark} {name}", detail])
        item.setForeground(0, OK_COLOR if ok else ERROR_COLOR)
        parent.addChild(item)

    def _on_step(self, step: StepResult) -> None:
        parent = self._current_item
        if parent is None:
            return
        for check in step.checks:
            self._add_check(parent, check.mark, check.name, check.detail, check.passed)
        if step.error:
            self._add_check(parent, "✗", f"Step {step.index} {step.action}", step.error, False)
        self.results.scrollToBottom()

    def _on_finished(self, report: TestReport) -> None:
        item = self._current_item
        if item is not None:
            if report.connection is not None:
                c = report.connection
                first = QTreeWidgetItem([f"{c.mark} Connection", c.detail])
                first.setForeground(0, OK_COLOR if c.passed else ERROR_COLOR)
                item.insertChild(0, first)
            for note in report.logs:
                item.addChild(QTreeWidgetItem(["• log", note]))
            item.setText(1, f"RESULT: {report.result} ({report.duration_ms:.0f} ms)")
            item.setForeground(1, OK_COLOR if report.result == PASS else ERROR_COLOR)
        self.reports.append(report)
        try:
            self.last_report_path = report.save()
            self.btn_open_report.setEnabled(True)
        except OSError as exc:
            self.ctx.status_message.emit(f"Could not save the report: {exc}")
        if self._queue and not report.aborted:
            self._start_next()
            return
        self._set_running(False)
        self._busy = False
        passed = sum(1 for r in self.reports if r.passed)
        overall = PASS if passed == len(self.reports) else "FAIL"
        self.result_label.setText(f"RESULT: {overall}" + (f"  ({passed}/{len(self.reports)} passed)" if len(self.reports) > 1 else ""))
        colour = OK_COLOR if overall == PASS else (WARN_COLOR if any(r.aborted for r in self.reports) else ERROR_COLOR)
        self.result_label.setStyleSheet(f"color: {colour.name()}")
        self.ctx.status_message.emit(f"Tests finished: {passed}/{len(self.reports)} passed")

    def stop(self) -> None:
        self._queue = []
        if self.engine is not None:
            self.engine.cancel()

    def _set_running(self, running: bool) -> None:
        self.btn_run.setEnabled(not running)
        self.btn_run_all.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def _open(self, path) -> None:
        if not path:
            return
        path = Path(path)
        if not path.suffix:  # a folder: make sure it exists before opening it
            path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
