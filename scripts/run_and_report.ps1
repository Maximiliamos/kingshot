param(
    [switch]$WipeRuntime,
    [switch]$CleanGame,
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $SkipPull) {
    $status = (& git status --porcelain)
    if ($status) {
        throw "Working tree is not clean. Refusing automatic pull. Commit/stash local changes first."
    }

    Write-Host "Updating feature/unified-android-backend..."
    & git pull --ff-only origin feature/unified-android-backend
    if ($LASTEXITCODE -ne 0) {
        throw "git pull failed."
    }
}

$commit = (& git rev-parse HEAD).Trim()
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = Join-Path $env:TEMP ("warbot-runtime-report-" + $stamp + "-" + $PID)
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$consoleLog = Join-Path $stage "console.txt"
$manifestPath = Join-Path $stage "manifest.json"

$started = Get-Date
$verifyExit = 999

Write-Host ""
Write-Host "=== WAR BOT REMOTE TEST CYCLE ==="
Write-Host "Commit: $commit"
Write-Host "Local report staging: $stage"
Write-Host ""

$verifyArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "verify_mvp.ps1")
)
if ($WipeRuntime) { $verifyArgs += "-WipeRuntime" }
if ($CleanGame) { $verifyArgs += "-CleanGame" }

try {
    & powershell @verifyArgs 2>&1 | Tee-Object -FilePath $consoleLog
    $verifyExit = $LASTEXITCODE
}
catch {
    $_ | Out-String | Tee-Object -FilePath $consoleLog -Append | Write-Host
    $verifyExit = 998
}

# Always ask the runtime for its latest deterministic report. Failure here
# must not hide the original verifier result.
try {
    & python .\native_arm64_poc.py boot-report 2>&1 | Tee-Object -FilePath $consoleLog -Append | Write-Host
}
catch {
    ("boot-report collection failed: " + ($_ | Out-String)) | Tee-Object -FilePath $consoleLog -Append | Write-Host
}

$runtimeRoot = "C:\warbot_arm64_runtime"
$knownFiles = @(
    "boot-diagnostic.json",
    "zygote-crash.txt",
    "adb-crash-buffer.txt",
    "adb-logcat-all.txt",
    "tombstone-probe.txt",
    "boot-live-state.txt",
    "game-crash.txt",
    "qemu-arm64.log",
    "machine.txt"
)

function Copy-DiagnosticText {
    param(
        [string]$Source,
        [string]$Destination,
        [long]$MaxBytes = 20971520
    )
    if (-not (Test-Path $Source)) { return }

    $item = Get-Item $Source
    if ($item.Length -le $MaxBytes) {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
        return
    }

    $note = "<WAR BOT report: original file $($item.Length) bytes; tail only>"
    Set-Content -Encoding UTF8 -Path $Destination -Value $note
    Get-Content -LiteralPath $Source -Tail 30000 | Add-Content -Encoding UTF8 -Path $Destination
}

if (Test-Path $runtimeRoot) {
    foreach ($name in $knownFiles) {
        Copy-DiagnosticText -Source (Join-Path $runtimeRoot $name) -Destination (Join-Path $stage $name)
    }

    $tombstones = Join-Path $runtimeRoot "tombstones"
    if (Test-Path $tombstones) {
        $destTombstones = Join-Path $stage "tombstones"
        New-Item -ItemType Directory -Force -Path $destTombstones | Out-Null
        Get-ChildItem -LiteralPath $tombstones -File -ErrorAction SilentlyContinue | Select-Object -First 20 | ForEach-Object {
            Copy-DiagnosticText -Source $_.FullName -Destination (Join-Path $destTombstones $_.Name) -MaxBytes 5242880
        }
    }
}

$bootstrapFrame = Join-Path $Root "debug\bootstrap-frame.png"
if (Test-Path $bootstrapFrame) {
    Copy-Item $bootstrapFrame (Join-Path $stage "bootstrap-frame.png") -Force
}

$finished = Get-Date
$manifest = [ordered]@{
    schema = 1
    commit = $commit
    source_branch = (& git branch --show-current).Trim()
    started_at = $started.ToString("o")
    finished_at = $finished.ToString("o")
    duration_seconds = [int](($finished - $started).TotalSeconds)
    verify_exit_code = $verifyExit
    result = $(if ($verifyExit -eq 0) { "PASS" } else { "FAIL" })
    runtime_root = $runtimeRoot
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $manifestPath

Write-Host ""
Write-Host "Uploading report to GitHub..."
$uploadExit = 0
try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "upload_runtime_report.ps1") -ReportSource $stage
    $uploadExit = $LASTEXITCODE
}
catch {
    $uploadExit = 997
    Write-Host "REPORT UPLOAD FAILED:"
    Write-Host ($_ | Out-String)
}

Write-Host ""
Write-Host "=== WAR BOT REMOTE TEST CYCLE COMPLETE ==="
Write-Host "Verifier exit code: $verifyExit"
Write-Host "Upload exit code:   $uploadExit"

if ($uploadExit -ne 0) {
    Write-Host "Local report preserved at: $stage"
    exit 90
}

exit $verifyExit
