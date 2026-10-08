param(
    [string]$Branch = "feature/unified-android-backend",
    [int]$FlowTimeoutMinutes = 45,
    [int]$SoakTimeoutMinutes = 90,
    [int]$SoakCharacters = 2,
    [string]$ExpectedCommit = "",
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Copy-IfExists {
    param([string]$Source, [string]$Destination)
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
    }
}

$RunEvidenceNames = @(
    "mvp-full-acceptance.json",
    "google-services.json",
    "google-services.png",
    "preview-production.json",
    "preview-production.png",
    "gui-host-smoke.json",
    "operator-io-smoke.json",
    "recovery-smoke.json",
    "mvp-game-flow-evidence.json",
    "mvp-soak-evidence.json",
    "mvp-final-process-audit.json",
    "mvp-failure-process-audit.json",
    "runtime-heartbeat.json",
    "android-data-free-space.json",
    "tutorial-perception-failure.json",
    "game-resource-network-diagnostics.json",
    "resource-readiness.json"
)

function Clear-PreviousRunEvidence {
    $debugRoot = Join-Path $Root "debug"
    $perceptionPath = Join-Path $debugRoot "tutorial-perception-failure.json"

    # The perception JSON can point at run-specific images with dynamic names.
    # Remove those first so a failed later run cannot package old screenshots.
    if (Test-Path -LiteralPath $perceptionPath -PathType Leaf) {
        try {
            $oldPerception = Get-Content -LiteralPath $perceptionPath -Raw | ConvertFrom-Json
            foreach ($property in @("full_frame", "normalized_frame", "annotated_frame")) {
                $candidate = [string]$oldPerception.$property
                if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                    Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
                }
            }
        }
        catch {
            Write-Warning "Could not parse stale tutorial perception evidence before cleanup: $($_.Exception.Message)"
        }
    }

    foreach ($name in $RunEvidenceNames) {
        Remove-Item -LiteralPath (Join-Path $debugRoot $name) -Force -ErrorAction SilentlyContinue
    }
}

$currentBranch = (& git branch --show-current | Out-String).Trim()
if ($currentBranch -ne $Branch) {
    throw "Refusing full MVP acceptance on branch '$currentBranch'; expected '$Branch'."
}

$localHead = (& git rev-parse HEAD).Trim()
if ($ExpectedCommit -and $localHead -ne $ExpectedCommit) {
    throw "Refusing full MVP acceptance on unexpected SHA. local=$localHead expected=$ExpectedCommit"
}

if (-not $SkipPull) {
    $dirty = (& git status --porcelain)
    if ($dirty) {
        throw "Working tree is dirty. Commit/stash local work before full MVP acceptance."
    }

    $before = (& git rev-parse HEAD).Trim()
    # Windows PowerShell 5.1 can promote native stderr to NativeCommandError
    # when ErrorActionPreference=Stop. git pull is optional once ExpectedCommit
    # already pins the exact release SHA, so capture its exit code explicitly.
    $savedErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $pullOutput = (& git pull --ff-only origin $Branch 2>&1 | Out-String).Trim()
        $pullCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorAction
    }
    if ($pullCode -ne 0) {
        if ($ExpectedCommit -and $before -eq $ExpectedCommit) {
            Write-Host "WARNING: git pull failed, but local HEAD exactly matches pinned release SHA."
            Write-Host "Continuing host acceptance at pinned SHA: $ExpectedCommit"
            if ($pullOutput) { Write-Host $pullOutput }
        }
        else {
            throw "git pull --ff-only failed and local HEAD is not protected by -ExpectedCommit. $pullOutput"
        }
    }
    $after = (& git rev-parse HEAD).Trim()

    if ($ExpectedCommit -and $after -ne $ExpectedCommit) {
        throw "Published branch moved away from pinned release SHA. local=$after expected=$ExpectedCommit"
    }

    if ($before -ne $after -and $env:TUGARIN_FULL_REPORT_REEXEC -ne "1") {
        $env:TUGARIN_FULL_REPORT_REEXEC = "1"
        $args = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", $PSCommandPath,
            "-Branch", $Branch,
            "-FlowTimeoutMinutes", [string]$FlowTimeoutMinutes,
            "-SoakTimeoutMinutes", [string]$SoakTimeoutMinutes,
            "-SoakCharacters", [string][Math]::Max(2, $SoakCharacters),
            "-ExpectedCommit", $ExpectedCommit,
            "-SkipPull"
        )
        & powershell.exe @args
        exit $LASTEXITCODE
    }
}

