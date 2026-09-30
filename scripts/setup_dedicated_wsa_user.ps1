#requires -version 5.1
param(
    [ValidateSet("Prepare", "Continue")]
    [string]$Stage = "Prepare",
    [string]$TargetUser = "Программист1",
    [string]$RepoRoot = "C:\warbot_git",
    [string]$Branch = "feature/unified-android-backend",
    [switch]$NoLogoffPrompt,
    [switch]$SkipDataBackup
)

$ErrorActionPreference = "Stop"

$WorkRoot = "C:\warbot_wsa"
$TaskName = "TUGARIN BOTS - Continue WSA Setup"
$PackageName = "MicrosoftCorporationII.WindowsSubsystemForAndroid"
$PackageFamily = "MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe"
$LogPath = Join-Path $WorkRoot "dedicated-user-setup.log"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-SetupLog {
    param([string]$Message)
    New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    Add-Content -Encoding UTF8 -Path $LogPath -Value $line
}

function Quote-Arg {
    param([string]$Value)
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Invoke-SelfElevated {
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Stage", $Stage,
        "-TargetUser", $TargetUser,
        "-RepoRoot", $RepoRoot,
        "-Branch", $Branch
    )
    if ($NoLogoffPrompt) { $args += "-NoLogoffPrompt" }
    if ($SkipDataBackup) { $args += "-SkipDataBackup" }
    $line = ($args | ForEach-Object { Quote-Arg ([string]$_) }) -join " "
    $proc = Start-Process powershell.exe -Verb RunAs -PassThru -Wait -ArgumentList $line
    if ($null -eq $proc) { return 98 }
    return [int]$proc.ExitCode
}

function Assert-Repository {
    if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
        throw "Repository not found: $RepoRoot"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot ".git") -PathType Container)) {
        throw "$RepoRoot is not a Git checkout."
    }
}

function Get-AdministratorsGroup {
    $group = Get-LocalGroup | Where-Object {
        $_.SID -and $_.SID.Value -eq "S-1-5-32-544"
    } | Select-Object -First 1
    if (-not $group) {
        throw "Could not resolve built-in Administrators group S-1-5-32-544."
    }
    return $group
}

function Ensure-DedicatedUser {
    $user = Get-LocalUser -Name $TargetUser -ErrorAction SilentlyContinue
    if (-not $user) {
        Write-Host ""
        Write-Host "Creating dedicated Windows account '$TargetUser'."
        Write-Host "The password is requested securely and is never written to disk."
        $password = Read-Host "Password for $TargetUser" -AsSecureString
        $params = @{
            Name = $TargetUser
            Password = $password
            FullName = "TUGARIN BOTS Runtime"
            Description = "TUGARIN BOTS WSA runtime"
            AccountNeverExpires = $true
            PasswordNeverExpires = $true
        }
        $user = New-LocalUser @params
        Write-SetupLog "Created local account $TargetUser SID=$($user.SID.Value)."
    }
    else {
        if (-not $user.Enabled) { Enable-LocalUser -Name $TargetUser }
        Write-SetupLog "Local account $TargetUser already exists SID=$($user.SID.Value)."
    }

    $admins = Get-AdministratorsGroup
    $members = @(Get-LocalGroupMember -Group $admins.Name -ErrorAction SilentlyContinue)
    $alreadyAdmin = [bool](@($members | Where-Object { $_.SID.Value -eq $user.SID.Value }).Count)
    if (-not $alreadyAdmin) {
        Add-LocalGroupMember -Group $admins.Name -Member $TargetUser -ErrorAction Stop
        Write-SetupLog "Added $TargetUser to local Administrators for same-identity WSA installation."
    }
    return (Get-LocalUser -Name $TargetUser -ErrorAction Stop)
}

function Grant-PathAccess {
    param(
        [Parameter(Mandatory = $true)]$User,
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$Recursive
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }

    $grant = "*$($User.SID.Value):(OI)(CI)M"
    Write-SetupLog "Granting $TargetUser access to $Path (recursive=$([bool]$Recursive))."

    if ($Recursive) {
        & icacls.exe $Path /grant $grant /T /C | Out-Null
    }
    else {
        & icacls.exe $Path /grant $grant /C | Out-Null
    }

    if ($LASTEXITCODE -ne 0) {
        throw "icacls failed for $Path."
    }
    Write-SetupLog "Access grant completed for $Path."
}

