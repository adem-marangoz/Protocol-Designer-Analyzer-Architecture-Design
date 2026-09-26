"""Consistency checks for the Windows packaging (installer, PyInstaller spec, CI).

The installer itself is compiled and exercised end to end on a Windows runner
(packaging/test_installer.ps1); these tests catch broken references early on
any platform.
"""

import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from protocol_designer import APP_NAME, APP_PUBLISHER, __version__

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "installer" / "setup.iss"


def iss_text():
    return ISS.read_text(encoding="utf-8")


def test_installer_version_matches_package():
    m = re.search(r'#define AppVersion "([^"]+)"', iss_text())
    assert m and m.group(1) == __version__


def test_installer_identity_matches_application():
    text = iss_text()
    assert f'#define AppName      "{APP_NAME}"' in text
    assert f'#define AppPublisher "{APP_PUBLISHER}"' in text
    # AppId links upgrades and uninstall to this program: it must never change
    assert "AppId={{8F3C2A10-5B7E-4D2A-9C61-2E4F0A7B1D35}" in text
    assert "8F3C2A10-5B7E-4D2A-9C61-2E4F0A7B1D35" in (ROOT / "packaging" / "test_installer.ps1").read_text(encoding="utf-8")


def test_installer_wizard_requirements():
    text = iss_text()
    assert re.search(r"^LicenseFile=", text, re.M), "license agreement page"
    assert "DefaultDirName={autopf}\\ProtocolDesigner" in text, "install location page"
    assert "UninstallDisplayIcon=" in text and "UninstallDisplayName=" in text, "Installed apps entry"
    assert 'Description: "Launch {#AppName} now"' in text, "finish page option"
    assert "[Components]" in text and "[Tasks]" in text
    assert "desktopicon" in text and "fileassoc" in text
    assert "Also delete your projects and settings?" in text


def test_installer_referenced_files_exist():
    text = iss_text()
    base = ISS.parent
    refs = []
    for key in ("LicenseFile", "SetupIconFile"):
        refs.append(re.search(rf"^{key}=(.+)$", text, re.M).group(1).strip())
    for key in ("WizardImageFile", "WizardSmallImageFile"):
        refs += re.search(rf"^{key}=(.+)$", text, re.M).group(1).strip().split(",")
    for src in re.findall(r'^Source: "([^"]+)"', text, re.M):
        if "dist\\" in src:
            continue  # produced by PyInstaller during the build
        refs.append(src)
    for ref in refs:
        path = (base / ref.replace("\\", "/"))
        if "*" in path.name:
            assert list(path.parent.glob(path.name)), f"no files match {ref}"
        else:
            assert path.exists(), f"missing {ref}"


def test_user_guide_is_installed_where_the_shortcut_points():
    text = iss_text()
    assert r'Filename: "{app}\docs\USER_GUIDE.md"' in text
    assert (ROOT / "docs" / "USER_GUIDE.md").exists()


def test_icon_is_a_valid_multi_size_ico():
    data = (ROOT / "installer" / "app.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        w, h, _, _, planes, bpp, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert offset + length <= len(data)
        sizes.append(w or 256)
    assert {16, 32, 48, 256} <= set(sizes)


@pytest.mark.parametrize("name", ["wizard.bmp", "wizard@2x.bmp", "wizard_small.bmp", "wizard_small@2x.bmp"])
def test_wizard_images_are_bitmaps(name):
    assert (ROOT / "installer" / "wizard_images" / name).read_bytes()[:2] == b"BM"


def test_eula_has_all_sections():
    text = (ROOT / "src" / "protocol_designer" / "resources" / "EULA.txt").read_text(encoding="utf-8")
    for heading in ["GRANT OF LICENSE", "RESTRICTIONS", "OWNERSHIP", "THIRD-PARTY", "DATA AND PRIVACY",
                    "DISCLAIMER OF WARRANTY", "LIMITATION OF LIABILITY", "TERMINATION", "GOVERNING LAW", "CONTACT"]:
        assert heading in text
    text.encode("ascii")  # Inno Setup shows .txt licenses with the ANSI code page


def test_pyinstaller_spec_references():
    spec = (ROOT / "packaging" / "ProtocolDesigner.spec").read_text(encoding="utf-8")
    for script in re.findall(r'analysis\("([^"]+)"\)', spec):
        assert (ROOT / "packaging" / script).exists()
    assert 'name="ProtocolDesigner"' in spec and "console=False" in spec
    assert 'name="pdcli"' in spec and "console=True" in spec
    assert (ROOT / "src" / "protocol_designer" / "resources" / "app.png").exists()


def test_version_info_generator(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "packaging" / "make_version_info.py")],
                         capture_output=True, text=True, check=True).stdout.split()
    assert len(out) == 2
    text = Path(out[0]).read_text(encoding="utf-8")
    parts = ", ".join((__version__.split(".") + ["0"] * 4)[:4])
    assert f"filevers=({parts})" in text
    assert f"StringStruct('ProductName', '{APP_NAME}')" in text


def test_workflow_references_existing_scripts():
    wf = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    for ref in ["packaging/make_version_info.py", "packaging/ProtocolDesigner.spec", "installer\\setup.iss",
                "packaging/test_installer.ps1", "requirements-dev.txt"]:
        assert ref in wf
        assert (ROOT / ref.replace("\\", "/")).exists()
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load(wf)["jobs"]
    assert set(jobs) == {"test-linux", "windows"}
    assert jobs["windows"]["runs-on"].startswith("windows")


def test_frozen_entry_points_import():
    for script in ("launch_gui.py", "launch_cli.py"):
        source = (ROOT / "packaging" / script).read_text(encoding="utf-8")
        compile(source, script, "exec")
