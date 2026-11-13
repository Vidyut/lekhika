# Build a Windows executable for Lekhika (simple volunteer build)
# Run from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows_exe.ps1
#
# Requirements: Python 3.9+ installed on Windows (with "Add python.exe to PATH").
# Output: dist\Lekhika\Lekhika.exe (portable folder)

$ErrorActionPreference = "Stop"

Write-Host "==> Creating virtual environment (.venv-build)"
if (!(Test-Path ".venv-build")) {
  py -3 -m venv .venv-build
}

$py = ".\.venv-build\Scripts\python.exe"
$pip = ".\.venv-build\Scripts\pip.exe"
$pyi = ".\.venv-build\Scripts\pyinstaller.exe"

Write-Host "==> Upgrading pip and installing build dependencies"
& $py -m pip install --upgrade pip
& $pip install -e .
& $pip install pyinstaller

Write-Host "==> Building Lekhika.exe with PyInstaller"
# --collect-all lekhika includes package data (guide/templates, etc.)
# Add --console instead of --windowed if you need debug output.
@"
from lekhika.app import main
main()
"@ | Set-Content -Encoding UTF8 _lekhika_entry.py

& $pyi `
  --noconfirm `
  --clean `
  --name Lekhika `
  --windowed `
  --icon packaging\lekhika.ico `
  --collect-all lekhika `
  --collect-all PySide6 `
  --collect-all docx `
  --collect-all reportlab `
  _lekhika_entry.py

Write-Host ""
Write-Host "Build complete."
Write-Host "Run: dist\Lekhika\Lekhika.exe"
Write-Host "To share, zip the entire dist\Lekhika folder."
