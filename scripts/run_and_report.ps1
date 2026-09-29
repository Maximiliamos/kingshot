param(
    [ValidateSet("wsa", "native_arm64")]
    [string]$Backend = "wsa",
    [string]$Serial = "127.0.0.1:58526",
    [string]$PairEndpoint = "",
    [string]$PairCode = "",
    [switch]$NoAutoDeveloperModePatch,
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
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $stashMessage = "TUGARIN_BOTS_AUTO_BACKUP_$stamp"
        Write-Host "Local changes detected. Saving them safely to git stash: $stashMessage"
        & git stash push -u -m $stashMessage
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create a safe backup stash. Refusing to continue."
        }
        $afterStash = (& git status --porcelain)
        if ($afterStash) {
            throw "Working tree is still dirty after backup stash. Refusing to continue."
        }
        Write-Host "Local changes preserved in stash '$stashMessage'. They will NOT be dropped automatically."
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
            "-Backend", $Backend,
            "-Serial", $Serial,
            "-SkipPull"
        )
        if ($PairEndpoint) { $reexec += @("-PairEndpoint", $PairEndpoint) }
        if ($PairCode) { $reexec += @("-PairCode", $PairCode) }
        if ($NoAutoDeveloperModePatch) { $reexec += "-NoAutoDeveloperModePatch" }
        if ($WipeRuntime) { $reexec += "-WipeRuntime" }
        if ($CleanGame) { $reexec += "-CleanGame" }
        & powershell @reexec
        exit $LASTEXITCODE
    }
}

$commit = (& git rev-parse HEAD).Trim()

# WSA is the primary MVP path. Its installer performs the host bootstrap,
# current-user AppX registration, Android/game verification and report upload
# as one bounded workflow. Keep the native-QEMU experiment below available
# only when explicitly requested with -Backend native_arm64.
if ($Backend -eq "wsa") {
    $installer = Join-Path $PSScriptRoot "install_wsa_poc.ps1"

    Write-Host ""
    Write-Host "=== TUGARIN BOTS WSA PHASE 1/2: privileged setup ==="
    $prepareArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $installer,
        "-Serial", $Serial,
        "-PrepareOnly"
    )
    if ($NoAutoDeveloperModePatch) { $prepareArgs += "-NoAutoDeveloperModePatch" }

    & powershell @prepareArgs
    $prepareExit = $LASTEXITCODE
    if ($prepareExit -ne 0) {
        Write-Host "WSA setup phase failed with exit code $prepareExit."
        exit $prepareExit
    }

    Write-Host ""
    Write-Host "=== TUGARIN BOTS WSA PHASE 2/2: interactive runtime ==="
    $runtimeArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $installer,
        "-Serial", $Serial,
        "-RuntimeOnly",
        "-SkipInstall"
    )
    if ($PairEndpoint) { $runtimeArgs += @("-PairEndpoint", $PairEndpoint) }
    if ($PairCode) { $runtimeArgs += @("-PairCode", $PairCode) }
    if ($NoAutoDeveloperModePatch) { $runtimeArgs += "-NoAutoDeveloperModePatch" }
    if ($CleanGame) { $runtimeArgs += "-CleanGame" }

    & powershell @runtimeArgs
    exit $LASTEXITCODE
}

# Evidence-driven experiment for the current blocker:
# Upstream QEMU avoids the Google-QEMU SIGSEGV but cannot provide the Android
# goldfish/hwcomposer device model required by this stock Google ARM64 image.
# Return to Google virt and change only the guest CPU model. If cortex-a53
# avoids the static libcodec2 crash seen with cortex-a57, the bug is narrowed
# to the Google QEMU A57 TCG translation path rather than Android userspace.
$env:WAR_BOT_RUNTIME_EXPERIMENT = "google-virt-a53-1cpu-single-tcg"
$env:WAR_BOT_ARM64_MACHINE = "virt"
$env:WAR_BOT_ARM64_CPU = "cortex-a53"
$env:WAR_BOT_ARM64_CPU_CORES = "1"
$env:WAR_BOT_ARM64_TCG_THREAD = "single"
$env:WAR_BOT_ARM64_POST_ADB_TIMEOUT = "180"
$WipeRuntime = $true

$configJson = (& python .\native_arm64_poc.py config | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read effective native ARM64 runtime config."
}
$config = $configJson | ConvertFrom-Json
Write-Host "Runtime experiment: $($config.experiment)"
Write-Host "Runtime machine:    $($config.machine)"
Write-Host "Runtime CPU model:  $($config.cpu)"
Write-Host "Runtime CPU cores:  $($config.cpu_cores)"
Write-Host "TCG thread mode:    $($config.tcg_thread_mode)"
if (
    $config.experiment -ne "google-virt-a53-1cpu-single-tcg" -or
    $config.machine -ne "virt" -or
    $config.cpu -ne "cortex-a53" -or
    [int]$config.cpu_cores -ne 1 -or
    $config.tcg_thread_mode -ne "single"
) {
    throw (
        "Experiment propagation guard failed. Refusing expensive host run. " +
        "Expected Google virt / cortex-a53 / 1 CPU / single TCG."
    )
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = Join-Path $env:TEMP ("warbot-runtime-report-" + $stamp + "-" + $PID)
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$consoleLog = Join-Path $stage "console.txt"
$manifestPath = Join-Path $stage "manifest.json"

# Remove host-side evidence from prior experiments. Guest userdata/cache are
# handled separately by -WipeRuntime; this only prevents stale tombstones or
# stale boot reports from contaminating the new upload.
$runtimeRoot = "C:\warbot_arm64_runtime"
$staleFiles = @(
    "boot-diagnostic.json",
    "zygote-crash.txt",
    "adb-crash-buffer.txt",
    "adb-logcat-all.txt",
    "adb-root-status.txt",
    "adb-dmesg.txt",
    "tombstone-probe.txt",
    "boot-live-state.txt",
    "game-crash.txt",
    "upstream-diagnostic.json",
    "upstream-qemu-arm64.log"
)
foreach ($name in $staleFiles) {
    Remove-Item -LiteralPath (Join-Path $runtimeRoot $name) -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath (Join-Path $runtimeRoot "tombstones") -Recurse -Force -ErrorAction SilentlyContinue

# Persist the exact Android Emulator toolchain version used for this A/B.
$emulatorExe = "C:\Android\Sdk\emulator\emulator.exe"
$emulatorVersionPath = Join-Path $stage "emulator-version.txt"
if (Test-Path $emulatorExe) {
    (& $emulatorExe -version 2>&1 | Out-String) |
        Set-Content -Encoding UTF8 $emulatorVersionPath
    Write-Host "Android Emulator version:"
    Get-Content $emulatorVersionPath | Select-Object -First 8 | Write-Host
}
else {
    "emulator.exe not found at $emulatorExe" | Set-Content -Encoding UTF8 $emulatorVersionPath
}


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

$savedErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & powershell @verifyArgs 2>&1 | Tee-Object -FilePath $consoleLog
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
    arm64_machine = $env:WAR_BOT_ARM64_MACHINE
    arm64_cpu = $env:WAR_BOT_ARM64_CPU
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
