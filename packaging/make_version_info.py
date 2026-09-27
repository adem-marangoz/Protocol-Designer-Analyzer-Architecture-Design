"""Write build/version_info.txt (Windows file properties) from the package version.

Explorer shows these values under Properties > Details of the .exe files.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from protocol_designer import APP_NAME, APP_PUBLISHER, __version__  # noqa: E402

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v}),
    prodvers=({v}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{publisher}'),
        StringStruct('FileDescription', '{description}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{internal}'),
        StringStruct('LegalCopyright', 'Copyright (C) {publisher}'),
        StringStruct('OriginalFilename', '{internal}.exe'),
        StringStruct('ProductName', '{product}'),
        StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def version_tuple(version: str) -> str:
    parts = [int(p) if p.isdigit() else 0 for p in version.split(".")[:4]]
    parts += [0] * (4 - len(parts))
    return ", ".join(str(p) for p in parts)


def write(internal: str, description: str) -> Path:
    out = ROOT / "build" / f"version_info_{internal}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        TEMPLATE.format(
            v=version_tuple(__version__),
            version=__version__,
            publisher=APP_PUBLISHER,
            product=APP_NAME,
            description=description,
            internal=internal,
        ),
        encoding="utf-8",
    )
    return out


if __name__ == "__main__":
    print(write("ProtocolDesigner", APP_NAME))
    print(write("pdcli", f"{APP_NAME} command line"))
