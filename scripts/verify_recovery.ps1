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

$provision = Join-Path $Root "scripts\provision_scrcpy_server.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $provision
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:PYTHONIOENCODING = "utf-8"
Write-Host "=== TUGARIN BOTS RECOVERY ACCEPTANCE ==="
Write-Host "This test stops only Kingshot, then reconnects ADB and verifies recovery."
Write-Host ""

& $PythonExe .\warbot_cli.py recovery-smoke --backend wsa --serial 127.0.0.1:58526 --with-adb-reconnect
$code = [int]$LASTEXITCODE
if ($code -ne 0) {
    Write-Host ""
    Write-Host "RECOVERY HOST GATE FAIL"
    exit $code
}

Write-Host ""
Write-Host "RECOVERY HOST GATE PASS"
exit 0
