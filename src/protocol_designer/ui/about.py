"""Help → About, with the License and third-party licenses (Section 26.5)."""

from __future__ import annotations

import platform

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from .. import APP_NAME, APP_PUBLISHER, __version__, paths


def read_resource(name: str) -> str:
    try:
        return paths.resource(name).read_text(encoding="utf-8")
    except OSError:
        return f"({name} not found)"


class AboutDialog(QDialog):
    def __init__(self, parent=None, tab: str = "about"):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.resize(700, 520)
        icon = paths.resource("app.png")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        about = QWidget()
        av = QVBoxLayout(about)
        text = QLabel(
            f"<h2>{APP_NAME}</h2>"
            f"<p>Version {__version__}<br>{APP_PUBLISHER}</p>"
            "<p>Protocol Definition, Analysis &amp; Test Platform: declarative protocol definitions, "
            "packet builder, analyzer, live monitor, automated tests and code generation for RS485, UART, "
            "TCP/UDP, CAN and CAN-FD.</p>"
            f"<p>Python {platform.python_version()} · Qt {qVersion()} · PySide6 {pyside_version}</p>"
            f"<p>User data: {paths.documents_dir()}<br>Settings: {paths.settings_dir()}<br>"
            f"Logs: {paths.logs_dir()}</p>"
        )
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setWordWrap(True)
        av.addWidget(text)
        av.addStretch(1)
        self.tabs.addTab(about, "About")

        for title, name in (("License", "EULA.txt"), ("Third-party licenses", "THIRD_PARTY_LICENSES.txt")):
            view = QPlainTextEdit(read_resource(name))
            view.setReadOnly(True)
            self.tabs.addTab(view, title)
        self.tabs.setCurrentIndex({"about": 0, "license": 1, "third_party": 2}.get(tab, 0))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
