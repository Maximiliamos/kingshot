param(
    [int]$TimeoutMinutes = 45,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

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
    Write-Host "Close the running TUGARIN BOTS GUI/bot before the deterministic flow gate."
    $existing | Select-Object ProcessId, Name, CommandLine | Format-Table -AutoSize
    exit 31
}

$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "debug") | Out-Null
$stdout = Join-Path $Root "debug\mvp-game-flow.stdout.log"
$stderr = Join-Path $Root "debug\mvp-game-flow.stderr.log"
$evidence = Join-Path $Root "debug\mvp-game-flow-evidence.json"
Remove-Item $stdout, $stderr, $evidence -Force -ErrorAction SilentlyContinue

Write-Host "=== TUGARIN BOTS FULL GAME FLOW ACCEPTANCE ==="
Write-Host "WARNING: this gate intentionally clears Kingshot app data."
Write-Host "It preserves the PC-side Tugarin nickname counter."
Write-Host ""

& $PythonExe .\warbot_cli.py restriction-check --backend wsa --serial 127.0.0.1:58526 `
    --output .\debug\account-restriction-preflight.png
if ($LASTEXITCODE -ne 0) {
    Write-Host "MVP GAME FLOW BLOCKED: account/server restriction detected before pm clear."
    exit 44
}

& $PythonExe .\warbot_cli.py prepare-mvp-flow --backend wsa --serial 127.0.0.1:58526 --yes
if ($LASTEXITCODE -ne 0) {
    Write-Host "MVP GAME FLOW PREP FAIL"
    exit $LASTEXITCODE
}

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
$lastPhase = ""
$lastStep = ""

while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $process.Refresh()
    $statePath = Join-Path $Root "state.json"
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            $phase = [string]$state.phase
            $step = [string]$state.step
            if ($phase -ne $lastPhase -or $step -ne $lastStep) {
                Write-Host ("FLOW {0:HH:mm:ss} phase={1} step={2}" -f (Get-Date), $phase, $step)
                $lastPhase = $phase
                $lastStep = $step
            }
        }
        catch {
            Write-Host "State read warning: $($_.Exception.Message)"
        }
    }
}

if (-not $process.HasExited) {
    Write-Host "Flow timeout after $TimeoutMinutes minute(s); stopping bot."
    try {
        @{ paused = $false; stop = $true } |
            ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $Root "control.json") -Encoding UTF8
        Start-Sleep -Seconds 3
        $process.Refresh()
    }
    catch {}
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "MVP GAME FLOW FAIL: timeout"
    if (Test-Path $stdout) { Get-Content $stdout -Tail 80 }
    if (Test-Path $stderr) { Get-Content $stderr -Tail 80 }
    exit 32
}

Write-Host ""
Write-Host "Bot exit code: $($process.ExitCode)"
if (Test-Path $stdout) {
    Write-Host "--- bot stdout tail ---"
    Get-Content $stdout -Tail 80
}
if (Test-Path $stderr) {
    $errLines = Get-Content $stderr -Tail 80
    if ($errLines) {
        Write-Host "--- bot stderr tail ---"
        $errLines | ForEach-Object { Write-Host $_ }
    }
}

$process.Refresh()
if ([int]$process.ExitCode -ne 0) {
    Write-Host "MVP GAME FLOW FAIL: bot exited with code $($process.ExitCode)."
    exit 33
}

$evidenceText = & $PythonExe .\warbot_cli.py flow-evidence --backend wsa --serial 127.0.0.1:58526
$evidenceCode = [int]$LASTEXITCODE
$evidenceText | Set-Content -LiteralPath $evidence -Encoding UTF8
$evidenceText | ForEach-Object { Write-Host $_ }

if ($evidenceCode -ne 0) {
    Write-Host ""
    Write-Host "MVP GAME FLOW FAIL"
    Write-Host "Evidence: $evidence"
    exit $evidenceCode
}

Write-Host ""
Write-Host "MVP GAME FLOW PASS"
Write-Host "Evidence: $evidence"
exit 0
