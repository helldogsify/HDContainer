; HDContainer — Inno Setup script (per-user install, no admin required)
#define MyAppName "HDContainer"
#define MyAppVersion "1.0.1"
#define MyAppExe "HDContainer.exe"
#define MyAppUrl "https://github.com/helldogsify/HDContainer"

[Setup]
AppId={{B7E6B4C2-1A3D-4F58-9E2C-7A1D9F3C2E55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=hdk
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=HDContainer-Setup
SetupIconFile=HDContainer.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Start HDContainer when Windows starts"; Flags: unchecked

[Files]
Source: "dist\HDContainer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "HDContainer.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\HDContainer.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\HDContainer.ico"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "HDContainer"; ValueData: """{app}\{#MyAppExe}"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall
