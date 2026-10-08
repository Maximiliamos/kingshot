param(
    [switch]$SkipInfrastructure,
    [switch]$PreflightOnly,
    [string]$TargetUser = "",
    [string]$ExpectedSid = "S-1-5-21-1641294696-4270169483-3689275233-1007",
    [int]$FlowTimeoutMinutes = 45,
    [int]$SoakTimeoutMinutes = 90,
    [int]$SoakCharacters = 2
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# This check must remain before evidence creation and before every gate.  WSA
# AppX registration and runtime belong to one Windows SID; running the
# destructive flow from a different account could clear the dedicated user's
# game while auditing the wrong process/session context.
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentSid = [string]$currentIdentity.User.Value
$currentName = [string]$currentIdentity.Name

# Windows PowerShell 5.1 treats UTF-8 without BOM as the active ANSI code page.
# Do not keep a Cyrillic account name as executable source text. The SID is the
# release identity source of truth; resolve its Unicode NTAccount at runtime.
try {
    $expectedSidObject = New-Object Security.Principal.SecurityIdentifier -ArgumentList $ExpectedSid
    $targetAccount = [string]$expectedSidObject.Translate(
        [Security.Principal.NTAccount]
    ).Value
}
catch {
    Write-Host "MVP preflight: cannot resolve expected SID $ExpectedSid to a Windows account." -ForegroundColor Red
    exit 91
}

$resolvedTargetUser = ($targetAccount -split '\\')[-1]
if ($TargetUser -and $TargetUser -ne $resolvedTargetUser) {
    Write-Host "MVP preflight: requested target user does not match ExpectedSid account." -ForegroundColor Red
    exit 91
}
$TargetUser = $resolvedTargetUser

if ($currentSid -ne $ExpectedSid) {
    $identityError = (
        "MVP preflight refused before any gate: current={0} sid={1}; required={2} sid={3}."
    ) -f $currentName, $currentSid, $targetAccount, $ExpectedSid
    Write-Host $identityError -ForegroundColor Red
    exit 91
}

Write-Host "SID preflight PASS: $currentName ($currentSid)"
if ($PreflightOnly) {
    Write-Host "MVP PRECHECK PASS (non-destructive; no acceptance gates executed)."
    exit 0
}

$EvidencePath = Join-Path $Root "debug\mvp-full-acceptance.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EvidencePath) | Out-Null
$GateResults = [System.Collections.Generic.List[object]]::new()
$StartedAt = [DateTimeOffset]::UtcNow
$RunId = [Guid]::NewGuid().ToString("N")

function Save-AcceptanceEvidence {
    param(
        [string]$Overall = "running",
        [string]$FailedGate = "",
        [int]$ExitCode = 0
    )
    $payload = [ordered]@{
        schema = 1
        product = "TUGARIN BOTS"
        overall = $Overall
        head = $head
        run_id = $RunId
        windows_user = $currentName
        windows_sid = $currentSid
        started_at_utc = $StartedAt.ToString("o")
        finished_at_utc = if ($Overall -eq "running") { $null } else { [DateTimeOffset]::UtcNow.ToString("o") }
        failed_gate = $FailedGate
        exit_code = $ExitCode
        gates = @($GateResults)
    }
    # PowerShell uses case-insensitive dynamic scoping: Run-Gate's foreach
    # $evidencePath previously SHADOWED this script variable, so each saved
    # acceptance snapshot overwrote the preceding gate's JSON evidence.
    # Explicit script scope makes the acceptance path immutable.
    $tmp = "${script:EvidencePath}.tmp"
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $script:EvidencePath -Force
}

function Invoke-FinalProcessAudit {
    param([string]$Suffix = "final")
    $auditPath = Join-Path $Root "debug\mvp-$Suffix-process-audit.json"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\audit_runtime_processes.ps1") `
        -TargetUser $TargetUser -Output $auditPath | Out-Host
    $auditCode = [int]$LASTEXITCODE
    return $auditCode
}

function Run-Gate {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Script,
        [string[]]$Arguments = @(),
        [string[]]$EvidencePaths = @()
    )
    Write-Host ""
    Write-Host ("=" * 72)
    Write-Host "GATE: $Name"
    Write-Host ("=" * 72)
    $gateStarted = [DateTimeOffset]::UtcNow
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script @Arguments
    $code = [int]$LASTEXITCODE
    $missingEvidence = @()
    if ($code -eq 0) {
        foreach ($gateEvidencePath in @($EvidencePaths)) {
            if ($gateEvidencePath -and -not (Test-Path -LiteralPath $gateEvidencePath -PathType Leaf)) {
                $missingEvidence += [string]$gateEvidencePath
            }
        }
        if ($missingEvidence.Count -gt 0) {
            $code = 93
            Write-Host "GATE FAIL: required evidence file(s) missing:" -ForegroundColor Red
            $missingEvidence | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        }
        # A file's existence is not evidence of gate success. During a prior
        # host run a dynamically scoped PowerShell path silently overwrote
        # per-gate JSON with intermediate acceptance snapshots. Require the
        # actual gate result to explicitly say pass=true as well.
        if ($code -eq 0) {
            $verifiedGateJson = @(
                "google-services.json", "preview-production.json",
                "gui-host-smoke.json", "operator-io-smoke.json",
                "recovery-smoke.json", "resource-readiness.json", "mvp-game-flow-evidence.json",
                "mvp-soak-evidence.json"
            )
            foreach ($gateEvidencePath in @($EvidencePaths)) {
                if ([IO.Path]::GetFileName($gateEvidencePath) -notin $verifiedGateJson) {
                    continue
                }
                try {
                    $gateEvidence = Get-Content -LiteralPath $gateEvidencePath -Raw | ConvertFrom-Json
                    if (-not ($gateEvidence.PSObject.Properties.Name -contains "pass") -or -not [bool]$gateEvidence.pass) {
                        throw "pass=true missing or false"
                    }
                }
                catch {
                    $code = 94
                    Write-Host "GATE FAIL: invalid or non-PASS evidence JSON: $gateEvidencePath ($($_.Exception.Message))" -ForegroundColor Red
                    break
                }
            }
        }
    }
    $GateResults.Add([ordered]@{
        name = $Name
        script = [IO.Path]::GetFileName($Script)
        pass = ($code -eq 0)
        exit_code = $code
        started_at_utc = $gateStarted.ToString("o")
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        evidence = @($EvidencePaths)
        missing_evidence = @($missingEvidence)
    })
    if ($code -ne 0) {
        # Every failure path still records whether a GUI/bot/video process was
        # left behind.  The audit is evidence-only and never kills an unrelated
        # process belonging to the dedicated account.
        $cleanupCode = Invoke-FinalProcessAudit -Suffix "failure"
        $GateResults.Add([ordered]@{
            name = "Failure-path process audit"
            script = "audit_runtime_processes.ps1"
            pass = ($cleanupCode -eq 0)
            exit_code = $cleanupCode
            started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        })
        Save-AcceptanceEvidence -Overall "fail" -FailedGate $Name -ExitCode $code
        Write-Host ""
        Write-Host "MVP 1.0 HOST ACCEPTANCE FAIL at: $Name (exit=$code)"
        Write-Host "Evidence: $EvidencePath"
        exit $code
    }
    Save-AcceptanceEvidence
}

