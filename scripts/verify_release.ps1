param(
    [switch]$SkipUnitTests,
    [switch]$CleanGame,
    [string]$TargetUser = "TugarinBots"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== TUGARIN BOTS RELEASE ACCEPTANCE ==="

if (-not $SkipUnitTests) {
    Write-Host "1/3 Hosted-equivalent unit/integration suite"
    $env:QT_QPA_PLATFORM = "offscreen"
    & python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    Write-Host "1/3 Unit/integration suite skipped explicitly"
}

Write-Host ""
Write-Host "2/3 WSA + Kingshot real-host gate"
$verifyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "verify_mvp.ps1"))
if ($CleanGame) { $verifyArgs += "-CleanGame" }
& powershell.exe @verifyArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "3/3 Dedicated-user stale-process audit"
$auditArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "audit_runtime_processes.ps1"),
    "-TargetUser", $TargetUser
)
& powershell.exe @auditArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "RELEASE HOST GATE PASS"
exit 0
