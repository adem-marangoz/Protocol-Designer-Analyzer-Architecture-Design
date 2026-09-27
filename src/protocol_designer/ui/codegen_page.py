"""Code Generator (Section 24): C, C++, Python and C# from the protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
)

from .. import paths
from ..application.codegen import LANGUAGES, generate, write_files
from ..application.codegen.plan import snake
from ..protocol.errors import ProtocolError
from .widgets import Page, hint_label, mono_font


class CodegenPage(Page):
    key = "codegen"
    title = "Code Generator"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.layout_.addWidget(hint_label(
            "Generate encoders and decoders from the same protocol definition. The generated code implements "
            "constants, LENGTH and CRC exactly like this tool and needs no external library."
        ))
        form = QFormLayout()
        self.language = QComboBox()
        for key, (label, _) in LANGUAGES.items():
            self.language.addItem(label, key)
        self.basename = QLineEdit()
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("default: derived from the protocol name")
        out_row = QHBoxLayout()
        self.out_dir = QLineEdit(str(paths.generated_dir()))
        self.btn_browse = QPushButton("Browse…")
        out_row.addWidget(self.out_dir, 1)
        out_row.addWidget(self.btn_browse)
        form.addRow("Language", self.language)
        form.addRow("File name", self.basename)
        form.addRow("Identifier prefix", self.prefix)
        form.addRow("Output folder", out_row)
        self.layout_.addLayout(form)
        row = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_save = QPushButton("Generate files")
        self.btn_open = QPushButton("Open output folder")
        row.addWidget(self.btn_preview)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        row.addWidget(self.btn_open)
        self.layout_.addLayout(row)
        self.tabs = QTabWidget()
        self.layout_.addWidget(self.tabs, 1)

        self.btn_browse.clicked.connect(self._browse)
        self.btn_preview.clicked.connect(self.preview)
        self.btn_save.clicked.connect(self.save)
        self.btn_open.clicked.connect(self._open_folder)
        self.language.currentIndexChanged.connect(lambda _: self.preview())
        self.files: Dict[str, str] = {}
        self.on_protocol_changed()

    def on_protocol_changed(self) -> None:
        self.basename.setText(snake(self.ctx.protocol.name))
        self.tabs.clear()
        self.files = {}

    def on_show(self, argument=None) -> None:
        self.preview()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir.text())
        if folder:
            self.out_dir.setText(folder)

    def _generate(self) -> Dict[str, str]:
        return generate(
            self.ctx.protocol,
            self.language.currentData(),
            basename=self.basename.text().strip() or None,
            prefix=self.prefix.text().strip() or None,
        )

    def preview(self) -> bool:
        self.tabs.clear()
        try:
            self.files = self._generate()
        except (ProtocolError, ValueError) as exc:
            self.files = {}
            text = QPlainTextEdit(f"Cannot generate code:\n{exc}")
            text.setReadOnly(True)
            self.tabs.addTab(text, "Error")
            return False
        for name, source in self.files.items():
            view = QPlainTextEdit(source)
            view.setReadOnly(True)
            view.setFont(mono_font())
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.tabs.addTab(view, name)
        return True

    def save(self) -> list:
        if not self.preview():
            self.error("Fix the protocol errors shown on the Dashboard before generating code.")
            return []
        try:
            written = write_files(self.files, Path(self.out_dir.text()))
        except OSError as exc:
            self.error(f"Cannot write files: {exc}")
            return []
        self.ctx.status_message.emit(f"Generated {len(written)} file(s) in {self.out_dir.text()}")
        return written

    def _open_folder(self) -> None:
        folder = Path(self.out_dir.text())
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
