# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds dist/ProtocolDesigner/ with
#   ProtocolDesigner.exe  - the desktop application (no console window)
#   pdcli.exe             - the command line interface
# Both share one set of libraries. Run from the repository root:
#   python packaging/make_version_info.py
#   pyinstaller --noconfirm packaging/ProtocolDesigner.spec

import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(ROOT, "installer", "app.ico")

hidden = (
    collect_submodules("can.interfaces")  # python-can loads interfaces by name at runtime
    + collect_submodules("serial.urlhandler")  # serial_for_url("loop://", "socket://", ...)
    + collect_submodules("serial.tools")
    + collect_submodules("protocol_designer")
)
datas = [
    (os.path.join(SRC, "protocol_designer", "resources"), os.path.join("protocol_designer", "resources")),
]
excludes = ["tkinter", "unittest", "pydoc_data", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick",
            "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts"]


def analysis(script):
    return Analysis(
        [os.path.join(ROOT, "packaging", script)],
        pathex=[SRC],
        binaries=[],
        datas=datas,
        hiddenimports=hidden,
        hookspath=[],
        runtime_hooks=[],
        excludes=excludes,
        noarchive=False,
    )


gui_a = analysis("launch_gui.py")
cli_a = analysis("launch_cli.py")

gui_pyz = PYZ(gui_a.pure)
cli_pyz = PYZ(cli_a.pure)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="ProtocolDesigner",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
    version=os.path.join(ROOT, "build", "version_info_ProtocolDesigner.txt"),
)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="pdcli",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=ICON,
    version=os.path.join(ROOT, "build", "version_info_pdcli.txt"),
)

coll = COLLECT(
    gui_exe,
    gui_a.binaries,
    gui_a.datas,
    cli_exe,
    cli_a.binaries,
    cli_a.datas,
    strip=False,
    upx=False,
    name="ProtocolDesigner",
)
