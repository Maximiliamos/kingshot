#requires -version 5.1
param(
    [string]$TargetUser = "Программист1",
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
$processNames = @("python", "pythonw", "cmd", "powershell", "pwsh", "conhost", "adb", "ffmpeg")
$processes = @()

try {
    $processes = @(
        Get-Process -IncludeUserName -ErrorAction SilentlyContinue |
            Where-Object {
                $_.UserName -ieq $account -and
                $_.ProcessName.ToLowerInvariant() -in $processNames
            } |
            ForEach-Object {
                $commandLine = ""
                try {
                    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction Stop
                    $commandLine = [string]$cim.CommandLine
                }
                catch {}
                $parentCommandLine = ""
                if ($cim -and $cim.ParentProcessId) {
                    try { $parentCommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$($cim.ParentProcessId)" -ErrorAction Stop).CommandLine } catch {}
                }
                [ordered]@{
                    name = $_.ProcessName
                    pid = $_.Id
                    user = $_.UserName
                    session_id = $_.SessionId
                    command_line = $commandLine
                    parent_process_id = if ($cim) { [int]$cim.ParentProcessId } else { 0 }
                    parent_command_line = $parentCommandLine
                }
            }
    )
}
catch {
    # IncludeUserName requires an elevated token even when auditing the current
    # interactive account. Fall back to the read-only CIM owner method so the
    # normal medium-integrity production session can still fail closed on its
    # own stale processes.
    $processes = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -and ([IO.Path]::GetFileNameWithoutExtension($_.Name).ToLowerInvariant() -in $processNames) } |
            ForEach-Object {
                $cim = $_
                $owner = $null
                try { $owner = Invoke-CimMethod -InputObject $cim -MethodName GetOwner -ErrorAction Stop } catch {}
                if ($owner -and $owner.User) {
                    $ownerAccount = if ($owner.Domain) { "$($owner.Domain)\$($owner.User)" } else { [string]$owner.User }
                    if ($ownerAccount -ieq $account) {
                        [ordered]@{
                            name = [IO.Path]::GetFileNameWithoutExtension($cim.Name)
                            pid = [int]$cim.ProcessId
                            user = $ownerAccount
                            session_id = [int]$cim.SessionId
                            command_line = [string]$cim.CommandLine
                            parent_process_id = [int]$cim.ParentProcessId
                            parent_command_line = try { [string](Get-CimInstance Win32_Process -Filter "ProcessId=$($cim.ParentProcessId)" -ErrorAction Stop).CommandLine } catch { "" }
                        }
                    }
                }
            }
    )
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

# ADB server and the PowerShell process executing this audit may legitimately
# exist. Fail on GUI/Python/console/FFmpeg leftovers and specifically on an ADB
# shell that still hosts our scrcpy Android server.
$stale = @(
    $processes | Where-Object {
        $name = $_.name.ToLowerInvariant()
        $line = ([string]$_.command_line) + " " + ([string]$_.parent_command_line)
        $projectOwned = $line -match "(?i)warbot_git|warbot_wsa\\.*venv|tugarin bots|gui\.py|bot\.py|warbot_cli\.py|run_gui|scrcpy-server"
        if ($name -in @("python", "pythonw", "cmd", "conhost", "ffmpeg")) {
            return $projectOwned
        }
        if ($name -in @("powershell", "pwsh")) {
            return ($_.pid -ne $PID -and $projectOwned)
        }
        if ($name -eq "adb") {
            return (
                $line -match "tugarin-scrcpy-server" -or
                $line -match "com\.genymobile\.scrcpy\.Server"
            )
        }
        return $false
    }
)

$staleTasks = @(
    $tasks | Where-Object {
        $name = [string]$_.task_name
        $state = [string]$_.state
        $enabled = [bool]$_.enabled
        $legacySetupTask = $name -match "(?i)GApps Migration|WSA.*Continue|Setup.*Continue|Migration"
        return ($state -eq "Running") -or ($enabled -and $legacySetupTask)
    }
)

$pass = $AllowGuiProcesses -or (($stale.Count -eq 0) -and ($staleTasks.Count -eq 0))
$result = [ordered]@{
    schema = 1
    checked_at = (Get-Date).ToString("o")
    target_user = $account
    allow_gui_processes = [bool]$AllowGuiProcesses
    pass = [bool]$pass
    processes = $processes
    stale_processes = $stale
    scheduled_tasks = $tasks
    stale_scheduled_tasks = $staleTasks
}

$parent = Split-Path -Parent $Output
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $Output
$result | ConvertTo-Json -Depth 8 | Write-Host

if (-not $pass) {
    Write-Error "Production-user stale project processes or active migration/continuation tasks remain after GUI/setup should be closed."
    exit 21
}
exit 0
