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
python -c "import window_container as w; assert w.torrents is not None, w._TORRENTS_IMPORT_ERR; print('torrents import ok')"
if ($LASTEXITCODE -ne 0) { throw "torrents module failed to import" }

Write-Host "==> Building HDContainer.exe $ver with PyInstaller..."
# pillow-heif: HEIC/HEIF в конвертере — библиотека с нативными DLL, собираем целиком
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
