param(
    [string]$ShortcutName = "ClipForge",
    [string]$IconPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvPythonW = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$PythonConsole = if (Test-Path $VenvPython) { $VenvPython } else { (Get-Command python).Source }
$LauncherExe = if (Test-Path $VenvPythonW) { $VenvPythonW } else { $PythonConsole }

$DefaultIco = Join-Path $ProjectRoot "assets\icons\clipforge.ico"
if (-not $IconPath -and -not (Test-Path $DefaultIco)) {
    & $PythonConsole -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from clipforge.desktop.icon import generate_ico; generate_ico(Path('assets/icons/clipforge.ico'))"
}

$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutFile = Join-Path $DesktopPath "$ShortcutName.lnk"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutFile)
$Shortcut.TargetPath = $LauncherExe
$Shortcut.Arguments = "clipforge.py app"
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "ClipForge Studio - Live Clipper, 9:16 Split-Cam y Subtítulos Bilingües EN/ES"

if ($IconPath -and (Test-Path $IconPath)) {
    $Shortcut.IconLocation = (Resolve-Path $IconPath).Path
} elseif (Test-Path $DefaultIco) {
    $Shortcut.IconLocation = $DefaultIco
}

$Shortcut.Save()
Write-Host "Acceso directo creado en: $ShortcutFile"
