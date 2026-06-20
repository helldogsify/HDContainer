# Build HDContainer.exe and the installer (HDContainer-Setup.exe).
# Requires: Python + PyInstaller, and Inno Setup 6 (ISCC.exe) for the installer.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Building HDContainer.exe with PyInstaller..."
python -m PyInstaller --onefile --noconsole --name HDContainer --icon HDContainer.ico --clean -y window_container.py

$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
  Write-Host "==> Building installer with Inno Setup..."
  & $iscc "installer.iss"
  Write-Host "==> Done. See dist\HDContainer-Setup.exe and dist\HDContainer.exe"
} else {
  Write-Host "Inno Setup (ISCC.exe) not found - built portable exe only (dist\HDContainer.exe)."
  Write-Host "Install Inno Setup 6 to build the installer: https://jrsoftware.org/isdl.php"
}
