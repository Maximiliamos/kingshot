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

    $beforePull = (& git rev-parse HEAD).Trim()
    Write-Host "Updating feature/unified-android-backend..."
    & git pull --ff-only origin feature/unified-android-backend
    if ($LASTEXITCODE -ne 0) {
        throw "git pull failed."
    }
    $afterPull = (& git rev-parse HEAD).Trim()

    # PowerShell parses the current script before git pull. If the pull updated
    # this workflow itself, immediately re-exec the new on-disk version so one
    # user command always runs the newest test/diagnostic plan.
    if ($beforePull -ne $afterPull -and $env:WAR_BOT_REPORT_REEXEC -ne "1") {
        Write-Host "Workflow updated; restarting with the new script..."
        $env:WAR_BOT_REPORT_REEXEC = "1"
        $reexec = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $PSCommandPath,
            "-SkipPull"
        )
        if ($WipeRuntime) { $reexec += "-WipeRuntime" }
        if ($CleanGame) { $reexec += "-CleanGame" }
        & powershell @reexec
        exit $LASTEXITCODE
    }
}

$commit = (& git rev-parse HEAD).Trim()

# Evidence-driven experiment for the current blocker:
# Google Android-QEMU reproduces native app_process64/libcodec2 SIGSEGV, while
# upstream QEMU crossed the same ~228s crash point and reached zygote/adbd with
# no SIGSEGV. Keep the upstream guest alive longer and verify whether TCP ADB
# becomes usable and whether sys.boot_completed reaches 1.
$env:WAR_BOT_RUNTIME_EXPERIMENT = "upstream-virt-1cpu-single-tcg"
$env:WAR_BOT_ARM64_CPU_CORES = "1"
$env:WAR_BOT_ARM64_TCG_THREAD = "single"
$env:WAR_BOT_ARM64_POST_ADB_TIMEOUT = "150"
$WipeRuntime = $true

$configJson = (& python .\native_arm64_poc.py config | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read effective native ARM64 runtime config."
}
$config = $configJson | ConvertFrom-Json
Write-Host "Runtime experiment: $($config.experiment)"
Write-Host "Runtime CPU cores:  $($config.cpu_cores)"
Write-Host "TCG thread mode:    $($config.tcg_thread_mode)"
if (
    $config.experiment -ne "upstream-virt-1cpu-single-tcg" -or
    [int]$config.cpu_cores -ne 1 -or
    $config.tcg_thread_mode -ne "single"
) {
    throw (
        "Experiment propagation guard failed. Refusing expensive host run. " +
        "Expected upstream-virt-1cpu-single-tcg / 1 CPU / single TCG."
    )
}

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

$probeArgs = @(
    ".\native_arm64_poc.py",
    "upstream-diagnose",
    "--duration-seconds", "600"
)

$savedErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & python @probeArgs 2>&1 | Tee-Object -FilePath $consoleLog
    $verifyExit = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $savedErrorAction
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
    "adb-root-status.txt",
    "adb-dmesg.txt",
    "tombstone-probe.txt",
    "boot-live-state.txt",
    "game-crash.txt",
    "qemu-arm64.log",
    "upstream-qemu-arm64.log",
    "upstream-diagnostic.json",
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
    result = "DIAGNOSTIC"
    runtime_root = $runtimeRoot
    experiment = $env:WAR_BOT_RUNTIME_EXPERIMENT
    arm64_cpu_cores = $env:WAR_BOT_ARM64_CPU_CORES
    tcg_thread_mode = $env:WAR_BOT_ARM64_TCG_THREAD
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