function Grant-BootstrapAccess {
    param([Parameter(Mandatory = $true)]$User)

    # The repository is small enough to update recursively. Do NOT recurse over
    # the extracted WSA tree: it contains thousands of files and made the first
    # bootstrap appear hung after the password prompt.
    Grant-PathAccess -User $User -Path $RepoRoot -Recursive
    Grant-PathAccess -User $User -Path $WorkRoot

    $downloads = Join-Path $WorkRoot "downloads"
    if (Test-Path -LiteralPath $downloads -PathType Container) {
        Grant-PathAccess -User $User -Path $downloads -Recursive
    }
}

function Stop-WsaProcesses {
    Write-SetupLog "Stopping WSA processes."
    Stop-Process -Name "WsaSettings","WsaClient","WindowsSubsystemForAndroid","WsaService","WSACrashUploader","vmmemWSA" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

function Backup-WsaProfileData {
    if ($SkipDataBackup) {
        Write-SetupLog "Profile-data backup skipped by parameter."
        return ""
    }

    $backupRoot = Join-Path $WorkRoot ("profile-backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $saved = 0

    foreach ($profile in @(Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue)) {
        if (-not $profile.LocalPath) { continue }
        if (-not (Test-Path -LiteralPath $profile.LocalPath -PathType Container)) { continue }

        $packageRoot = Join-Path $profile.LocalPath ("AppData\Local\Packages\" + $PackageFamily)
        if (-not (Test-Path -LiteralPath $packageRoot -PathType Container)) { continue }

        $sidSafe = ([string]$profile.SID) -replace '[^A-Za-z0-9_.-]', '_'
        $dest = Join-Path $backupRoot $sidSafe
        New-Item -ItemType Directory -Force -Path $dest | Out-Null

        $userdata = Join-Path $packageRoot "LocalCache\userdata.vhdx"
        if (Test-Path -LiteralPath $userdata -PathType Leaf) {
            Copy-Item -LiteralPath $userdata -Destination (Join-Path $dest "userdata.vhdx") -Force
            $saved++
        }

        $settings = Join-Path $packageRoot "Settings"
        if (Test-Path -LiteralPath $settings -PathType Container) {
            Copy-Item -LiteralPath $settings -Destination (Join-Path $dest "Settings") -Recurse -Force
            $saved++
        }
    }

    Write-SetupLog "WSA profile backup saved at $backupRoot; copied items=$saved."
    return $backupRoot
}

function Remove-WsaRegistrationInUserSession {
    param(
        [Parameter(Mandatory = $true)][string]$Sid,
        [Parameter(Mandatory = $true)][string]$PackageFullName
    )

    $sidObject = New-Object Security.Principal.SecurityIdentifier($Sid)
    $account = $sidObject.Translate([Security.Principal.NTAccount]).Value
    $taskName = "TUGARIN BOTS - Remove WSA $($Sid.Split('-')[-1])"
    $scriptPath = Join-Path $WorkRoot "remove-wsa-current-user.ps1"
    $resultPath = Join-Path $WorkRoot "remove-wsa-current-user-result.json"
    $escapedPackage = $PackageFullName.Replace("'", "''")
    $escapedResult = $resultPath.Replace("'", "''")
    $body = @"
`$ErrorActionPreference = 'Stop'
try {
    Remove-AppxPackage -Package '$escapedPackage' -ErrorAction Stop
    @{ pass = `$true; user = [Security.Principal.WindowsIdentity]::GetCurrent().Name; at = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath '$escapedResult'
    exit 0
}
catch {
    @{ pass = `$false; user = [Security.Principal.WindowsIdentity]::GetCurrent().Name; error = `$_.Exception.Message; at = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath '$escapedResult'
    exit 1
}
"@
    Set-Content -LiteralPath $scriptPath -Value $body -Encoding UTF8
    Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
    )
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddMinutes(2)
        do {
            Start-Sleep -Seconds 2
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        } while ($task.State -eq 'Running' -and (Get-Date) -lt $deadline)
        if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
            throw "Per-user WSA removal did not produce a result for $account."
        }
        $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
        if (-not $result.pass) {
            throw "Per-user WSA removal failed for ${account}: $($result.error)"
        }
        Write-SetupLog "Removed WSA registration in user session $account ($Sid)."
    }
    finally {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
}

function Remove-WsaRegistrations {
    $packages = @(Get-AppxPackage -AllUsers -Name $PackageName -ErrorAction SilentlyContinue)
    $inventory = Join-Path $WorkRoot "wsa-registration-before-clean.txt"
    $packages | Select-Object Name, PackageFullName, PackageFamilyName, Version, InstallLocation, IsDevelopmentMode, PackageUserInformation |
        Format-List | Out-String | Set-Content -Encoding UTF8 $inventory

    foreach ($pkg in $packages) {
        Write-SetupLog "Removing WSA registration for all users: $($pkg.PackageFullName)"
        try {
            Remove-AppxPackage -Package $pkg.PackageFullName -AllUsers -ErrorAction Stop
        }
        catch {
            Write-SetupLog "All-users removal failed; using same-SID removal for unpackaged registrations: $($_.Exception.Message)"
            foreach ($userInfo in @($pkg.PackageUserInformation)) {
                $text = [string]$userInfo
                if ($text -notmatch '^(S-1-[0-9-]+).*:\s*Installed') { continue }
                Remove-WsaRegistrationInUserSession -Sid $Matches[1] -PackageFullName $pkg.PackageFullName
            }
        }
    }

    try {
        $provisioned = @(Get-AppxProvisionedPackage -Online -ErrorAction Stop | Where-Object {
            $_.DisplayName -eq $PackageName
        })
        foreach ($pkg in $provisioned) {
            Write-SetupLog "Removing provisioned WSA package: $($pkg.PackageName)"
            Remove-AppxProvisionedPackage -Online -PackageName $pkg.PackageName -ErrorAction Stop | Out-Null
        }
    }
    catch {
        # WSABuilds is registered with Add-AppxPackage -Register and is not a
        # provisioned Windows image package. Some split-admin configurations
        # deny DISM inventory even after UAC; per-user AppX verification below
        # remains the authoritative cleanup gate.
        Write-SetupLog "Provisioned-package inventory unavailable (non-fatal for unpackaged WSA): $($_.Exception.Message)"
    }

    Start-Sleep -Seconds 2
    $remaining = @(Get-AppxPackage -AllUsers -Name $PackageName -ErrorAction SilentlyContinue)
    if ($remaining.Count -gt 0) {
        $remaining | Select-Object Name, PackageFullName, InstallLocation, PackageUserInformation |
            Format-List | Out-String |
            Set-Content -Encoding UTF8 (Join-Path $WorkRoot "wsa-registration-cleanup-failed.txt")
        throw "WSA is still registered for at least one Windows user. Clean handoff is blocked."
    }
    Write-SetupLog "Verified: no WSA package registration remains for any Windows user."
}

function Move-OldWsaFilesAside {
    $backupRoot = Join-Path $WorkRoot ("reinstall-backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    $moved = 0
    foreach ($rootName in @("WSA_LTS8_Windows10", "WSA_LTS8_Windows11")) {
        $root = Join-Path $WorkRoot $rootName
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
        $dest = Join-Path $backupRoot $rootName
        Write-SetupLog "Moving previous extracted WSA tree to $dest."
        Move-Item -LiteralPath $root -Destination $dest -Force
        $moved++
    }

    Remove-Item -LiteralPath "C:\warbot_wsa_runtime" -Recurse -Force -ErrorAction SilentlyContinue
    Write-SetupLog "Fresh WSA extraction prepared. Old trees moved=$moved; verified archive cache preserved."
    return $backupRoot
}

function Register-ContinuationTask {
    $account = "$env:COMPUTERNAME\$TargetUser"
    $argList = @(
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-Arg $PSCommandPath),
        "-Stage", "Continue",
        "-TargetUser", (Quote-Arg $TargetUser),
        "-RepoRoot", (Quote-Arg $RepoRoot),
        "-Branch", (Quote-Arg $Branch),
        "-NoLogoffPrompt"
    )
    $taskArgs = $argList -join " "
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArgs -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden
    $params = @{
        TaskName = $TaskName
        Action = $action
        Trigger = $trigger
        Principal = $principal
        Settings = $settings
        Force = $true
    }
    Register-ScheduledTask @params | Out-Null
    Write-SetupLog "Registered automatic continuation task for $account."
}

function Save-Handoff {
    param(
        [Parameter(Mandatory = $true)]$User,
        [string]$ProfileBackup,
        [string]$FilesBackup
    )
    $payload = [ordered]@{
        schema = 1
        state = "WAITING_FOR_TARGET_USER_LOGON"
        prepared_at = (Get-Date).ToString("o")
        target_user = $TargetUser
        target_sid = $User.SID.Value
        repository = $RepoRoot
        branch = $Branch
        profile_backup = $ProfileBackup
        extracted_files_backup = $FilesBackup
        continuation_task = $TaskName
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $WorkRoot "dedicated-user-handoff.json")
}

function Update-HandoffState {
    param([string]$State, [hashtable]$Extra = @{})
    $payload = [ordered]@{
        schema = 1
        state = $State
        updated_at = (Get-Date).ToString("o")
        target_user = $TargetUser
        repository = $RepoRoot
        branch = $Branch
    }
    foreach ($key in $Extra.Keys) { $payload[$key] = $Extra[$key] }
    $payload | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $WorkRoot "dedicated-user-handoff.json")
}