$commit = (& git rev-parse HEAD).Trim()
if ($ExpectedCommit -and $commit -ne $ExpectedCommit) {
    throw "Local HEAD changed during preflight. local=$commit expected=$ExpectedCommit"
}
$upstream = (& git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null | Out-String).Trim()
$upstreamHead = if ($upstream) {
    (& git rev-parse $upstream 2>$null | Out-String).Trim()
} else { "" }
if (-not $upstream -or $upstreamHead -ne $commit) {
    if ($ExpectedCommit -and $commit -eq $ExpectedCommit) {
        Write-Host "WARNING: upstream tracking ref is unavailable/stale, but exact pinned release SHA is verified locally."
    }
    else {
        throw "Local HEAD must equal published upstream before host acceptance. local=$commit upstream=$upstreamHead"
    }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = Join-Path $env:TEMP ("tugarin-mvp-report-" + $stamp + "-" + $PID)
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$consoleLog = Join-Path $stage "console.txt"
$started = Get-Date
$verifyExit = 999

$verifyArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "verify_mvp_full.ps1"),
    "-FlowTimeoutMinutes", [string]$FlowTimeoutMinutes,
    "-SoakTimeoutMinutes", [string]$SoakTimeoutMinutes,
    "-SoakCharacters", [string][Math]::Max(2, $SoakCharacters)
)

# Never allow a failed/partial run to inherit release evidence from an older
# acceptance. Each full run starts from an empty release-evidence set.
Clear-PreviousRunEvidence

$savedErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & powershell.exe @verifyArgs 2>&1 | Tee-Object -FilePath $consoleLog
    $verifyExit = [int]$LASTEXITCODE
}
finally {
    $ErrorActionPreference = $savedErrorAction
}

$debugEvidence = @($RunEvidenceNames)
foreach ($name in $debugEvidence) {
    Copy-IfExists -Source (Join-Path $Root "debug\$name") -Destination (Join-Path $stage $name)
}

$perceptionEvidencePath = Join-Path $Root "debug\tutorial-perception-failure.json"
if (Test-Path -LiteralPath $perceptionEvidencePath) {
    try {
        $perceptionEvidence = Get-Content -LiteralPath $perceptionEvidencePath -Raw | ConvertFrom-Json
        foreach ($property in @("full_frame", "normalized_frame", "annotated_frame")) {
            $candidate = [string]$perceptionEvidence.$property
            if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                Copy-IfExists -Source $candidate -Destination (Join-Path $stage ([IO.Path]::GetFileName($candidate)))
            }
        }
    }
    catch {
        Write-Warning "Could not collect tutorial perception image evidence: $($_.Exception.Message)"
    }
}

foreach ($name in @("state.json", "state.previous.json", "control.json")) {
    Copy-IfExists -Source (Join-Path $Root $name) -Destination (Join-Path $stage $name)
}
# Cumulative logs may contain previous users, runs and credentials. Never
# export them verbatim. Current-run bot log events are present in events.jsonl,
# and console.txt is already scoped to this host verifier invocation.
Copy-IfExists -Source "C:\warbot_wsa\reports\LATEST-LOCAL.json" -Destination (Join-Path $stage "wsa-latest-local.json")

