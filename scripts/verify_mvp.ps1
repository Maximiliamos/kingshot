param(
    [switch]$WipeRuntime,
    [switch]$CleanGame
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

New-Item -ItemType Directory -Force -Path (Join-Path $Root "debug") | Out-Null
$frame = Join-Path $Root "debug\bootstrap-frame.png"

$args = @(".\warbot_cli.py", "bootstrap", "--output", $frame)
if ($WipeRuntime) { $args += "--wipe" }
if ($CleanGame) { $args += "--clean-game" }

Write-Host "=== WAR BOT MVP VERIFY ==="
Write-Host "1/3 Native ARM64 Android + game bootstrap"

& python @args
$bootstrapExit = $LASTEXITCODE
if ($bootstrapExit -ne 0) {
    Write-Host ""
    Write-Host "Bootstrap failed. Collecting deterministic boot diagnostics..."
    & python .\native_arm64_poc.py boot-report
    Write-Host ""
    Write-Host "Diagnostics:"
    Write-Host "  C:\warbot_arm64_runtime\zygote-crash.txt"
    Write-Host "  C:\warbot_arm64_runtime\boot-diagnostic.json"
    Write-Host "  C:\warbot_arm64_runtime\qemu-arm64.log"
    exit $bootstrapExit
}

Write-Host ""
Write-Host "2/3 Strict Android/native gate"
& python .\native_arm64_poc.py verify-native
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "3/3 Device/game status"
& python .\warbot_cli.py status
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path $frame)) {
    Write-Error "Bootstrap reported success but screenshot is missing: $frame"
    exit 2
}

Write-Host ""
Write-Host "MVP HOST GATE PASS"
Write-Host "Screenshot: $frame"
Write-Host "Now launch the GUI with: .\run_gui.bat"
