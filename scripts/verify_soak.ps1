param(
    [int]$MinCharacters = 2,
    [int]$TimeoutMinutes = 90,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$MinCharacters = [Math]::Max(2, $MinCharacters)

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

$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and
        $_.CommandLine -and
        ($_.CommandLine -match 'bot\.py' -or $_.CommandLine -match 'gui\.py')
    }
if ($existing) {
    Write-Host "Close the running TUGARIN BOTS GUI/bot before soak acceptance."
    $existing | Select-Object ProcessId, Name, CommandLine | Format-Table -AutoSize
    exit 41
}

$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "debug") | Out-Null
$stdout = Join-Path $Root "debug\mvp-soak.stdout.log"
$stderr = Join-Path $Root "debug\mvp-soak.stderr.log"
$evidence = Join-Path $Root "debug\mvp-soak-evidence.json"
Remove-Item $stdout, $stderr, $evidence -Force -ErrorAction SilentlyContinue

Write-Host "=== TUGARIN BOTS MULTI-CYCLE SOAK ACCEPTANCE ==="
Write-Host "WARNING: this gate intentionally clears Kingshot app data between cycles."
Write-Host "Target committed characters: $MinCharacters"
Write-Host ""

& $PythonExe .\warbot_cli.py restriction-check --backend wsa --serial 127.0.0.1:58526 `
    --output .\debug\account-restriction-preflight.png
if ($LASTEXITCODE -ne 0) {
    Write-Host "MVP SOAK BLOCKED: account/server restriction detected before pm clear."
    exit 44
}

$prepText = (& $PythonExe .\warbot_cli.py prepare-mvp-soak --backend wsa --serial 127.0.0.1:58526 --yes | Out-String)
if ($LASTEXITCODE -ne 0) {
    Write-Host $prepText
    Write-Host "MVP SOAK PREP FAIL"
    exit $LASTEXITCODE
}
$prepText | Write-Host
$prep = $prepText | ConvertFrom-Json
$targetCharacters = [int]$prep.characters_before + $MinCharacters

$startArgs = @{
    FilePath = $PythonExe
    ArgumentList = @(".\bot.py")
    WorkingDirectory = $Root
    RedirectStandardOutput = $stdout
    RedirectStandardError = $stderr
    WindowStyle = "Hidden"
    PassThru = $true
}
$process = Start-Process @startArgs

$deadline = (Get-Date).AddMinutes([Math]::Max(1, $TimeoutMinutes))
$targetReached = $false
$initialWorkingSetMb = $null
$lastWorkingSetMb = 0.0
$maxWorkingSetMb = 0.0
$heartbeatPath = Join-Path $Root "debug\runtime-heartbeat.json"
$heartbeatSeen = $false
$maxHeartbeatAgeSeconds = 0.0
$staleHeartbeatDetected = $false
$lastSummary = ""

while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $process.Refresh()
    try {
        $working = [Math]::Round($process.WorkingSet64 / 1MB, 1)
        if ($null -eq $initialWorkingSetMb) { $initialWorkingSetMb = $working }
        $lastWorkingSetMb = $working
        if ($working -gt $maxWorkingSetMb) { $maxWorkingSetMb = $working }
    }
    catch {}

    if (Test-Path -LiteralPath $heartbeatPath -PathType Leaf) {
        try {
            $heartbeatSeen = $true
            $heartbeatAge = [Math]::Max(
                0.0,
                ((Get-Date) - (Get-Item -LiteralPath $heartbeatPath).LastWriteTime).TotalSeconds
            )
            if ($heartbeatAge -gt $maxHeartbeatAgeSeconds) {
                $maxHeartbeatAgeSeconds = [Math]::Round($heartbeatAge, 3)
            }
            if ($heartbeatAge -gt 30.0) {
                $staleHeartbeatDetected = $true
                Write-Host ("SOAK heartbeat stale: {0:N1}s" -f $heartbeatAge)
                break
            }
        }
        catch {
            Write-Host "Heartbeat read warning: $($_.Exception.Message)"
        }
    }

    $statePath = Join-Path $Root "state.json"
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { continue }

    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $summary = "phase=$($state.phase) step=$($state.step) characters=$($state.characters_created) cycle=$($state.current_cycle)"
        if ($summary -ne $lastSummary) {
            Write-Host ("SOAK {0:HH:mm:ss} {1}" -f (Get-Date), $summary)
            $lastSummary = $summary
        }
        if ($state.last_stop_reason) {
            Write-Host "Bot stop reason: $($state.last_stop_reason)"
            break
        }
        if ([int]$state.characters_created -ge $targetCharacters) {
            $targetReached = $true
            break
        }
    }
    catch {
        Write-Host "State read warning: $($_.Exception.Message)"
    }
}

if ($targetReached -and -not $process.HasExited) {
    Write-Host "Soak target reached; requesting graceful stop."
    @{ paused = $false; stop = $true } |
        ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $Root "control.json") -Encoding UTF8
    $stopDeadline = (Get-Date).AddSeconds(12)
    while (-not $process.HasExited -and (Get-Date) -lt $stopDeadline) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
    }
}

$forcedTermination = $false
if (-not $process.HasExited) {
    $forcedTermination = $true
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    try { $process.WaitForExit(5000) } catch {}
}

if (-not $targetReached) {
    Write-Host "MVP SOAK FAIL: target not reached before process exit/timeout."
    Write-Host "Peak bot working set: $maxWorkingSetMb MB"
    if (Test-Path $stdout) { Get-Content $stdout -Tail 100 }
    if (Test-Path $stderr) { Get-Content $stderr -Tail 100 }
    exit 42
}

$process.Refresh()
if ($forcedTermination -or [int]$process.ExitCode -ne 0) {
    Write-Host "MVP SOAK FAIL: bot did not stop cleanly (forced=$forcedTermination exit=$($process.ExitCode))."
    if (Test-Path $stdout) { Get-Content $stdout -Tail 100 }
    if (Test-Path $stderr) { Get-Content $stderr -Tail 100 }
    exit 43
}

$evidenceText = & $PythonExe .\warbot_cli.py soak-evidence --backend wsa --serial 127.0.0.1:58526 --min-characters $MinCharacters
$evidenceCode = [int]$LASTEXITCODE

if ($evidenceCode -eq 0) {
    try {
        $evidenceObject = ($evidenceText | Out-String) | ConvertFrom-Json
        $resources = [ordered]@{
            initial_bot_working_set_mb = $initialWorkingSetMb
            final_observed_bot_working_set_mb = $lastWorkingSetMb
            peak_bot_working_set_mb = $maxWorkingSetMb
            heartbeat_seen = $heartbeatSeen
            max_heartbeat_age_seconds = [Math]::Round($maxHeartbeatAgeSeconds, 3)
            stale_heartbeat_detected = $staleHeartbeatDetected
            forced_termination = $forcedTermination
            process_exit_code = [int]$process.ExitCode
        }
        $evidenceObject | Add-Member -NotePropertyName resource_metrics -NotePropertyValue $resources -Force
        $resourcePass = [bool]($heartbeatSeen -and -not $staleHeartbeatDetected -and -not $forcedTermination)
        $evidenceObject.pass = [bool]($evidenceObject.pass -and $resourcePass)
        $evidenceObject | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $evidence -Encoding UTF8
        if (-not [bool]$evidenceObject.pass) { $evidenceCode = 45 }
    }
    catch {
        Write-Host "MVP SOAK FAIL: could not augment resource evidence: $($_.Exception.Message)"
        $evidenceCode = 46
        $evidenceText | Set-Content -LiteralPath $evidence -Encoding UTF8
    }
}
else {
    $evidenceText | Set-Content -LiteralPath $evidence -Encoding UTF8
}

if (Test-Path -LiteralPath $evidence) {
    Get-Content -LiteralPath $evidence | ForEach-Object { Write-Host $_ }
}

Write-Host "Peak bot working set: $maxWorkingSetMb MB"
Write-Host "Heartbeat seen: $heartbeatSeen; max age: $([Math]::Round($maxHeartbeatAgeSeconds, 3))s"
if ($evidenceCode -ne 0) {
    Write-Host ""
    Write-Host "MVP SOAK FAIL"
    Write-Host "Evidence: $evidence"
    exit $evidenceCode
}

Write-Host ""
Write-Host "MVP SOAK PASS"
Write-Host "Evidence: $evidence"
exit 0
