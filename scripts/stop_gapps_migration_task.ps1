$ErrorActionPreference = "SilentlyContinue"
$taskName = "TUGARIN BOTS - GApps Migration"
Stop-ScheduledTask -TaskName $taskName
Disable-ScheduledTask -TaskName $taskName | Out-Null

$targetSession = @(
    (& quser.exe TugarinBots 2>$null) -split "`r?`n" |
        Where-Object { $_ -match "TugarinBots" } |
        ForEach-Object {
            if ($_ -match "\s+(\d+)\s+(Active|Disc)\s+") { [int]$Matches[1] }
        }
    ) | Select-Object -First 1
Get-Process -IncludeUserName | Where-Object {
    $null -ne $targetSession -and $_.SessionId -eq $targetSession -and $_.ProcessName -in @("powershell", "powershell_ise", "cmd", "adb", "conhost")
} | Stop-Process -Force

@{
    stopped_at = (Get-Date).ToString("o")
    task = $taskName
    session = $targetSession
} | ConvertTo-Json | Set-Content -Encoding UTF8 "C:\warbot_wsa\gapps-migration-stopped.json"
