param(
    [string]$Branch = "feature/unified-android-backend",
    [int]$FlowTimeoutMinutes = 45,
    [int]$SoakTimeoutMinutes = 90,
    [int]$SoakCharacters = 2,
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

if (-not $SkipPull) {
    $dirty = (& git status --porcelain)
    if ($dirty) {
        throw "Working tree is dirty. Commit/stash local work before full MVP acceptance."
    }

    $before = (& git rev-parse HEAD).Trim()
    & git pull --ff-only origin $Branch
    if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only failed." }
    $after = (& git rev-parse HEAD).Trim()

    if ($before -ne $after -and $env:TUGARIN_FULL_REPORT_REEXEC -ne "1") {
        $env:TUGARIN_FULL_REPORT_REEXEC = "1"
        $args = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", $PSCommandPath,
            "-Branch", $Branch,
            "-FlowTimeoutMinutes", [string]$FlowTimeoutMinutes,
            "-SoakTimeoutMinutes", [string]$SoakTimeoutMinutes,
            "-SoakCharacters", [string][Math]::Max(2, $SoakCharacters),
            "-SkipPull"
        )
        & powershell.exe @args
        exit $LASTEXITCODE
    }
}

$commit = (& git rev-parse HEAD).Trim()
$upstream = (& git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null | Out-String).Trim()
$upstreamHead = if ($upstream) {
    (& git rev-parse $upstream 2>$null | Out-String).Trim()
} else { "" }
if (-not $upstream -or $upstreamHead -ne $commit) {
    throw "Local HEAD must equal published upstream before host acceptance. local=$commit upstream=$upstreamHead"
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

$savedErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & powershell.exe @verifyArgs 2>&1 | Tee-Object -FilePath $consoleLog
    $verifyExit = [int]$LASTEXITCODE
}
finally {
    $ErrorActionPreference = $savedErrorAction
}

$debugEvidence = @(
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
    "runtime-heartbeat.json"
)
foreach ($name in $debugEvidence) {
    Copy-IfExists -Source (Join-Path $Root "debug\$name") -Destination (Join-Path $stage $name)
}

foreach ($name in @("state.json", "state.previous.json", "control.json")) {
    Copy-IfExists -Source (Join-Path $Root $name) -Destination (Join-Path $stage $name)
}
Copy-IfExists -Source (Join-Path $Root "logs\events.jsonl") -Destination (Join-Path $stage "events.jsonl")
Copy-IfExists -Source (Join-Path $Root "logs\bot.log") -Destination (Join-Path $stage "bot.log")
Copy-IfExists -Source "C:\warbot_wsa\reports\LATEST-LOCAL.json" -Destination (Join-Path $stage "wsa-latest-local.json")

$fullEvidencePath = Join-Path $Root "debug\mvp-full-acceptance.json"
$overall = "fail"
if ($verifyExit -eq 0 -and (Test-Path -LiteralPath $fullEvidencePath)) {
    try {
        $full = Get-Content -LiteralPath $fullEvidencePath -Raw | ConvertFrom-Json
        if ([string]$full.overall -eq "pass") { $overall = "pass" }
    }
    catch {}
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
    source_branch = (& git branch --show-current).Trim()
    upstream = $upstream
    upstream_commit = $upstreamHead
    started_at = $started.ToString("o")
    finished_at = $finished.ToString("o")
    duration_seconds = [int](($finished - $started).TotalSeconds)
    verify_exit_code = $verifyExit
    overall = $overall
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
