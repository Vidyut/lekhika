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
# PySide6 is trimmed to modules Lekhika uses (Core/Gui/Widgets/Multimedia/TextToSpeech)
# to reduce bundle size and cross-platform plugin conflicts; smoke-test exports/images/TTS.
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
  --collect-submodules PySide6.QtCore `
  --collect-submodules PySide6.QtGui `
  --collect-submodules PySide6.QtWidgets `
  --collect-submodules PySide6.QtMultimedia `
  --collect-submodules PySide6.QtTextToSpeech `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.QtWebEngineQuick `
  --exclude-module PySide6.QtQuick `
  --exclude-module PySide6.QtQml `
  --exclude-module PySide6.QtQmlModels `
  --exclude-module PySide6.QtQmlMeta `
  --exclude-module PySide6.Qt3DCore `
  --exclude-module PySide6.Qt3DRender `
  --exclude-module PySide6.Qt3DInput `
  --exclude-module PySide6.Qt3DExtras `
  --exclude-module PySide6.Qt3DAnimation `
  --exclude-module PySide6.Qt3DLogic `
  --exclude-module PySide6.QtCharts `
  --exclude-module PySide6.QtDataVisualization `
  --exclude-module PySide6.QtGraphs `
  --exclude-module PySide6.QtPdf `
  --exclude-module PySide6.QtPdfWidgets `
  --exclude-module PySide6.QtBluetooth `
  --exclude-module PySide6.QtNfc `
  --exclude-module PySide6.QtPositioning `
  --exclude-module PySide6.QtLocation `
  --exclude-module PySide6.QtSensors `
  --exclude-module PySide6.QtSerialPort `
  --exclude-module PySide6.QtSerialBus `
  --exclude-module PySide6.QtRemoteObjects `
  --exclude-module PySide6.QtNetworkAuth `
  --exclude-module PySide6.QtDesigner `
  --exclude-module PySide6.QtHelp `
  --exclude-module PySide6.QtSql `
  --exclude-module PySide6.QtTest `
  --exclude-module PySide6.QtUiTools `
  --exclude-module PySide6.QtScxml `
  --exclude-module PySide6.QtStateMachine `
  --exclude-module PySide6.QtSpatialAudio `
  --collect-all docx `
  --collect-all reportlab `
  _lekhika_entry.py

Write-Host ""
Write-Host "Build complete."
Write-Host "Run: dist\Lekhika\Lekhika.exe"
Write-Host "To share, zip the entire dist\Lekhika folder."
