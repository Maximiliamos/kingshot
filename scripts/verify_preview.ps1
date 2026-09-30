param(
    [double]$PreviewSeconds = 10,
    [switch]$AllowFallback,
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
    throw "TUGARIN BOTS Python venv not found. Run verify_release.ps1 first."
}

$env:PYTHONIOENCODING = "utf-8"
Write-Host "=== TUGARIN BOTS PREVIEW ACCEPTANCE ==="
Write-Host "Python: $PythonExe"
Write-Host "Duration: $PreviewSeconds sec"
Write-Host "Expected transport: $(if ($AllowFallback) { 'H.264 or fallback' } else { 'H.264 required' })"

$args = @(
    ".\warbot_cli.py", "preview-smoke",
    "--backend", "wsa",
    "--serial", "127.0.0.1:58526",
    "--preview-seconds", ([string]$PreviewSeconds)
)
if (-not $AllowFallback) { $args += "--require-h264" }

& $PythonExe @args
$code = [int]$LASTEXITCODE
if ($code -ne 0) {
    Write-Host ""
    Write-Host "PREVIEW HOST GATE FAIL"
    Write-Host ""
    Write-Host "Collecting H.264 encoder/decoder diagnostics..."
    $probeArgs = @(
        ".\warbot_cli.py", "preview-probe",
        "--backend", "wsa",
        "--serial", "127.0.0.1:58526"
    )
    & $PythonExe @probeArgs
    $probeCode = [int]$LASTEXITCODE
    Write-Host ""
    Write-Host "Preview probe exit: $probeCode"
    Write-Host "Diagnostic JSON: C:\warbot_git\debug\preview-h264-probe.json"
    exit $code
}

Write-Host ""
Write-Host "PREVIEW HOST GATE PASS"
exit 0