function Assert-RunningAsDedicatedUser {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $target = Get-LocalUser -Name $TargetUser -ErrorAction Stop
    if ($identity.User.Value -ne $target.SID.Value) {
        throw "Wrong Windows identity: $($identity.Name) SID=$($identity.User.Value); expected $TargetUser SID=$($target.SID.Value)."
    }
    Write-SetupLog "Verified dedicated runtime identity SID=$($identity.User.Value)."
}

function Invoke-GitWithOutput {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Operation
    )

    # PowerShell 7 can turn Git's normal remote-progress stderr (for example,
    # "From https://...") into a terminating NativeCommandError when
    # $ErrorActionPreference is Stop.  Keep native output visible, then rely on
    # Git's exit code as the authoritative success signal.
    $previousErrorActionPreference = $ErrorActionPreference
    $hadNativeErrorPreference = Test-Path Variable:PSNativeCommandUseErrorActionPreference
    if ($hadNativeErrorPreference) {
        $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    $ErrorActionPreference = "Continue"
    try {
        & git @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $exitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        if ($hadNativeErrorPreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
        }
    }

    if ($exitCode -ne 0) {
        throw "git $Operation failed with exit code $exitCode."
    }
}

function Update-Repository {
    $safePath = $RepoRoot -replace "\\", "/"
    & git config --global --add safe.directory $safePath | Out-Null

    Write-SetupLog "Updating repository branch $Branch."
    Invoke-GitWithOutput -Arguments @("-C", $RepoRoot, "fetch", "origin", $Branch) -Operation "fetch"

    $dirty = (& git -C $RepoRoot status --porcelain | Out-String).Trim()
    if ($dirty) {
        $stashName = "TUGARIN_BOTS_DEDICATED_USER_BACKUP_" + (Get-Date -Format "yyyyMMdd-HHmmss")
        Invoke-GitWithOutput -Arguments @("-C", $RepoRoot, "stash", "push", "-u", "-m", $stashName) -Operation "stash backup"
        Write-SetupLog "Preserved local repository changes in stash $stashName."
    }

    Invoke-GitWithOutput -Arguments @("-C", $RepoRoot, "checkout", $Branch) -Operation "checkout"
    Invoke-GitWithOutput -Arguments @("-C", $RepoRoot, "pull", "--ff-only", "origin", $Branch) -Operation "pull"

    $head = (& git -C $RepoRoot rev-parse HEAD | Out-String).Trim()
    Write-SetupLog "Repository HEAD=$head."
    return $head
}

