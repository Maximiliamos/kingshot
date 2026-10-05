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
$evidence = Join-Path $Root "debug\operator-io-acceptance.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $evidence) | Out-Null
Remove-Item -LiteralPath $evidence -Force -ErrorAction SilentlyContinue

Write-Host "=== TUGARIN BOTS OPERATOR I/O ACCEPTANCE ==="
Write-Host "Read-only/non-destructive: Unicode clipboard, UI hierarchy, audio health."
Write-Host ""

$ioText = (& $PythonExe .\warbot_cli.py operator-io-smoke --backend wsa --serial 127.0.0.1:58526 | Out-String)
$code = [int]$LASTEXITCODE
$ioText | Set-Content -LiteralPath $evidence -Encoding UTF8
$ioText | Write-Host
if ($code -ne 0) {
    Write-Host ""
    Write-Host "OPERATOR I/O HOST GATE FAIL"
    Write-Host "Evidence: $evidence"
    exit $code
}

Write-Host ""
Write-Host "OPERATOR I/O HOST GATE PASS"
Write-Host "Evidence: $evidence"
exit 0
