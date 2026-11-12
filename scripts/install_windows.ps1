# Installs a Lekhika shortcut into the current user's Start Menu (and
# optionally the Desktop). No admin rights needed.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -Desktop
#   powershell ... -Uninstall
#
# The shortcut runs the checkout's .venv interpreter with `-m lekhika`
# (pythonw.exe, so no console window appears), falling back to a `lekhika`
# command already on PATH.

param(
    [switch]$Desktop,
    [switch]$Uninstall
)

$repo = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $repo "packaging\lekhika.ico"

function Find-Launcher {
    $pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
    if (Test-Path $pythonw) {
        return @{ Target = $pythonw; Arguments = "-m lekhika" }
    }
    $console = Get-Command lekhika -ErrorAction SilentlyContinue
    if ($console) {
        return @{ Target = $console.Source; Arguments = "" }
    }
    Write-Error ("No Lekhika launcher found. Install it first:`n" +
        "  py -3 -m venv `"$repo\.venv`"`n" +
        "  `"$repo\.venv\Scripts\pip`" install -e `"$repo`"")
    exit 1
}

function New-LekhikaShortcut([string]$path, $launcher) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $launcher.Target
    $shortcut.Arguments = $launcher.Arguments
    $shortcut.WorkingDirectory = $repo
    $shortcut.IconLocation = $icon
    $shortcut.Description = "Plan and write long-form documents"
    $shortcut.Save()
    Write-Host "installed $path"
}

$programs = [Environment]::GetFolderPath("Programs")  # Start Menu
$shortcuts = @(
    (Join-Path $programs "Lekhika.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Lekhika.lnk")
)
if (-not $Desktop) { $shortcuts = @($shortcuts[0]) }

if ($Uninstall) {
    $found = $false
    foreach ($path in @(
        (Join-Path $programs "Lekhika.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Lekhika.lnk")
    )) {
        if (Test-Path $path) {
            Remove-Item $path
            Write-Host "removed $path"
            $found = $true
        }
    }
    if (-not $found) { Write-Host "nothing to remove" }
    exit 0
}

if (-not (Test-Path $icon)) {
    Write-Warning "packaging\lekhika.ico missing - shortcut will use a generic icon"
}
$launcher = Find-Launcher
foreach ($path in $shortcuts) { New-LekhikaShortcut $path $launcher }