function Ensure-PythonEnvironment {
    $venvRoot = Join-Path $WorkRoot "tugarin-venv"
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $systemPython = (Get-Command python.exe -ErrorAction Stop).Source
        Write-SetupLog "Creating dedicated Python venv at $venvRoot."
        & $systemPython -m venv $venvRoot 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed." }
    }

    Write-SetupLog "Installing TUGARIN BOTS Python requirements into dedicated venv."
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $RepoRoot "requirements.txt") 2>&1 |
        ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

    $androidHelpers = Join-Path $RepoRoot "requirements-android-optional.txt"
    if (Test-Path -LiteralPath $androidHelpers -PathType Leaf) {
        Write-SetupLog "Installing Android Unicode/system-UI helpers."
        & $venvPython -m pip install --disable-pip-version-check -r $androidHelpers 2>&1 |
            ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "Android helper dependency installation failed." }
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Dedicated venv Python disappeared after dependency installation: $venvPython"
    }
    return [string]$venvPython
}

function Ensure-PreviewCodec {
    $ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if (-not $ffmpeg) { $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue }
    if ($ffmpeg) {
        Write-SetupLog "FFmpeg preview decoder ready: $($ffmpeg.Source)"
        return $true
    }

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-SetupLog "WARNING: FFmpeg not found and winget is unavailable; GUI will use ADB screencap fallback."
        return $false
    }

    Write-SetupLog "Installing FFmpeg for low-latency H.264 preview (Gyan.FFmpeg)."
    & $winget.Source install --id Gyan.FFmpeg -e --silent --accept-package-agreements --accept-source-agreements 2>&1 |
        ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-SetupLog "WARNING: FFmpeg installation failed exit=$LASTEXITCODE; GUI will use ADB screencap fallback."
        return $false
    }

    Write-SetupLog "FFmpeg installation completed. A new process/session will discover its command alias."
    return $true
}