$fullEvidencePath = Join-Path $Root "debug\mvp-full-acceptance.json"
$overall = "fail"
$acceptanceRunId = ""
$acceptanceHead = ""
if (Test-Path -LiteralPath $fullEvidencePath -PathType Leaf) {
    try {
        $full = Get-Content -LiteralPath $fullEvidencePath -Raw | ConvertFrom-Json
        $acceptanceRunId = [string]$full.run_id
        $acceptanceHead = [string]$full.head
        if (
            $verifyExit -eq 0 -and
            [string]$full.overall -eq "pass" -and
            $acceptanceHead -eq $commit -and
            $acceptanceRunId
        ) {
            $overall = "pass"
        }
        elseif ($verifyExit -eq 0) {
            Write-Warning "Verifier exited 0, but acceptance evidence does not prove PASS for current HEAD/run_id."
        }
    }
    catch {
        Write-Warning "Could not parse current full acceptance evidence: $($_.Exception.Message)"
    }
}

# Filter to this exact acceptance run, rather than shipping years of appended
# events. Missing run_id is a failure to attribute data and is never published.
if ($acceptanceRunId) {
    $sourceEvents = Join-Path $Root "logs\\events.jsonl"
    $destEvents = Join-Path $stage "events.jsonl"
    if (Test-Path -LiteralPath $sourceEvents -PathType Leaf) {
        $eventWriter = New-Object System.IO.StreamWriter($destEvents, $false, (New-Object System.Text.UTF8Encoding($false)))
        try {
            foreach ($line in [IO.File]::ReadLines($sourceEvents)) {
                try { $item = $line | ConvertFrom-Json -ErrorAction Stop }
                catch { continue }
                if ([string]$item.run_id -cne $acceptanceRunId) { continue }
                $safeLine = ConvertTo-Json -InputObject $item -Depth 10 -Compress
                # Redact common credentials and personal email before upload.
                $safeLine = [regex]::Replace($safeLine, '(?i)(bearer\\s+)[a-z0-9._~+/-]+', '$1[REDACTED]')
                $safeLine = [regex]::Replace($safeLine, '(?i)((?:token|password|api[_-]?key)[=:]\\s*)[^\\\\\\s,;\"]+', '$1[REDACTED]')
                $safeLine = [regex]::Replace($safeLine, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', '[REDACTED_EMAIL]')
                $eventWriter.WriteLine($safeLine)
            }
        }
        finally { $eventWriter.Dispose() }
    }
}
$evidenceInventory = @()
Get-ChildItem -LiteralPath $stage -File -ErrorAction SilentlyContinue | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $evidenceInventory += [ordered]@{
        name = $_.Name
        size_bytes = [int64]$_.Length
        sha256 = $hash
    }
}

$finished = Get-Date
$manifest = [ordered]@{
    schema = 1
    kind = "mvp-full-host"
    commit = $commit
    expected_commit = $ExpectedCommit
    source_branch = (& git branch --show-current).Trim()
    upstream = $upstream
    upstream_commit = $upstreamHead
    started_at = $started.ToString("o")
    finished_at = $finished.ToString("o")
    duration_seconds = [int](($finished - $started).TotalSeconds)
    verify_exit_code = $verifyExit
    overall = $overall
    event_log_scope = "exact_acceptance_run_id_only"
    cumulative_bot_log_exported = $false
    acceptance_run_id = $acceptanceRunId
    acceptance_head = $acceptanceHead
    flow_timeout_minutes = $FlowTimeoutMinutes
    soak_timeout_minutes = $SoakTimeoutMinutes
    soak_characters = [Math]::Max(2, $SoakCharacters)
    evidence_files = @($evidenceInventory)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $stage "manifest.json")

Write-Host ""
Write-Host "Uploading full MVP host evidence..."
$uploadExit = 0
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "upload_runtime_report.ps1") -ReportSource $stage
    $uploadExit = [int]$LASTEXITCODE
}
catch {
    $uploadExit = 997
    Write-Host "REPORT UPLOAD FAILED:"
    Write-Host ($_ | Out-String)
}

Write-Host ""
Write-Host "=== TUGARIN BOTS FULL MVP REPORT COMPLETE ==="
Write-Host "Commit:             $commit"
Write-Host "Verifier exit code: $verifyExit"
Write-Host "Overall:            $overall"
Write-Host "Upload exit code:   $uploadExit"
if ($uploadExit -ne 0) {
    Write-Host "Local report preserved at: $stage"
    exit 90
}
exit $verifyExit
