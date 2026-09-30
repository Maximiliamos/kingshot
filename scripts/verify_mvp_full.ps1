param(
    [switch]$SkipInfrastructure,
    [int]$FlowTimeoutMinutes = 45,
    [int]$SoakTimeoutMinutes = 90,
    [int]$SoakCharacters = 2
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

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
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script @Arguments
    $code = [int]$LASTEXITCODE
    if ($code -ne 0) {
        Write-Host ""
        Write-Host "MVP 1.0 HOST ACCEPTANCE FAIL at: $Name (exit=$code)"
        exit $code
    }
}

Write-Host "=== TUGARIN BOTS MVP 1.0 FULL HOST ACCEPTANCE ==="
Write-Host "Repository: $Root"
$head = (& git -C $Root rev-parse HEAD | Out-String).Trim()
Write-Host "HEAD: $head"
Write-Host ""
Write-Host "IMPORTANT: game-flow and soak gates intentionally clear Kingshot app data."
Write-Host "The PC-side Tugarin nickname counter is preserved."

if (-not $SkipInfrastructure) {
    Run-Gate -Name "Infrastructure / WSA / Kingshot / process audit" -Script (Join-Path $Root "scripts\verify_release.ps1")
}

Run-Gate -Name "Low-latency scrcpy H.264 preview" -Script (Join-Path $Root "scripts\verify_preview.ps1")
Run-Gate -Name "Bounded game + ADB recovery" -Script (Join-Path $Root "scripts\verify_recovery.ps1")
Run-Gate -Name "Exact State #3 -> tutorial -> Tugarin<N>" -Script (Join-Path $Root "scripts\verify_game_flow.ps1") -Arguments @("-TimeoutMinutes", [string]$FlowTimeoutMinutes)
Run-Gate -Name "Multi-cycle soak" -Script (Join-Path $Root "scripts\verify_soak.ps1") -Arguments @(
    "-TimeoutMinutes", [string]$SoakTimeoutMinutes,
    "-MinCharacters", [string][Math]::Max(2, $SoakCharacters)
)

Write-Host ""
Write-Host ("=" * 72)
Write-Host "MVP 1.0 HOST ACCEPTANCE PASS"
Write-Host "HEAD: $head"
Write-Host ("=" * 72)
exit 0
