; HDContainer — Inno Setup script
#define MyAppName "HDContainer"
#define MyAppVersion "1.0.5"
#define MyAppExe "HDContainer.exe"
#define MyAppUrl "https://github.com/helldogsify/HDContainer"

[Setup]
AppId={{B7E6B4C2-1A3D-4F58-9E2C-7A1D9F3C2E55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=hdk
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; спрашиваем у пользователя, куда ставить:
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
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

[CustomMessages]
en.AskRemoveData=Also delete your containers and settings?%n%nYes — remove everything. No — keep them for next time.
ru.AskRemoveData=Удалить также ваши контейнеры и настройки?%n%nДа — удалить всё. Нет — сохранить для следующего раза.

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

[UninstallRun]
; корректно закрыть запущенный экземпляр перед удалением файлов
Filename: "{app}\{#MyAppExe}"; Parameters: "--quit"; Flags: waituntilterminated runhidden; RunOnceId: "QuitApp"

[Code]
procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\HDContainer');
    if DirExists(DataDir) then
    begin
      if MsgBox(ExpandConstant('{cm:AskRemoveData}'), mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
