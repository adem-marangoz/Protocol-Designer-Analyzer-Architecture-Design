<#
.SYNOPSIS
  Builds ProtocolDesigner-Setup-<version>.exe on a Windows machine.

.DESCRIPTION
  1. creates a virtual environment and installs the dependencies
  2. runs the test suite (skip with -SkipTests)
  3. builds the executables with PyInstaller (dist\ProtocolDesigner)
  4. compiles the Inno Setup installer (installer\Output)

  Requirements: Python 3.10+ (64-bit) and Inno Setup 6.3+
  (winget install JRSoftware.InnoSetup  or  choco install innosetup).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#>
param(
    [switch]$SkipTests,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

Step "Python environment"
if (-not (Test-Path ".venv")) { & $Python -m venv .venv }
$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$Version = (& $Py -c "import sys; sys.path.insert(0, 'src'); import protocol_designer as p; print(p.__version__)").Trim()
Write-Host "Version: $Version"

if (-not $SkipTests) {
    Step "Tests"
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    Remove-Item Env:QT_QPA_PLATFORM
}

Step "PyInstaller"
& $Py packaging\make_version_info.py
& $Py -m PyInstaller --noconfirm --clean packaging\ProtocolDesigner.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
& "dist\ProtocolDesigner\pdcli.exe" validate protocols\tpms_rs485.json
if ($LASTEXITCODE -ne 0) { throw "the built pdcli.exe does not work" }

Step "Inno Setup"
$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
    foreach ($candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                             "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $candidate) { $Iscc = $candidate; break }
    }
}
if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) not found. Install it with: winget install JRSoftware.InnoSetup" }
& $Iscc "/DAppVersion=$Version" installer\setup.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$Setup = Get-Item "installer\Output\ProtocolDesigner-Setup-$Version.exe"
Write-Host "`nInstaller ready: $($Setup.FullName) ($([math]::Round($Setup.Length / 1MB, 1)) MB)" -ForegroundColor Green
