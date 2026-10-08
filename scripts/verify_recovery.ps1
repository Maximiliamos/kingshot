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
    throw "TUGARIN BOTS Python venv not found."
}

$env:PYTHONIOENCODING = "utf-8"
$evidence = Join-Path $Root "debug\recovery-smoke.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $evidence) | Out-Null
Remove-Item -LiteralPath $evidence -Force -ErrorAction SilentlyContinue

Write-Host "=== TUGARIN BOTS RECOVERY ACCEPTANCE ==="
Write-Host "This test stops only Kingshot, then reconnects ADB and verifies recovery."
Write-Host ""

$recoveryText = (& $PythonExe .\warbot_cli.py recovery-smoke --backend wsa --serial 127.0.0.1:58526 --with-adb-reconnect | Out-String)
$code = [int]$LASTEXITCODE
$recoveryText | Set-Content -LiteralPath $evidence -Encoding UTF8
$recoveryText | Write-Host
if ($code -ne 0) {
    Write-Host ""
    Write-Host "RECOVERY HOST GATE FAIL"
    Write-Host "Evidence: $evidence"
    exit $code
}

Write-Host ""
Write-Host "RECOVERY HOST GATE PASS"
Write-Host "Evidence: $evidence"
exit 0
