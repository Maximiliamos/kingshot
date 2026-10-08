param(
    [string]$PythonExe = "",
    [string]$Serial = "127.0.0.1:58526",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not $Output) {
    $Output = Join-Path $Root "debug\google-services.png"
}

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

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
$env:PYTHONIOENCODING = "utf-8"
& $PythonExe .\warbot_cli.py google-services-smoke `
    --backend wsa --serial $Serial --output $Output
$code = [int]$LASTEXITCODE
if ($code -ne 0) {
    Write-Host "GOOGLE SERVICES HOST GATE FAIL (exit=$code)"
    Write-Host "Evidence: $([IO.Path]::ChangeExtension($Output, '.json'))"
    exit $code
}

Write-Host "GOOGLE SERVICES HOST GATE PASS"
Write-Host "Evidence: $([IO.Path]::ChangeExtension($Output, '.json'))"
exit 0