function Create-GuiShortcut {
    param([Parameter(Mandatory = $true)][string]$PythonExe)
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop "TUGARIN BOTS.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $pythonw = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
    $target = if (Test-Path -LiteralPath $pythonw -PathType Leaf) { $pythonw } else { $PythonExe }
    $shortcut.TargetPath = $target
    $shortcut.Arguments = '"' + (Join-Path $RepoRoot "gui.py") + '"'
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.Description = "TUGARIN BOTS"
    $shortcut.Save()
    Write-SetupLog "Created GUI shortcut $shortcutPath."
    return $shortcutPath
}

function Invoke-Prepare {
    Assert-Repository
    $user = Ensure-DedicatedUser
    Grant-BootstrapAccess -User $user

    Stop-WsaProcesses
    $profileBackup = Backup-WsaProfileData
    Remove-WsaRegistrations
    $filesBackup = Move-OldWsaFilesAside

    Grant-BootstrapAccess -User $user
    Register-ContinuationTask
    Save-Handoff -User $user -ProfileBackup $profileBackup -FilesBackup $filesBackup

    Write-Host ""
    Write-Host "=============================================================="
    Write-Host "TUGARIN BOTS clean WSA handoff is ready."
    Write-Host "Sign in to local Windows account: $TargetUser"
    Write-Host "After logon the setup will continue automatically:"
    Write-Host "fresh WSA extraction -> registration -> P0 -> WSA_GAME_PASS -> GUI."
    Write-Host "=============================================================="
    Write-Host ""

    if (-not $NoLogoffPrompt) {
        [void](Read-Host "Press ENTER to sign out now. Use Ctrl+C if you want to stay logged in.")
        Write-SetupLog "Signing out for dedicated-user handoff."
        shutdown.exe /l
    }
}

