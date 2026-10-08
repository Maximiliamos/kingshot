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
    throw "TUGARIN BOTS Python venv not found for replay regression gate."
}

$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "debug") | Out-Null
$evidence = Join-Path $Root "debug\replay-regression.json"
Remove-Item -LiteralPath $evidence -Force -ErrorAction SilentlyContinue
$reportText = (& $PythonExe .\vision_replay.py --require-real-coverage | Out-String)
$replayCode = [int]$LASTEXITCODE
$reportText | Set-Content -LiteralPath $evidence -Encoding UTF8
$reportText | Write-Host
if ($replayCode -ne 0) {
    Write-Host "REPLAY REGRESSION BLOCKED: real labeled positive/negative frames are missing or failing."
    Write-Host "The synthetic unit tests are not a substitute for real Kingshot evidence."
    exit $replayCode
}
try {
    $report = $reportText | ConvertFrom-Json
    if (-not [bool]$report.pass -or -not [bool]$report.coverage_ready) {
        throw "Missing real frame coverage or regression failure"
    }
}
catch {
    Write-Host "REPLAY REGRESSION FAIL: invalid pass evidence."
    exit 53
}
Write-Host "REPLAY REGRESSION PASS: labeled real screenshots cover positive and negative UI."
exit 0
