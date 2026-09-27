<#
.SYNOPSIS
  End-to-end installer test (design document Section 26.9):
  install -> verify "Installed apps" entry -> run -> upgrade -> uninstall.

.PARAMETER Setup
  Path of ProtocolDesigner-Setup-<version>.exe
.PARAMETER UpgradeSetup
  Optional newer installer used to test the upgrade path.
#>
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [string]$UpgradeSetup = ""
)
$ErrorActionPreference = "Stop"
$AppId = "{8F3C2A10-5B7E-4D2A-9C61-2E4F0A7B1D35}_is1"
$Key = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
$Dir = Join-Path $env:TEMP "PD-InstallTest"
$Docs = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "ProtocolDesigner"

function Check($condition, $message) {
    if (-not $condition) { throw "FAILED: $message" }
    Write-Host "  [ok] $message" -ForegroundColor Green
}

function Install($path, $label) {
    Write-Host "`n--- $label ($path)" -ForegroundColor Cyan
    $log = Join-Path $env:TEMP "pd-$label.log"
    $p = Start-Process -FilePath $path -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ALLUSERS", "/DIR=`"$Dir`"",
        "/COMPONENTS=core,examples,docs", "/TASKS=desktopicon,fileassoc", "/LOG=`"$log`"")
    if ($p.ExitCode -ne 0) { Get-Content $log -Tail 40; throw "installer exit code $($p.ExitCode)" }
}

function Entries() {
    @(Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" |
      Where-Object { $_.PSChildName -eq $AppId })
}

# ---------------------------------------------------------------- install
Install $Setup "install"
Check (Test-Path $Key) "registered under Settings > Apps > Installed apps"
$entry = Get-ItemProperty $Key
Check ($entry.DisplayName -eq "Protocol Designer & Analyzer") "DisplayName = $($entry.DisplayName)"
Check ($entry.DisplayVersion) "DisplayVersion = $($entry.DisplayVersion)"
Check ($entry.Publisher) "Publisher = $($entry.Publisher)"
Check ($entry.UninstallString -like "*unins000.exe*") "UninstallString = $($entry.UninstallString)"
Check ($entry.InstallLocation.TrimEnd('\') -eq $Dir.TrimEnd('\')) "InstallLocation = $($entry.InstallLocation)"
Check ($entry.DisplayIcon -like "*ProtocolDesigner.exe*") "DisplayIcon = $($entry.DisplayIcon)"
Check ($entry.EstimatedSize -gt 0) "EstimatedSize = $($entry.EstimatedSize) KB"
Check ($entry.URLInfoAbout) "URLInfoAbout = $($entry.URLInfoAbout)"
foreach ($f in @("ProtocolDesigner.exe", "pdcli.exe", "unins000.exe", "LICENSE.txt", "THIRD_PARTY_LICENSES.txt",
                 "protocols\tpms_rs485.json", "docs\USER_GUIDE.md")) {
    Check (Test-Path (Join-Path $Dir $f)) "installed $f"
}
$assoc = (Get-ItemProperty "HKLM:\SOFTWARE\Classes\.pdproj").'(default)'
Check ($assoc -eq "ProtocolDesigner.Project") ".pdproj file association"
$menu = Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "Protocol Designer & Analyzer\Protocol Designer & Analyzer.lnk"
Check (Test-Path $menu) "Start Menu shortcut"
$desktop = Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "Protocol Designer & Analyzer.lnk"
Check (Test-Path $desktop) "desktop shortcut"

# -------------------------------------------------------------------- run
Write-Host "`n--- run the installed program" -ForegroundColor Cyan
& (Join-Path $Dir "pdcli.exe") --version
& (Join-Path $Dir "pdcli.exe") test (Join-Path $Dir "protocols\tpms_rs485.json")
Check ($LASTEXITCODE -eq 0) "installed pdcli.exe runs the example tests"
$env:QT_QPA_PLATFORM = "offscreen"
$gui = Start-Process -FilePath (Join-Path $Dir "ProtocolDesigner.exe") -Wait -PassThru `
    -ArgumentList @("--self-test", "`"$(Join-Path $Dir 'protocols\tpms_rs485.json')`"")
Remove-Item Env:QT_QPA_PLATFORM
Check ($gui.ExitCode -eq 0) "installed ProtocolDesigner.exe starts, opens every page and passes the example tests"
Check (Test-Path (Join-Path $Docs "Protocols\tpms_rs485.json")) "first run copied the examples to Documents\ProtocolDesigner"
$marker = Join-Path $Docs "Protocols\my_protocol.json"
Copy-Item (Join-Path $Dir "protocols\tpms_rs485.json") $marker

# ---------------------------------------------------------------- upgrade
if ($UpgradeSetup) {
    Install $UpgradeSetup "upgrade"
    Check ((Entries).Count -eq 1) "upgrade replaced the existing entry (still one Installed apps entry)"
    $after = Get-ItemProperty $Key
    Check ($after.DisplayVersion -ne $entry.DisplayVersion) "DisplayVersion upgraded to $($after.DisplayVersion)"
    Check ($after.InstallLocation.TrimEnd('\') -eq $Dir.TrimEnd('\')) "installed over the old version in the same folder"
    Check (Test-Path $marker) "user data preserved during upgrade"
}

# -------------------------------------------------------------- uninstall
Write-Host "`n--- uninstall" -ForegroundColor Cyan
$uninstaller = Join-Path $Dir "unins000.exe"
$p = Start-Process -FilePath $uninstaller -Wait -PassThru -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
Check ($p.ExitCode -eq 0) "uninstaller exit code 0"
for ($i = 0; $i -lt 60 -and ((Test-Path $Key) -or (Test-Path (Join-Path $Dir "ProtocolDesigner.exe"))); $i++) { Start-Sleep 1 }
Check (-not (Test-Path $Key)) "removed from Installed apps"
Check (-not (Test-Path (Join-Path $Dir "ProtocolDesigner.exe"))) "program files removed"
Check (-not (Test-Path $menu)) "Start Menu shortcut removed"
Check (-not (Test-Path $desktop)) "desktop shortcut removed"
Check (-not (Test-Path "HKLM:\SOFTWARE\Classes\ProtocolDesigner.Project")) "file association removed"
Check (Test-Path $marker) "user projects kept after uninstall"
Write-Host "`nAll installer checks passed." -ForegroundColor Green
