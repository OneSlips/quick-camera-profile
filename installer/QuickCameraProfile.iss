; Inno Setup script for Quick Camera Profile
; Build with: iscc installer\\QuickCameraProfile.iss

#define MyAppName "Quick Camera Profile"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Roman Alurkoff"
#define MyAppExeName "QuickProfile.exe"
#define MyAppURL "https://github.com/<your-account>/quick-camera-profile"
#define MyAppSourceURL "https://github.com/<your-account>/quick-camera-profile/archive/refs/tags/v1.0.0.zip"

[Setup]
AppId={{9AA42A42-27C7-47EA-B708-80A5E9DA7A22}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\\{#MyAppName}
DefaultGroupName={#MyAppName}
LicenseFile=LICENSE
OutputDir=release
OutputBaseFilename=QuickCameraProfile-Setup-v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "dist\\QuickProfile\\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "SOURCE_CODE_OFFER.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"
Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    MsgBox(
      'This software includes GPL-3.0 licensed components.' + #13#10 +
      'Source code for this release: {#MyAppSourceURL}' + #13#10 +
      'Project repository: {#MyAppURL}',
      mbInformation, MB_OK);
  end;
end;
