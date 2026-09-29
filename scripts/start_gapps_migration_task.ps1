param(
    [string]$TargetUser = "TugarinBots",
    [string]$RepoRoot = "C:\warbot_git"
)

$ErrorActionPreference = "Stop"
$taskName = "TUGARIN BOTS - GApps Migration"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This task bootstrap must run elevated."
}

$targetSid = (Get-LocalUser -Name $TargetUser -ErrorAction Stop).Sid.Value
$sessionText = (& quser.exe $TargetUser 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $sessionText -notmatch [regex]::Escape($TargetUser)) {
    throw "Target user $TargetUser has no Windows session; refusing cross-SID migration."
}

$reporter = Join-Path $RepoRoot "scripts\run_and_report.ps1"
if (-not (Test-Path -LiteralPath $reporter -PathType Leaf)) {
    throw "Runtime reporter not found: $reporter"
}

$arguments = @(
    "-NoProfile",
    "-NonInteractive",
    "-WindowStyle", "Hidden",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $reporter + '"'),
    "-Backend", "wsa",
    "-WsaFlavor", "GApps",
    "-ReplaceExistingWsa",
    "-AllowMagisk",
    "-SkipPull"
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $RepoRoot
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$TargetUser" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
$task = New-ScheduledTask -Action $action -Principal $taskPrincipal -Settings $settings
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

@{
    task = $taskName
    target_user = $TargetUser
    target_sid = $targetSid
    started_at = (Get-Date).ToString("o")
    repository = $RepoRoot
} | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 "C:\warbot_wsa\gapps-migration-task.json"

Write-Host "Started $taskName under $env:COMPUTERNAME\$TargetUser ($targetSid)."
