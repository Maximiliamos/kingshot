param(
    # Compatibility switch for the legacy native-QEMU report workflow.
    # WSA userdata is never wiped implicitly.
    [switch]$WipeRuntime,
    [switch]$CleanGame,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $PythonExe) {
    $dedicatedPython = "C:\warbot_wsa\tugarin-venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $dedicatedPython -PathType Leaf) {
        $PythonExe = $dedicatedPython
    }
    else {
        $resolvedPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $resolvedPython) { $resolvedPython = Get-Command python -ErrorAction Stop }
        $PythonExe = [string]$resolvedPython.Source
    }
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python interpreter does not exist: $PythonExe"
}
Write-Host "MVP Python: $PythonExe"

New-Item -ItemType Directory -Force -Path (Join-Path $Root "debug") | Out-Null
$frame = Join-Path $Root "debug\bootstrap-frame.png"

$args = @(
    ".\warbot_cli.py", "bootstrap",
    "--backend", "wsa",
    "--serial", "127.0.0.1:58526",
    "--output", $frame,
    "--game-stability-seconds", "120"
)
if ($CleanGame) { $args += "--clean-game" }

Write-Host "=== TUGARIN BOTS MVP VERIFY ==="
Write-Host "1/3 WSA Android + game bootstrap"

& $PythonExe @args
$bootstrapExit = $LASTEXITCODE
if ($bootstrapExit -ne 0) {
    Write-Host ""
    Write-Host "WSA bootstrap failed. Run the installer/verifier for a full report:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\install_wsa_poc.ps1"
    exit $bootstrapExit
}

Write-Host ""
Write-Host "2/3 WSA device status"
& $PythonExe .\warbot_cli.py status --backend wsa --serial 127.0.0.1:58526
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "3/3 Screenshot artifact"

if (-not (Test-Path $frame)) {
    Write-Error "Bootstrap reported success but screenshot is missing: $frame"
    exit 2
}

Write-Host ""
Write-Host "MVP HOST GATE PASS"
Write-Host "Screenshot: $frame"
Write-Host "Now launch the GUI with: wscript.exe //B .\run_gui.vbs"