function Invoke-Continue {
    Assert-Repository
    Assert-RunningAsDedicatedUser

    if (-not (Test-IsAdmin)) {
        Write-SetupLog "Continuation needs elevation under the SAME dedicated SID."
        $code = Invoke-SelfElevated
        exit $code
    }

    [string]$head = Update-Repository
    [string]$venvPython = Ensure-PythonEnvironment
    [void](Ensure-PreviewCodec)
    $scrcpyProvisioner = Join-Path $RepoRoot "scripts\provision_scrcpy_server.ps1"
    if (-not (Test-Path -LiteralPath $scrcpyProvisioner -PathType Leaf)) {
        throw "scrcpy provisioner is missing: $scrcpyProvisioner"
    }
    Write-SetupLog "Provisioning pinned scrcpy Android video server."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scrcpyProvisioner 2>&1 |
        ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "scrcpy-server provisioning failed exit=$LASTEXITCODE"
    }

    $venvScripts = Split-Path -Parent $venvPython
    $env:PATH = "$venvScripts;$env:PATH"
    $env:PYTHONIOENCODING = "utf-8"

    Update-HandoffState -State "P0_RUNNING" -Extra @{
        commit = $head
        current_sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    }

    Write-SetupLog "Starting authoritative WSA P0 under dedicated user."
    Set-Location $RepoRoot
    $p0Args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $RepoRoot "scripts\run_and_report.ps1"),
        "-Backend", "wsa",
        "-SkipPull"
    )
    & powershell.exe @p0Args
    $p0Exit = [int]$LASTEXITCODE

    if ($p0Exit -ne 0) {
        Update-HandoffState -State "P0_FAILED" -Extra @{
            commit = $head
            p0_exit_code = $p0Exit
        }
        Write-SetupLog "P0 failed exit=$p0Exit. GUI is intentionally not launched. Continuation task remains for retry."
        exit $p0Exit
    }

    $shortcutPath = Create-GuiShortcut -PythonExe $venvPython
    Update-HandoffState -State "WSA_GAME_PASS" -Extra @{
        commit = $head
        p0_exit_code = 0
        gui_shortcut = $shortcutPath
    }

    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-SetupLog "Removed continuation task after successful P0."
    }
    catch {
        Write-SetupLog "WARNING: could not remove continuation task: $($_.Exception.Message)"
    }

    Write-SetupLog "P0 passed. GUI shortcut is ready; automatic GUI launch is intentionally disabled."

    Write-Host ""
    Write-Host "TUGARIN BOTS setup completed."
    Write-Host "P0 state: WSA_GAME_PASS"
    Write-Host "GUI was not auto-launched; use the desktop shortcut when needed."
}

New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null

try {
    Write-SetupLog "Dedicated-user setup start stage=$Stage target=$TargetUser current=$([Security.Principal.WindowsIdentity]::GetCurrent().Name)."

    if (-not (Test-IsAdmin)) {
        if ($Stage -eq "Continue") {
            $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
            $target = Get-LocalUser -Name $TargetUser -ErrorAction Stop
            if ($identity.User.Value -eq $target.SID.Value) {
                Update-HandoffState -State "WAITING_FOR_TARGET_USER_TOKEN_REFRESH" -Extra @{
                    stage = $Stage
                    current_user = $identity.Name
                    current_sid = $identity.User.Value
                    reason = "Sign out and sign back in so the new local Administrators membership is present in the interactive token."
                }
                Write-SetupLog "Continue is waiting for a fresh $TargetUser logon token; refusing elevation through a different administrator SID."
                exit 92
            }
        }
        Write-SetupLog "Requesting administrator elevation for stage $Stage."
        $code = Invoke-SelfElevated
        exit $code
    }

    if ($Stage -eq "Prepare") {
        Invoke-Prepare
    }
    else {
        Invoke-Continue
    }
}
catch {
    $detail = ($_ | Out-String)
    try {
        $detail | Set-Content -Encoding UTF8 (Join-Path $WorkRoot "dedicated-user-setup-error.txt")
        Update-HandoffState -State "FAILED" -Extra @{
            stage = $Stage
            error = $_.Exception.Message
        }
        Write-SetupLog "FAILED stage=$Stage error=$($_.Exception.Message)"
    }
    catch {}
    Write-Host $detail
    exit 99
}