Write-Host "=== TUGARIN BOTS MVP 1.0 FULL HOST ACCEPTANCE ==="
Write-Host "Repository: $Root"
$head = (& git -C $Root rev-parse HEAD | Out-String).Trim()
$env:TUGARIN_ACCEPTANCE_RUN_ID = $RunId
$env:TUGARIN_ACCEPTANCE_HEAD = $head
Write-Host "HEAD: $head"
Write-Host "Acceptance run: $RunId"

$trackedChanges = (& git -C $Root status --porcelain --untracked-files=no | Out-String).Trim()
if ($trackedChanges) {
    $GateResults.Add([ordered]@{
        name = "Clean tracked worktree"
        script = "git status --porcelain --untracked-files=no"
        pass = $false
        exit_code = 90
        started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        detail = $trackedChanges
    })
    Save-AcceptanceEvidence -Overall "fail" -FailedGate "Clean tracked worktree" -ExitCode 90
    Write-Host ""
    Write-Host "MVP 1.0 HOST ACCEPTANCE FAIL: tracked worktree is dirty."
    Write-Host $trackedChanges
    Write-Host "Evidence: $EvidencePath"
    exit 90
}

$GateResults.Add([ordered]@{
    name = "Clean tracked worktree"
    script = "git status --porcelain --untracked-files=no"
    pass = $true
    exit_code = 0
    started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
})

