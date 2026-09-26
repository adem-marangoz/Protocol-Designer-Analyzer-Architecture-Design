; Inno Setup script for Protocol Designer & Analyzer (design document Section 26).
;
; Produces installer\Output\ProtocolDesigner-Setup-<version>.exe with the wizard:
;   Welcome -> License Agreement (must accept) -> Install location -> Components
;   -> Additional tasks -> Ready -> Installing -> Finish (launch now)
; and registers the program under Settings > Apps > Installed apps.
;
; Build (from the repository root, after PyInstaller):
;   iscc /DAppVersion=1.0.0 installer\setup.iss
; Requires Inno Setup 6.3 or newer.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName      "Protocol Designer & Analyzer"
#define AppPublisher "Protocol Designer Project"
#define AppURL       "https://github.com/adem-marangoz/Protocol-Designer-Analyzer-Architecture-Design"
#define AppExeName   "ProtocolDesigner.exe"
#define CliExeName   "pdcli.exe"

[Setup]
; AppId must stay the same forever: it links upgrades and uninstall to this program.
AppId={{8F3C2A10-5B7E-4D2A-9C61-2E4F0A7B1D35}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppContact={#AppURL}/issues
AppComments=Protocol Definition, Analysis & Test Platform
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\ProtocolDesigner
DefaultGroupName={#AppName}
AllowNoIcons=yes
; License Agreement page: "Next" stays disabled until "I accept the agreement" is chosen.
LicenseFile=..\src\protocol_designer\resources\EULA.txt
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile=wizard_images\wizard.bmp,wizard_images\wizard@2x.bmp
WizardSmallImageFile=wizard_images\wizard_small.bmp,wizard_images\wizard_small@2x.bmp
OutputDir=Output
OutputBaseFilename=ProtocolDesigner-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Install for all users (admin) by default; the user may choose "only for me".
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
; Close a running copy during upgrade / uninstall.
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes
DisableProgramGroupPage=auto
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full";    Description: "Full installation"
Name: "compact"; Description: "Compact installation"
Name: "custom";  Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core";     Description: "Core application (required)"; Types: full compact custom; Flags: fixed
Name: "examples"; Description: "Example protocols (RS485, CAN, bootloader)"; Types: full
Name: "docs";     Description: "User guide and protocol format reference"; Types: full

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "fileassoc";   Description: "&Associate .pdproj files with {#AppName}"; GroupDescription: "File associations:"

[Files]
Source: "..\dist\ProtocolDesigner\*"; DestDir: "{app}"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\src\protocol_designer\resources\EULA.txt"; DestDir: "{app}"; DestName: "LICENSE.txt"; Components: core; Flags: ignoreversion
Source: "..\src\protocol_designer\resources\THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Components: core; Flags: ignoreversion
Source: "..\protocols\*.json"; DestDir: "{app}\protocols"; Components: examples; Flags: ignoreversion
Source: "..\docs\*.md"; DestDir: "{app}\docs"; Components: docs; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}\docs"; Components: docs; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";                 Filename: "{app}\{#AppExeName}"; Comment: "Design, analyse and test communication protocols"
Name: "{group}\User Guide";                 Filename: "{app}\docs\USER_GUIDE.md"; Components: docs
Name: "{group}\License";                    Filename: "{app}\LICENSE.txt"
Name: "{group}\Uninstall {#AppName}";       Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";           Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; .pdproj file association (Section 26.4)
Root: HKA; Subkey: "Software\Classes\.pdproj"; ValueType: string; ValueName: ""; ValueData: "ProtocolDesigner.Project"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.pdproj\OpenWithProgids"; ValueType: string; ValueName: "ProtocolDesigner.Project"; ValueData: ""; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ProtocolDesigner.Project"; ValueType: string; ValueName: ""; ValueData: "Protocol Designer Project"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ProtocolDesigner.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ProtocolDesigner.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Tasks: fileassoc
; "App Paths" lets Win+R start the program and the CLI by name
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#AppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#CliExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#CliExeName}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only files the program itself may have created inside its own folder.
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
{ Section 26.6: uninstall keeps user data (protocols, test results, logs,
  settings) unless the user explicitly asks to delete it. Silent uninstalls
  (used for upgrades and by IT deployment) never delete user data. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Docs, Roaming, Local: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if UninstallSilent then
      Exit;
    Docs := ExpandConstant('{userdocs}\ProtocolDesigner');
    Roaming := ExpandConstant('{userappdata}\ProtocolDesigner');
    Local := ExpandConstant('{localappdata}\ProtocolDesigner');
    if DirExists(Docs) or DirExists(Roaming) or DirExists(Local) then
    begin
      if MsgBox('Also delete your projects and settings?' + #13#10#13#10 +
                'This removes your protocol files, test reports, logs and settings:' + #13#10 +
                Docs + #13#10 + Roaming + #13#10 + Local + #13#10#13#10 +
                'Choose "No" to keep them (recommended if you plan to reinstall).',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(Docs, True, True, True);
        DelTree(Roaming, True, True, True);
        DelTree(Local, True, True, True);
      end;
    end;
  end;
end;
