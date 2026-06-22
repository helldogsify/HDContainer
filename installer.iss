; HDContainer — Inno Setup script
#define MyAppName "HDContainer"
#define MyAppVersion "1.2.1"
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
; экран приветствия показываем -> на нём прямо сообщаем, что это ОБНОВЛЕНИЕ
DisableWelcomePage=no
; на чистую установку спросим папку, при обновлении возьмём прежнюю (auto)
DisableDirPage=auto
DisableProgramGroupPage=yes
; ВСЕГДА ставим для текущего пользователя -> один и тот же scope, поэтому
; обновление гарантированно находит прежнюю установку. Раньше диалог
; per-user/per-machine позволял уехать в другой hive -> Setup не видел старую
; версию и ставил «как на чистый комп».
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=HDContainer-Setup
SetupIconFile=HDContainer.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; запущенный экземпляр закрываем сами в коде (тихо, без диалога «закройте прогу»)
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
en.AskRemoveData=Also delete your containers and settings?%n%nYes — remove everything. No — keep them for next time.
ru.AskRemoveData=Удалить также ваши контейнеры и настройки?%n%nДа — удалить всё. Нет — сохранить для следующего раза.
en.UpdateInfo=HDContainer %1 is already installed in %2. Setup will update it to version %3 in the same folder; the running copy will be closed automatically.
ru.UpdateInfo=HDContainer %1 уже установлен в папке %2. Установщик обновит его до версии %3 в той же папке; запущенная копия будет закрыта автоматически.
en.StartupTask=Start HDContainer when Windows starts
ru.StartupTask=Запускать HDContainer при старте Windows

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "{cm:StartupTask}"; Flags: unchecked

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
var
  PrevPath: String;
  PrevVer: String;
  IsUpgrade: Boolean;

// найти прежнюю установку по ключу деинсталляции Inno (AppId + _is1), HKCU/HKLM
function FindPrev(): Boolean;
var
  K: String;
begin
  K := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7E6B4C2-1A3D-4F58-9E2C-7A1D9F3C2E55}_is1';
  PrevPath := '';
  PrevVer := '';
  Result := RegQueryStringValue(HKCU, K, 'InstallLocation', PrevPath);
  if not Result then
    Result := RegQueryStringValue(HKLM, K, 'InstallLocation', PrevPath);
  if Result then
  begin
    if not RegQueryStringValue(HKCU, K, 'DisplayVersion', PrevVer) then
      RegQueryStringValue(HKLM, K, 'DisplayVersion', PrevVer);
    if PrevVer = '' then PrevVer := '?';
  end;
end;

function InitializeSetup(): Boolean;
begin
  IsUpgrade := FindPrev();
  Result := True;
end;

// на экране приветствия прямо пишем, что это обновление и в какой папке
procedure InitializeWizard();
begin
  if IsUpgrade and (WizardForm.WelcomeLabel2 <> nil) then
    WizardForm.WelcomeLabel2.Caption :=
      FmtMessage(CustomMessage('UpdateInfo'), [PrevVer, PrevPath, '{#MyAppVersion}']);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
begin
  // ТИХО закрыть запущенный экземпляр перед заменой файлов (без диалогов Inno):
  // мягко (--quit корректно отвяжет окна) в обеих возможных папках, затем добить.
  if (PrevPath <> '') and FileExists(AddBackslash(PrevPath) + '{#MyAppExe}') then
    Exec(AddBackslash(PrevPath) + '{#MyAppExe}', '--quit', '', SW_HIDE,
         ewWaitUntilTerminated, rc);
  if FileExists(ExpandConstant('{app}\{#MyAppExe}')) then
    Exec(ExpandConstant('{app}\{#MyAppExe}'), '--quit', '', SW_HIDE,
         ewWaitUntilTerminated, rc);
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM {#MyAppExe} /F', '', SW_HIDE,
       ewWaitUntilTerminated, rc);
  Sleep(500);
  Result := '';
end;

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
