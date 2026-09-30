$ErrorActionPreference = "SilentlyContinue"
$taskName = "TUGARIN BOTS - GApps Migration"
Get-ScheduledTask | Where-Object { $_.TaskName -like "TUGARIN BOTS*" } | ForEach-Object {
    Stop-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath
    Disable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath | Out-Null
}

$targetProcesses = @(Get-Process -IncludeUserName | Where-Object {
    $_.UserName -ieq "$env:COMPUTERNAME\TugarinBots" -and $_.ProcessName -in @(
        "powershell", "powershell_ise", "cmd", "adb", "conhost", "python", "pythonw"
    )
})
$targetProcesses | Stop-Process -Force

@{
    stopped_at = (Get-Date).ToString("o")
    task = $taskName
    stopped_processes = @($targetProcesses | ForEach-Object { "$($_.ProcessName):$($_.Id)" })
} | ConvertTo-Json | Set-Content -Encoding UTF8 "C:\warbot_wsa\gapps-migration-stopped.json"
