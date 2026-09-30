#requires -version 5.1
param(
    [string]$TargetUser = "TugarinBots",
    [string]$Output = "",
    [switch]$AllowGuiProcesses
)

$ErrorActionPreference = "Stop"

if (-not $Output) {
    $root = Split-Path -Parent $PSScriptRoot
    $debug = Join-Path $root "debug"
    New-Item -ItemType Directory -Force -Path $debug | Out-Null
    $Output = Join-Path $debug "runtime-process-audit.json"
}

$account = "$env:COMPUTERNAME\$TargetUser"
$processNames = @("python", "pythonw", "cmd", "powershell", "pwsh", "conhost", "adb")
$processes = @()

try {
    $processes = @(
        Get-Process -IncludeUserName -ErrorAction SilentlyContinue |
            Where-Object {
                $_.UserName -ieq $account -and
                $_.ProcessName.ToLowerInvariant() -in $processNames
            } |
            ForEach-Object {
                [ordered]@{
                    name = $_.ProcessName
                    pid = $_.Id
                    user = $_.UserName
                    session_id = $_.SessionId
                }
            }
    )
}
catch {
    throw "Could not inspect dedicated-user processes: $($_.Exception.Message)"
}

$tasks = @()
try {
    $tasks = @(
        Get-ScheduledTask -ErrorAction SilentlyContinue |
            Where-Object { $_.TaskName -like "TUGARIN BOTS*" } |
            ForEach-Object {
                [ordered]@{
                    task_name = $_.TaskName
                    task_path = $_.TaskPath
                    state = [string]$_.State
                    enabled = [bool]$_.Settings.Enabled
                }
            }
    )
}
catch {}

# adb.exe may intentionally keep one background server process after a diagnostic
# command. Record it, but do not classify it as a stale GUI/console process.
$stale = @(
    $processes | Where-Object {
        $_.name.ToLowerInvariant() -in @(
            "python", "pythonw", "cmd", "powershell", "pwsh", "conhost"
        )
    }
)

$pass = $AllowGuiProcesses -or ($stale.Count -eq 0)
$result = [ordered]@{
    schema = 1
    checked_at = (Get-Date).ToString("o")
    target_user = $account
    allow_gui_processes = [bool]$AllowGuiProcesses
    pass = [bool]$pass
    processes = $processes
    stale_processes = $stale
    scheduled_tasks = $tasks
}

$parent = Split-Path -Parent $Output
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $Output
$result | ConvertTo-Json -Depth 8 | Write-Host

if (-not $pass) {
    Write-Error "Dedicated-user stale Python/console processes remain after GUI/setup should be closed."
    exit 21
}
exit 0
