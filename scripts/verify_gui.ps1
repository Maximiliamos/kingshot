param(
    [int]$SmokeSeconds = 12,
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

$PythonW = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
if (-not (Test-Path -LiteralPath $PythonW -PathType Leaf)) {
    throw "pythonw.exe not found next to production Python: $PythonW"
}

$provision = Join-Path $Root "scripts\provision_scrcpy_server.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $provision
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$report = Join-Path $Root "debug\gui-host-smoke.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $report) | Out-Null
Remove-Item -LiteralPath $report -Force -ErrorAction SilentlyContinue

$oldSeconds = $env:TUGARIN_GUI_HOST_SMOKE_SECONDS
$oldReport = $env:TUGARIN_GUI_HOST_SMOKE_REPORT
try {
    $env:TUGARIN_GUI_HOST_SMOKE_SECONDS = [string][Math]::Max(3, $SmokeSeconds)
    $env:TUGARIN_GUI_HOST_SMOKE_REPORT = $report

    Write-Host "=== TUGARIN BOTS GUI HOST ACCEPTANCE ==="
    Write-Host "Launcher: $PythonW"
    Write-Host "Smoke: $($env:TUGARIN_GUI_HOST_SMOKE_SECONDS) sec"

    $startArgs = @{
        FilePath = $PythonW
        ArgumentList = @((Join-Path $Root "gui.py"))
        WorkingDirectory = $Root
        PassThru = $true
    }
    $process = Start-Process @startArgs

    $deadline = (Get-Date).AddSeconds([Math]::Max(20, $SmokeSeconds + 15))
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
    }

    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Write-Host "GUI HOST GATE FAIL: GUI did not self-close."
        exit 51
    }

    if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
        Write-Host "GUI HOST GATE FAIL: report missing."
        exit 52
    }

    $json = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    Get-Content -LiteralPath $report
    if ($process.ExitCode -ne 0 -or -not [bool]$json.pass) {
        Write-Host ""
        Write-Host "GUI HOST GATE FAIL"
        exit 53
    }

    Write-Host ""
    Write-Host "GUI HOST GATE PASS"
    exit 0
}
finally {
    $env:TUGARIN_GUI_HOST_SMOKE_SECONDS = $oldSeconds
    $env:TUGARIN_GUI_HOST_SMOKE_REPORT = $oldReport
}
