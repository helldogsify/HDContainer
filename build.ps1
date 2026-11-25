# Build HDContainer.exe and the installer (HDContainer-Setup.exe).
# Requires: Python + PyInstaller, and Inno Setup 6 (ISCC.exe) for the installer.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# version from installer.iss -> VERSIONINFO resource of the exe (Properties ->
# Details in Explorer; AV/SmartScreen treat an exe without it as more suspicious)
$ver = (Select-String -Path installer.iss -Pattern '#define MyAppVersion "([^"]+)"').Matches[0].Groups[1].Value
$v4 = (($ver -split '\.') + @('0','0','0'))[0..3] -join ', '
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v4), prodvers=($v4), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'hdk'),
    StringStruct('FileDescription', 'HDContainer'),
    StringStruct('FileVersion', '$ver'),
    StringStruct('InternalName', 'HDContainer'),
    StringStruct('LegalCopyright', 'MIT License'),
    StringStruct('OriginalFilename', 'HDContainer.exe'),
    StringStruct('ProductName', 'HDContainer'),
    StringStruct('ProductVersion', '$ver')])]),
  VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | ForEach-Object { if (-not (Test-Path build)) { New-Item -ItemType Directory build | Out-Null }; Set-Content -Path build\version_info.txt -Value $_ -Encoding UTF8 }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }   # never package a stale exe

# torrents: libtorrent (pinned) must be importable, otherwise the torrent module would
# be silently left out of the exe (it is imported in try/except like voice input)
python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('libtorrent') else 1)"
if ($LASTEXITCODE -ne 0) {
  python -m pip install "libtorrent==2.1.1"
  if ($LASTEXITCODE -ne 0) { throw "pip install libtorrent failed" }
}
python -c "import libtorrent; import window_container as w; assert w.torrents is not None, w._TORRENTS_IMPORT_ERR; assert w.boxes is not None, w._BOXES_IMPORT_ERR; assert w.screenshot is not None, w._SCREENSHOT_IMPORT_ERR; assert w.recorder is not None, w._RECORDER_IMPORT_ERR; assert w.screenrec is not None, w._SCREENREC_IMPORT_ERR; assert w.macros is not None, w._MACROS_IMPORT_ERR; assert w.lan_ui is not None, w._LAN_IMPORT_ERR; import rec_audio; assert rec_audio.audioop is not None, 'audioop missing'; print('torrents, boxes, screenshot, recorder, screenrec, macros and lan import ok')"
if ($LASTEXITCODE -ne 0) { throw "torrents, boxes, screenshot, recorder, screenrec, macros or lan module failed to import" }

Write-Host "==> Building HDContainer.exe $ver with PyInstaller..."
# pillow-heif: HEIC/HEIF в конвертере — библиотека с нативными DLL, собираем целиком
# Two builds of the same code:
#  - onedir (build\onedir\HDContainer) goes into the installer: it starts ~3x faster and
#    runs as one process, while a onefile exe unpacks ~80 MB into %TEMP% at every start of
#    every process (the app, its watchdog, each Explorer "Convert to") and keeps a
#    bootloader parent process beside each of them;
#  - onefile (dist\HDContainer.exe) stays the portable download of the release.
if (Test-Path build\onedir) { Remove-Item -Recurse -Force build\onedir }
python -m PyInstaller --onedir --noconsole --name HDContainer --icon HDContainer.ico `
  --version-file build\version_info.txt --collect-all pillow_heif --clean -y `
  --distpath build\onedir --workpath build\work_onedir window_container.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path build\onedir\HDContainer\HDContainer.exe)) { throw "PyInstaller (onedir) failed" }
python -m PyInstaller --onefile --noconsole --name HDContainer --icon HDContainer.ico `
  --version-file build\version_info.txt --collect-all pillow_heif --clean -y window_container.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path dist\HDContainer.exe)) { throw "PyInstaller failed" }

$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
  Write-Host "==> Building installer with Inno Setup..."
  & $iscc "installer.iss"
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
  Write-Host "==> Done. See dist\HDContainer-Setup.exe and dist\HDContainer.exe"
} else {
  Write-Host "Inno Setup (ISCC.exe) not found - built portable exe only (dist\HDContainer.exe)."
  Write-Host "Install Inno Setup 6 to build the installer: https://jrsoftware.org/isdl.php"
}
