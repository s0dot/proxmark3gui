<#
  Install-Shortcut.ps1 - creates a pinnable "Proxmark3 GUI" shortcut.

  Makes a real Windows .lnk (target = pythonw.exe, so NO console window) on your
  Desktop, with the P3 icon. You can then right-click it and "Pin to taskbar".
  Run it once:

      powershell -ExecutionPolicy Bypass -File "<path>\pm3gui\Install-Shortcut.ps1"

  (or right-click the file, then "Run with PowerShell")
#>

$ErrorActionPreference = "Stop"
$here   = $PSScriptRoot                       # ...\pm3gui
$repo   = Split-Path $here -Parent            # repo root
$server = Join-Path $here "server.py"
$icon   = Join-Path $here "pm3.ico"

Write-Host "Proxmark3 GUI - shortcut installer" -ForegroundColor Cyan

# --- find an interpreter (prefer pythonw.exe = no console window) ------------
function Find-Exe($name) { $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { $c.Source } }

$python = Find-Exe "python"; if (-not $python) { $python = Find-Exe "py" }
if (-not $python) {
    Write-Host "  [!] Python was not found on PATH. Install Python 3, then re-run this." -ForegroundColor Red
    exit 1
}

$pythonw = $null
$cand = Join-Path (Split-Path $python) "pythonw.exe"
if (Test-Path $cand) { $pythonw = $cand }
if (-not $pythonw) { $pythonw = Find-Exe "pythonw" }
if (-not $pythonw) { $pythonw = Find-Exe "pyw" }
if (-not $pythonw) {
    $pythonw = $python
    Write-Host "  [i] pythonw.exe not found; using python.exe (a console window will show)." -ForegroundColor Yellow
}

# --- ensure the icon exists --------------------------------------------------
if (-not (Test-Path $icon)) {
    try { & $python (Join-Path $here "make_icon.py") | Out-Null } catch {}
}

# --- create the shortcut -----------------------------------------------------
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Proxmark3 GUI.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath       = $pythonw
$lnk.Arguments        = '"' + $server + '"'
$lnk.WorkingDirectory = $repo
$lnk.Description       = "Proxmark3 GUI"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Save()

Write-Host ""
Write-Host "  [+] Created: $lnkPath" -ForegroundColor Green
Write-Host "      Target : $pythonw ""$server"""
Write-Host ""
Write-Host "  To pin it to the taskbar:" -ForegroundColor Cyan
Write-Host "    1. Right-click the new 'Proxmark3 GUI' icon on your Desktop"
Write-Host "    2. (Windows 11) click 'Show more options' if needed"
Write-Host "    3. choose 'Pin to taskbar'"
Write-Host ""
Write-Host "  Clicking it starts the GUI (no black window) and opens your browser."
Write-Host "  Clicking it again while it is running just re-opens the browser tab."
