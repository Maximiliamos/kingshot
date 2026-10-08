param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $PythonExe) {
    foreach ($candidate in @(
        "C:\warbot_wsa\tugarin-venv\Scripts\python.exe",
        "C:\warbot_wsa\release-venv\Scripts\python.exe"
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $PythonExe = $candidate
            break
        }
    }
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "TUGARIN BOTS Python venv not found for read-only readiness gate."
}

$env:PYTHONIOENCODING = "utf-8"
$evidence = Join-Path $Root "debug\resource-readiness.json"
Remove-Item -LiteralPath $evidence -Force -ErrorAction SilentlyContinue
Write-Host "=== NON-DESTRUCTIVE KINGSHOT RESOURCE READINESS ==="
Write-Host "No pm clear, clicks, game restart, DNS or account changes are allowed."
& $PythonExe .\warbot_cli.py resource-readiness --backend wsa --serial 127.0.0.1:58526
$probeCode = [int]$LASTEXITCODE
if ($probeCode -ne 0) {
    Write-Host "RESOURCE READINESS BLOCKED: game UI was not positively verified."
    Write-Host "Do not run destructive game-flow or soak while resources are unresolved."
    Write-Host "Evidence: $evidence"
    exit $probeCode
}
if (-not (Test-Path -LiteralPath $evidence -PathType Leaf)) {
    Write-Host "RESOURCE READINESS FAIL: evidence file missing."
    exit 52
}
Write-Host "RESOURCE READINESS PASS: two recognized Kingshot UI frames observed."
Write-Host "NOTE: subsequent pm clear may still trigger a new resource download."
exit 0
