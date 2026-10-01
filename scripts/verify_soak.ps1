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

$provision = Join-Path $Root "scripts\provision_scrcpy_server.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $provision
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

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
$maxWorkingSetMb = 0.0
$lastSummary = ""

while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $process.Refresh()
    try {
        $working = [Math]::Round($process.WorkingSet64 / 1MB, 1)
        if ($working -gt $maxWorkingSetMb) { $maxWorkingSetMb = $working }
    }
    catch {}

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
$evidenceText | Set-Content -LiteralPath $evidence -Encoding UTF8
$evidenceText | ForEach-Object { Write-Host $_ }

Write-Host "Peak bot working set: $maxWorkingSetMb MB"
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
