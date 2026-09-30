param(
    [switch]$SkipInfrastructure,
    [int]$FlowTimeoutMinutes = 45,
    [int]$SoakTimeoutMinutes = 90,
    [int]$SoakCharacters = 2
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$EvidencePath = Join-Path $Root "debug\mvp-full-acceptance.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EvidencePath) | Out-Null
$GateResults = [System.Collections.Generic.List[object]]::new()
$StartedAt = [DateTimeOffset]::UtcNow

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
        started_at_utc = $StartedAt.ToString("o")
        finished_at_utc = if ($Overall -eq "running") { $null } else { [DateTimeOffset]::UtcNow.ToString("o") }
        failed_gate = $FailedGate
        exit_code = $ExitCode
        gates = @($GateResults)
    }
    $tmp = "$EvidencePath.tmp"
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $EvidencePath -Force
}

function Run-Gate {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Script,
        [string[]]$Arguments = @()
    )
    Write-Host ""
    Write-Host ("=" * 72)
    Write-Host "GATE: $Name"
    Write-Host ("=" * 72)
    $gateStarted = [DateTimeOffset]::UtcNow
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script @Arguments
    $code = [int]$LASTEXITCODE
    $GateResults.Add([ordered]@{
        name = $Name
        script = [IO.Path]::GetFileName($Script)
        pass = ($code -eq 0)
        exit_code = $code
        started_at_utc = $gateStarted.ToString("o")
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    })
    if ($code -ne 0) {
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
Write-Host "HEAD: $head"
Save-AcceptanceEvidence
Write-Host ""
Write-Host "IMPORTANT: game-flow and soak gates intentionally clear Kingshot app data."
Write-Host "The PC-side Tugarin nickname counter is preserved."

if (-not $SkipInfrastructure) {
    Run-Gate -Name "Infrastructure / WSA / Kingshot / process audit" -Script (Join-Path $Root "scripts\verify_release.ps1")
}

Run-Gate -Name "Low-latency scrcpy H.264 preview" -Script (Join-Path $Root "scripts\verify_preview.ps1")
Run-Gate -Name "Consoleless GUI render" -Script (Join-Path $Root "scripts\verify_gui.ps1")
Run-Gate -Name "Operator Unicode/UI/audio channel" -Script (Join-Path $Root "scripts\verify_operator_io.ps1")
Run-Gate -Name "Bounded game + ADB recovery" -Script (Join-Path $Root "scripts\verify_recovery.ps1")
Run-Gate -Name "Exact State #3 -> tutorial -> Tugarin<N>" -Script (Join-Path $Root "scripts\verify_game_flow.ps1") -Arguments @("-TimeoutMinutes", [string]$FlowTimeoutMinutes)
Run-Gate -Name "Multi-cycle soak" -Script (Join-Path $Root "scripts\verify_soak.ps1") -Arguments @(
    "-TimeoutMinutes", [string]$SoakTimeoutMinutes,
    "-MinCharacters", [string][Math]::Max(2, $SoakCharacters)
)

Run-Gate -Name "Final current-user process cleanup" -Script (Join-Path $Root "scripts\audit_runtime_processes.ps1") -Arguments @(
    "-TargetUser", [string]$env:USERNAME,
    "-Output", (Join-Path $Root "debug\mvp-final-process-audit.json")
)

Save-AcceptanceEvidence -Overall "pass" -ExitCode 0
Write-Host ""
Write-Host ("=" * 72)
Write-Host "MVP 1.0 HOST ACCEPTANCE PASS"
Write-Host "HEAD: $head"
Write-Host "Evidence: $EvidencePath"
Write-Host ("=" * 72)
exit 0