$upstream = (& git -C $Root rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null | Out-String).Trim()
$upstreamHead = if ($upstream) {
    (& git -C $Root rev-parse $upstream 2>$null | Out-String).Trim()
} else { "" }
if (-not $upstream -or $upstreamHead -ne $head) {
    $GateResults.Add([ordered]@{
        name = "Published branch head"
        script = "git rev-parse @{u}"
        pass = $false
        exit_code = 92
        started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        detail = "local=$head upstream=$upstream upstream_head=$upstreamHead"
    })
    Save-AcceptanceEvidence -Overall "fail" -FailedGate "Published branch head" -ExitCode 92
    Write-Host "MVP 1.0 HOST ACCEPTANCE FAIL: local HEAD is not the published upstream HEAD."
    Write-Host "Local: $head"
    Write-Host "Upstream: $upstreamHead"
    exit 92
}
$GateResults.Add([ordered]@{
    name = "Published branch head"
    script = "git rev-parse @{u}"
    pass = $true
    exit_code = 0
    started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    detail = "$upstream@$upstreamHead"
})
Save-AcceptanceEvidence
Write-Host ""
Write-Host "IMPORTANT: readiness probes game UI without clearing data; game-flow and soak subsequently clear Kingshot app data."
Write-Host "The PC-side Tugarin nickname counter is preserved."

if (-not $SkipInfrastructure) {
    Run-Gate -Name "Infrastructure / WSA / Kingshot / process audit" `
        -Script (Join-Path $Root "scripts\verify_release.ps1") `
        -EvidencePaths @("C:\warbot_wsa\reports\LATEST-LOCAL.json")
}

Run-Gate -Name "Google Services / Play Store / account" `
    -Script (Join-Path $Root "scripts\verify_google_services.ps1") `
    -EvidencePaths @(
        (Join-Path $Root "debug\google-services.json"),
        (Join-Path $Root "debug\google-services.png")
    )
Run-Gate -Name "Production PrintWindow preview" `
    -Script (Join-Path $Root "scripts\verify_preview.ps1") `
    -EvidencePaths @(
        (Join-Path $Root "debug\preview-production.json"),
        (Join-Path $Root "debug\preview-production.png")
    )
Run-Gate -Name "Consoleless GUI render" `
    -Script (Join-Path $Root "scripts\verify_gui.ps1") `
    -EvidencePaths @((Join-Path $Root "debug\gui-host-smoke.json"))
Run-Gate -Name "Operator Unicode/UI/audio channel" `
    -Script (Join-Path $Root "scripts\verify_operator_io.ps1") `
    -EvidencePaths @((Join-Path $Root "debug\operator-io-smoke.json"))
Run-Gate -Name "Bounded game + ADB recovery" `
    -Script (Join-Path $Root "scripts\verify_recovery.ps1") `
    -EvidencePaths @((Join-Path $Root "debug\recovery-smoke.json"))
# Mandatory non-destructive positive game UI verification immediately before
# any acceptance step that clears Kingshot app data. Network PASS is not proof.
Run-Gate -Name "Non-destructive resource readiness" `
    -Script (Join-Path $Root "scripts\\verify_resource_readiness.ps1") `
    -EvidencePaths @((Join-Path $Root "debug\\resource-readiness.json"))
Run-Gate -Name "Exact State #3 -> tutorial -> Tugarin<N>" `
    -Script (Join-Path $Root "scripts\verify_game_flow.ps1") `
    -Arguments @("-TimeoutMinutes", [string]$FlowTimeoutMinutes) `
    -EvidencePaths @((Join-Path $Root "debug\mvp-game-flow-evidence.json"))
Run-Gate -Name "Multi-cycle soak" `
    -Script (Join-Path $Root "scripts\verify_soak.ps1") `
    -Arguments @(
        "-TimeoutMinutes", [string]$SoakTimeoutMinutes,
        "-MinCharacters", [string][Math]::Max(2, $SoakCharacters)
    ) `
    -EvidencePaths @((Join-Path $Root "debug\mvp-soak-evidence.json"))

Run-Gate -Name "Final production-user process audit" `
    -Script (Join-Path $Root "scripts\audit_runtime_processes.ps1") `
    -Arguments @(
        "-TargetUser", $TargetUser,
        "-Output", (Join-Path $Root "debug\mvp-final-process-audit.json")
    ) `
    -EvidencePaths @((Join-Path $Root "debug\mvp-final-process-audit.json"))

Save-AcceptanceEvidence -Overall "pass" -ExitCode 0
Write-Host ""
Write-Host ("=" * 72)
Write-Host "MVP 1.0 HOST ACCEPTANCE PASS"
Write-Host "HEAD: $head"
Write-Host "Evidence: $EvidencePath"
Write-Host ("=" * 72)
exit 0
