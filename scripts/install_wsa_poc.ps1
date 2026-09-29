param(
    [string]$Serial = "127.0.0.1:58526",
    [string]$PairEndpoint = "",
    [string]$PairCode = "",
    [switch]$CleanGame,
    [switch]$SkipInstall,
    [switch]$NoAutoDeveloperModePatch,
    [switch]$PrepareOnly,
    [switch]$RuntimeOnly,
    [ValidateSet("NoGApps", "GApps")]
    [string]$WsaFlavor = "NoGApps",
    [switch]$ReplaceExistingWsa,
    [switch]$AllowMagisk
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$WorkRoot = "C:\warbot_wsa"
$DownloadRoot = Join-Path $WorkRoot "downloads"
$RuntimeRoot = "C:\warbot_wsa_runtime"

# Selected after reading the real Windows build. WSABuilds ships separate
# patched LTS 8 packages for Windows 10 and Windows 11.
$ReleaseTag = ""
$ArchiveName = ""
$ArchiveUrl = ""
$ArchiveSha256 = ""
$InstallRoot = ""
$ArchivePath = ""

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}


function Ensure-PackagedActivationType {
    if ("TugarinBots.PackagedAppActivator" -as [type]) { return }

    $source = @"
using System;
using System.Runtime.InteropServices;

namespace TugarinBots
{
    [ComImport]
    [Guid("2E941141-7F97-4756-BA1D-9DECDE894A3D")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IApplicationActivationManager
    {
        [PreserveSig]
        int ActivateApplication(
            [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
            [MarshalAs(UnmanagedType.LPWStr)] string arguments,
            uint options,
            out uint processId);
    }

    [ComImport]
    [Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")]
    public class ApplicationActivationManager
    {
    }

    public static class PackagedAppActivator
    {
        public static uint Activate(string appUserModelId, string arguments)
        {
            var manager = (IApplicationActivationManager)new ApplicationActivationManager();
            try
            {
                uint processId;
                int hr = manager.ActivateApplication(appUserModelId, arguments, 0, out processId);
                if (hr < 0)
                {
                    Marshal.ThrowExceptionForHR(hr);
                }
                return processId;
            }
            finally
            {
                if (Marshal.IsComObject(manager))
                {
                    Marshal.ReleaseComObject(manager);
                }
            }
        }
    }
}
"@

    Add-Type -TypeDefinition $source -Language CSharp -ErrorAction Stop
}

function Invoke-PackagedApplication {
    param(
        [Parameter(Mandatory = $true)][string]$Aumid,
        [string]$Arguments = ""
    )

    Ensure-PackagedActivationType
    $processId = [TugarinBots.PackagedAppActivator]::Activate($Aumid, $Arguments)
    return [uint32]$processId
}

function Get-WsaApplicationCatalog {
    param([Parameter(Mandatory = $true)]$Package)

    $manifest = Get-AppxPackageManifest -Package $Package.PackageFullName -ErrorAction Stop
    $apps = @()
    foreach ($app in @($manifest.Package.Applications.Application)) {
        if ($null -eq $app) { continue }
        $id = [string]$app.Id
        if (-not $id) { continue }
        $apps += [pscustomobject]@{
            id = $id
            executable = [string]$app.Executable
            entry_point = [string]$app.EntryPoint
            aumid = ($Package.PackageFamilyName + "!" + $id)
        }
    }
    return $apps
}

function Save-WsaUserContextDiagnostics {
    param([Parameter(Mandatory = $true)][string]$ReportStage)

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $packageUsers = @()
    try {
        foreach ($pkg in @(Get-AppxPackage -AllUsers -Name "MicrosoftCorporationII.WindowsSubsystemForAndroid" -ErrorAction SilentlyContinue)) {
            foreach ($userInfo in @($pkg.PackageUserInformation)) {
                $packageUsers += [ordered]@{
                    package_full_name = [string]$pkg.PackageFullName
                    install_location = [string]$pkg.InstallLocation
                    user_security_id = [string]$userInfo.UserSecurityId
                    install_state = [string]$userInfo.InstallState
                }
            }
        }
    }
    catch {}

    $explorerOwners = @()
    try {
        foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -ErrorAction SilentlyContinue)) {
            $owner = Invoke-CimMethod -InputObject $proc -MethodName GetOwner -ErrorAction SilentlyContinue
            if ($owner -and $owner.User) {
                $account = if ($owner.Domain) { "$($owner.Domain)\$($owner.User)" } else { [string]$owner.User }
                $ownerSid = ""
                try {
                    $ownerSid = (New-Object System.Security.Principal.NTAccount($account)).Translate([System.Security.Principal.SecurityIdentifier]).Value
                }
                catch {}
                $explorerOwners += [ordered]@{
                    process_id = [int]$proc.ProcessId
                    session_id = [int]$proc.SessionId
                    account = $account
                    sid = $ownerSid
                }
            }
        }
    }
    catch {}

    [ordered]@{
        current_account = $identity.Name
        current_sid = $identity.User.Value
        current_session_id = (Get-Process -Id $PID).SessionId
        is_admin = (Test-IsAdmin)
        explorer_owners = $explorerOwners
        package_users = $packageUsers
    } | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-user-context.json")
}

function Invoke-SelfElevated {
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Serial", $Serial
    )
    if ($PairEndpoint) { $args += @("-PairEndpoint", $PairEndpoint) }
    if ($PairCode) { $args += @("-PairCode", $PairCode) }
    if ($CleanGame) { $args += "-CleanGame" }
    if ($SkipInstall) { $args += "-SkipInstall" }
    if ($NoAutoDeveloperModePatch) { $args += "-NoAutoDeveloperModePatch" }
    if ($PrepareOnly) { $args += "-PrepareOnly" }
    if ($RuntimeOnly) { $args += "-RuntimeOnly" }

    $quoted = $args | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }
    $proc = Start-Process powershell.exe -Verb RunAs -PassThru -Wait -ArgumentList ($quoted -join " ")
    if ($null -eq $proc) {
        return 98
    }
    return $proc.ExitCode
}

$DeveloperSettingsSourceCommit = "2e04da1be0765a8a248ab7006ed5f7eeeed15b76"
$DeveloperSettingsBlobSha1 = "019f772c0e46e7eed9aaa0a26ea35bf6ef32093e"
$DeveloperSettingsUrl = "https://raw.githubusercontent.com/WSA-Installer/wsa-installer/$DeveloperSettingsSourceCommit/assets/settings.dat"

function Get-GitBlobSha1 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $result = (& git hash-object -- $Path 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $result -notmatch "^[0-9a-fA-F]{40}$") {
        throw "Could not calculate Git blob SHA-1 for $Path"
    }
    return $result.ToLowerInvariant()
}

function Enable-DeveloperModeFallback {
    param(
        [Parameter(Mandatory = $true)][string]$ReportStage
    )

    $settingsPath = Join-Path $env:LOCALAPPDATA "Packages\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe\Settings\settings.dat"
    if (-not (Test-Path $settingsPath)) {
        Write-Log "Developer-mode fallback skipped: settings.dat was not found."
        return $null
    }

    $patchDir = Join-Path $WorkRoot "developer-mode-fallback"
    New-Item -ItemType Directory -Force -Path $patchDir | Out-Null
    $patchPath = Join-Path $patchDir "settings.dat"
    $backupPath = Join-Path $patchDir ("settings.dat.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

    try {
        Write-Log "Preparing reversible Developer-mode settings fallback."
        # WSA holds settings.dat open while its SettingsApp/runtime is alive.
        # Stop the same WSA processes that will be restarted below *before*
        # copying the backup, otherwise this reversible repair can never run.
        Stop-Process -Name "WsaSettings","WsaClient","WindowsSubsystemForAndroid","WsaService","vmmemWSA" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Copy-Item -LiteralPath $settingsPath -Destination $backupPath -Force

        $needDownload = $true
        if (Test-Path $patchPath) {
            try {
                $blob = Get-GitBlobSha1 -Path $patchPath
                if ($blob -eq $DeveloperSettingsBlobSha1) {
                    $needDownload = $false
                }
            }
            catch {}
        }

        if ($needDownload) {
            Remove-Item -Force -ErrorAction SilentlyContinue $patchPath
            $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
            if ($curl) {
                & curl.exe -L --fail --retry 3 --retry-all-errors --output $patchPath $DeveloperSettingsUrl
                if ($LASTEXITCODE -ne 0) {
                    throw "Could not download pinned Developer-mode settings fallback."
                }
            }
            else {
                Invoke-WebRequest -UseBasicParsing -Uri $DeveloperSettingsUrl -OutFile $patchPath
            }
        }

        if ((Get-Item $patchPath).Length -ne 8192) {
            throw "Pinned Developer-mode settings fallback has unexpected size."
        }
        $blobSha = Get-GitBlobSha1 -Path $patchPath
        if ($blobSha -ne $DeveloperSettingsBlobSha1) {
            throw "Pinned Developer-mode settings fallback hash mismatch."
        }

        [ordered]@{
            source_commit = $DeveloperSettingsSourceCommit
            blob_sha1 = $blobSha
            source_url = $DeveloperSettingsUrl
            original_settings = $settingsPath
            backup = $backupPath
        } | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $ReportStage "developer-mode-fallback.json")

        Copy-Item -LiteralPath $patchPath -Destination $settingsPath -Force
        Write-Log "Developer-mode fallback applied; original settings preserved at $backupPath."
        return $backupPath
    }
    catch {
        Write-Log "Developer-mode fallback could not be applied safely: $($_.Exception.Message)"
        if (Test-Path $backupPath) {
            try { Copy-Item -LiteralPath $backupPath -Destination $settingsPath -Force } catch {}
        }
        return $null
    }
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = $sha.ComputeHash($stream)
            return ([System.BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
        }
        finally {
            $sha.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}


function Get-WsaExcludedPortRanges {
    try {
        return (& netsh.exe interface ipv4 show excludedportrange protocol=tcp 2>&1 | Out-String)
    }
    catch {
        return ""
    }
}

function Test-WsaPortReserved {
    param([int]$Port = 58526)
    $text = Get-WsaExcludedPortRanges
    foreach ($line in ($text -split "`r?`n")) {
        if ($line -match "^\s*(\d+)\s+(\d+)") {
            $start = [int]$matches[1]
            $finish = [int]$matches[2]
            if ($Port -ge $start -and $Port -le $finish) {
                return $true
            }
        }
    }
    return $false
}

function Ensure-WsaAdbPortReservation {
    param(
        [Parameter(Mandatory = $true)][string]$ReportStage,
        [int]$Port = 58526
    )

    $before = Get-WsaExcludedPortRanges
    $before | Set-Content -Encoding UTF8 (Join-Path $ReportStage "excluded-tcp-ranges-before.txt")
    if (Test-WsaPortReserved -Port $Port) {
        Write-Log "WSA ADB port $Port is already reserved from the Windows dynamic/Hyper-V port pool."
        return $true
    }

    Write-Log "WSA ADB port $Port is not reserved; applying the official WSABuilds 10061 prevention before WSA starts."
    Stop-Process -Name "WsaClient","WindowsSubsystemForAndroid","WsaService","vmmemWSA" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    $result = ""
    try {
        $result = (& netsh.exe int ipv4 add excludedportrange protocol=tcp startport=$Port numberofports=1 2>&1 | Out-String).Trim()
    }
    catch {
        $result = $_.Exception.Message
    }
    $result | Set-Content -Encoding UTF8 (Join-Path $ReportStage "port-58526-reservation.txt")

    $after = Get-WsaExcludedPortRanges
    $after | Set-Content -Encoding UTF8 (Join-Path $ReportStage "excluded-tcp-ranges-after.txt")
    $ok = Test-WsaPortReserved -Port $Port
    if ($ok) {
        Write-Log "WSA ADB port $Port reservation is active."
    }
    else {
        Write-Log "WARNING: Windows did not reserve WSA ADB port $Port. The verifier will continue with guest-IP/HNS fallbacks instead of assuming localhost is usable."
    }
    return $ok
}

function Ensure-WsaLoopbackExemption {
    param([Parameter(Mandatory = $true)][string]$ReportStage)

    $family = "microsoftcorporationii.windowssubsystemforandroid_8wekyb3d8bbwe"
    $before = ""
    try {
        $before = (& CheckNetIsolation.exe LoopbackExempt -s 2>&1 | Out-String)
    }
    catch {}
    $before | Set-Content -Encoding UTF8 (Join-Path $ReportStage "loopback-exempt-before.txt")

    if ($before -match [regex]::Escape($family)) {
        Write-Log "WSA loopback exemption is already present."
        return $true
    }

    Write-Log "Adding WSA loopback exemption for localhost ADB forwarding."
    $add = ""
    try {
        $add = (& CheckNetIsolation.exe LoopbackExempt -a -n=$family 2>&1 | Out-String).Trim()
    }
    catch {
        $add = $_.Exception.Message
    }
    $add | Set-Content -Encoding UTF8 (Join-Path $ReportStage "loopback-exempt-add.txt")

    $after = ""
    try {
        $after = (& CheckNetIsolation.exe LoopbackExempt -s 2>&1 | Out-String)
    }
    catch {}
    $after | Set-Content -Encoding UTF8 (Join-Path $ReportStage "loopback-exempt-after.txt")
    $ok = ($after -match [regex]::Escape($family))
    if (-not $ok) {
        Write-Log "WARNING: WSA loopback exemption could not be verified; guest-IP ADB discovery will remain enabled."
    }
    return $ok
}

function Test-TcpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMs = 700
    )

    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $tcp.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        try {
            $tcp.EndConnect($async)
            return $tcp.Connected
        }
        catch {
            return $false
        }
    }
    catch {
        return $false
    }
    finally {
        try { $tcp.Close() } catch {}
    }
}

function Get-WsaRuntimeSnapshot {
    $processRows = @()
    try {
        $processRows = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match "(?i)wsa|vmmem"
        } | Select-Object Name, ProcessId, ParentProcessId, ExecutablePath, CommandLine)
    }
    catch {}

    $listeners = @()
    try {
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {
            $_.LocalPort -eq 58526 -or $_.LocalPort -eq 5555
        } | Select-Object LocalAddress, LocalPort, OwningProcess, State)
    }
    catch {}

    return [pscustomobject]@{
        timestamp = (Get-Date).ToString("o")
        processes = $processRows
        listeners = $listeners
        runtime_alive = [bool](@($processRows | Where-Object {
            $_.Name -match "(?i)^WsaClient\.exe$|^WsaService\.exe$|^WindowsSubsystemForAndroid\.exe$|^vmmemWSA\.exe$"
        }).Count)
    }
}

function Save-WsaHostDiagnostics {
    param(
        [Parameter(Mandatory = $true)][string]$ReportStage,
        [datetime]$Since = (Get-Date).AddMinutes(-15)
    )

    try {
        $snapshot = Get-WsaRuntimeSnapshot
        $snapshot | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-host-runtime.json")
    }
    catch {}

    try {
        Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "(?i)WSA|vEthernet|Hyper-V" -or $_.InterfaceDescription -match "(?i)Hyper-V|virtual" } |
            Select-Object Name, InterfaceDescription, Status, MacAddress, LinkSpeed, ifIndex |
            ConvertTo-Json -Depth 5 |
            Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-net-adapters.json")
    }
    catch {}

    try {
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Select-Object InterfaceAlias, InterfaceIndex, IPAddress, PrefixLength, AddressState |
            ConvertTo-Json -Depth 5 |
            Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-ip-addresses.json")
    }
    catch {}

    try {
        Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Select-Object InterfaceAlias, InterfaceIndex, IPAddress, LinkLayerAddress, State |
            ConvertTo-Json -Depth 5 |
            Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-neighbors.json")
    }
    catch {}

    try {
        if (Get-Command Get-HnsEndpoint -ErrorAction SilentlyContinue) {
            Get-HnsEndpoint | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-hns-endpoints.json")
        }
    }
    catch {}

    try {
        $hnsdiag = Get-Command hnsdiag.exe -ErrorAction SilentlyContinue
        if ($hnsdiag) {
            (& $hnsdiag.Source list endpoints 2>&1 | Out-String) |
                Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-hnsdiag-endpoints.txt")
        }
    }
    catch {}

    foreach ($logName in @(
        "Microsoft-Windows-AppXDeploymentServer/Operational",
        "Microsoft-Windows-Hyper-V-Compute-Admin",
        "Microsoft-Windows-Host-Network-Service-Admin"
    )) {
        try {
            $safe = ($logName -replace "[^A-Za-z0-9.-]", "_")
            Get-WinEvent -LogName $logName -MaxEvents 60 -ErrorAction Stop |
                Where-Object { $_.TimeCreated -ge $Since } |
                Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message |
                Format-List | Out-String |
                Set-Content -Encoding UTF8 (Join-Path $ReportStage ("event-" + $safe + ".txt"))
        }
        catch {}
    }

    try {
        Get-WinEvent -FilterHashtable @{ LogName = "Application"; StartTime = $Since } -ErrorAction SilentlyContinue |
            Where-Object { $_.Message -match "(?i)WsaClient|WindowsSubsystemForAndroid|vmmemWSA|WsaService" } |
            Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message |
            Format-List | Out-String |
            Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-application-events.txt")
    }
    catch {}

    try {
        $diagRoot = Join-Path $env:LOCALAPPDATA "Packages\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe\LocalState\diagnostics"
        if (Test-Path $diagRoot) {
            Get-ChildItem -LiteralPath $diagRoot -Force -ErrorAction SilentlyContinue |
                Select-Object FullName, Length, LastWriteTime |
                Format-Table -AutoSize | Out-String |
                Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-local-diagnostics-files.txt")
            $logcat = Join-Path $diagRoot "logcat"
            if (Test-Path $logcat -PathType Leaf) {
                Get-Content -LiteralPath $logcat -Tail 3000 -ErrorAction SilentlyContinue |
                    Set-Content -Encoding UTF8 (Join-Path $ReportStage "wsa-host-logcat-tail.txt")
            }
        }
    }
    catch {}
}
if ($PrepareOnly -and $RuntimeOnly) {
    throw "-PrepareOnly and -RuntimeOnly cannot be used together."
}

if (-not $RuntimeOnly -and -not (Test-IsAdmin)) {
    Write-Host "Administrator rights are required only for WSA setup/registration."
    Write-Host "Requesting elevation for the setup phase..."
    $childExit = Invoke-SelfElevated
    exit $childExit
}

if ($RuntimeOnly -and (Test-IsAdmin)) {
    Write-Host "WARNING: RuntimeOnly is intended for the normal interactive user session."
}

$ReportsRoot = Join-Path $WorkRoot "reports"
New-Item -ItemType Directory -Force -Path $WorkRoot, $DownloadRoot, $RuntimeRoot, $ReportsRoot | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = Join-Path $ReportsRoot ("wsa-p0-" + $stamp + "-" + $PID)
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$p0StartedAt = Get-Date
$manifestPath = Join-Path $stage "manifest.json"
$consolePath = Join-Path $stage "console.txt"
$latestLocalPath = Join-Path $ReportsRoot "LATEST-LOCAL.json"
[ordered]@{
    report_path = $stage
    started_at = (Get-Date).ToString("o")
    state = "RUNNING"
} | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 $latestLocalPath

# Keep the latest 20 local P0 reports. Persistent storage makes evidence
# survive reboot while bounding disk use.
Get-ChildItem -LiteralPath $ReportsRoot -Directory -Filter "wsa-p0-*" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

function Write-Log {
    param([string]$Message)
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    Add-Content -Encoding UTF8 -Path $consolePath -Value $line
}

function Save-Manifest {
    param(
        [string]$State,
        [int]$ExitCode = 0,
        [hashtable]$Extra = @{}
    )
    $commitText = (& git -C $Root rev-parse HEAD 2>$null | Out-String).Trim()
    if (-not $commitText) { $commitText = "unknown" }
    $payload = [ordered]@{
        schema = 1
        kind = "wsa-poc"
        state = $State
        exit_code = $ExitCode
        commit = $commitText
        source_branch = (& git branch --show-current).Trim()
        created_at = (Get-Date).ToString("o")
        wsa_release = $ReleaseTag
        archive = $ArchiveName
        archive_sha256 = $ArchiveSha256
        serial = $Serial
        install_root = $InstallRoot
    }
    foreach ($key in $Extra.Keys) {
        $payload[$key] = $Extra[$key]
    }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $manifestPath
}

function Upload-Report {
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "upload_runtime_report.ps1") -ReportSource $stage
    }
    catch {
        Write-Host "Report upload failed:"
        Write-Host ($_ | Out-String)
    }
}

function Finish-Report {
    param(
        [string]$State,
        [int]$ExitCode = 0,
        [hashtable]$Extra = @{}
    )
    Save-Manifest -State $State -ExitCode $ExitCode -Extra $Extra
    [ordered]@{
        report_path = $stage
        finished_at = (Get-Date).ToString("o")
        state = $State
        exit_code = $ExitCode
        commit = ((& git -C $Root rev-parse HEAD 2>$null | Out-String).Trim())
    } | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 $latestLocalPath
    Upload-Report
    Write-Host ""
    Write-Host "WSA PoC state: $State"
    Write-Host "Persistent local report: $stage"
    Write-Host "Latest local pointer: $latestLocalPath"
}

trap {
    $detail = ($_ | Out-String)
    try {
        $detail | Set-Content -Encoding UTF8 (Join-Path $stage "uncaught-error.txt")
        Finish-Report -State "UNCAUGHT_ERROR" -ExitCode 99 -Extra @{
            error = $_.Exception.Message
            category = $_.CategoryInfo.Category.ToString()
            target = [string]$_.CategoryInfo.TargetName
        }
    }
    catch {
        Write-Host "Secondary failure while uploading uncaught error:"
        Write-Host ($_ | Out-String)
    }
    Write-Host $detail
    exit 99
}

Write-Log "Starting WSA LTS 8 PoC installer."

$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$currentVersion = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
$build = [int]$os.BuildNumber
$ubr = [int]$currentVersion.UBR
$fullBuild = "$build.$ubr"

$hostInfo = [ordered]@{
    caption = $os.Caption
    version = $os.Version
    build = $os.BuildNumber
    ubr = $ubr
    full_build = $fullBuild
    display_version = $currentVersion.DisplayVersion
    architecture = $os.OSArchitecture
    cpu = $cpu.Name
    virtualization_firmware_enabled = $cpu.VirtualizationFirmwareEnabled
    slat = $cpu.SecondLevelAddressTranslationExtensions
}
$hostInfo | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $stage "host.json")

if ($build -ge 22000) {
    if ($WsaFlavor -eq "GApps") {
        throw "The pinned GApps migration is currently validated only for Windows 10 22H2."
    }
    $ReleaseTag = "Windows_11_2407.40000.4.0_LTS_8"
    $ArchiveName = "WSA_2407.40000.4.0_x64_Release-Nightly-NoGApps-NoAmazon.7z"
    $ArchiveSha256 = "9c51759762f14cdebde7da08ccf94deb220484215468526e1ef688fd669ab7c1"
    $InstallRoot = Join-Path $WorkRoot "WSA_LTS8_Windows11"
    Write-Log "Selected WSABuilds LTS 8 package for Windows 11 ($fullBuild)."
}
elseif ($build -eq 19045 -and $ubr -ge 2311) {
    $ReleaseTag = "Windows_10_2407.40000.4.0_LTS_8"
    if ($WsaFlavor -eq "GApps") {
        if (-not $AllowMagisk) {
            throw "The pinned WSABuilds LTS 8 GApps package includes Magisk. Pass -AllowMagisk only after explicit user approval."
        }
        $ArchiveName = "WSA_2407.40000.4.0_x64_Release-Nightly-GApps-13.0-NoAmazon_Windows_10.7z"
        $ArchiveSha256 = "501a3ad48c998e9b1e1d91cfbdfb742f8f46f927e9f09dc9b11c70abbe074458"
        $InstallRoot = Join-Path $WorkRoot "WSA_LTS8_Windows10_GApps"
    }
    else {
        $ArchiveName = "WSA_2407.40000.4.0_x64_Release-Nightly-NoGApps-NoAmazon_Windows_10.7z"
        $ArchiveSha256 = "366c344eee70e610e905c7588f661ce028faef8ae55ec9cc6c8dd348ec2cb7c8"
        $InstallRoot = Join-Path $WorkRoot "WSA_LTS8_Windows10"
    }
    Write-Log "Selected WSABuilds LTS 8 $WsaFlavor package for Windows 10 22H2 ($fullBuild)."
}
else {
    Finish-Report -State "UNSUPPORTED_WINDOWS_BUILD" -ExitCode 11 -Extra @{ host = $hostInfo }
    throw (
        "This WSA PoC requires Windows 11 build 22000+ or Windows 10 22H2 " +
        "build 19045.2311+. Detected $fullBuild."
    )
}

$ArchiveUrl = "https://github.com/MustardChef/WSABuilds/releases/download/$ReleaseTag/$ArchiveName"
$ArchivePath = Join-Path $DownloadRoot $ArchiveName

if ($cpu.VirtualizationFirmwareEnabled -eq $false) {
    Finish-Report -State "VIRTUALIZATION_DISABLED" -ExitCode 12 -Extra @{ host = $hostInfo }
    throw "CPU virtualization is disabled in BIOS/UEFI. Enable Intel VT-x/AMD-V and rerun."
}

if (-not $RuntimeOnly) {
    $developerRegistrationReady = $false
    $developerRegistrationDetail = ""
    try {
        $regOutput = (& reg.exe add "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" /t REG_DWORD /f /v "AllowDevelopmentWithoutDevLicense" /d "1" 2>&1 | Out-String).Trim()
        $regExit = $LASTEXITCODE
        $developerRegistrationDetail = "reg.exe exit=$regExit output=$regOutput"
        if ($regExit -eq 0) {
            $developerRegistrationReady = $true
        }
    }
    catch {
        $developerRegistrationDetail = "reg.exe failed: $($_.Exception.Message)"
    }

    if (-not $developerRegistrationReady) {
        try {
            $devKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
            New-Item -Path $devKey -Force | Out-Null
            New-ItemProperty -Path $devKey -Name "AllowDevelopmentWithoutDevLicense" -PropertyType DWord -Value 1 -Force | Out-Null
            $actual = (Get-ItemProperty -Path $devKey -Name "AllowDevelopmentWithoutDevLicense" -ErrorAction Stop).AllowDevelopmentWithoutDevLicense
            if ([int]$actual -eq 1) {
                $developerRegistrationReady = $true
                $developerRegistrationDetail += "; PowerShell registry fallback succeeded"
            }
        }
        catch {
            $developerRegistrationDetail += "; PowerShell registry fallback failed: $($_.Exception.Message)"
        }
    }

    $developerRegistrationDetail | Set-Content -Encoding UTF8 (Join-Path $stage "developer-package-registration.txt")
    if ($developerRegistrationReady) {
        Write-Log "Windows developer package registration is enabled."
    }
    else {
        # This setting is only a prerequisite for registration attempts. An already
        # registered WSA package does not need to be blocked by a registry-policy
        # write failure; AppX registration below remains the authoritative gate.
        Write-Log "WARNING: developer package registration setting could not be changed; continuing to authoritative WSA registration check."
    }

    $featureState = @{}
    $needsReboot = $false
    foreach ($featureName in @("VirtualMachinePlatform", "HypervisorPlatform")) {
        $currentFeature = Get-WindowsOptionalFeature -Online -FeatureName $featureName -ErrorAction Stop
        $featureState[$featureName] = $currentFeature.State.ToString()
        if ($currentFeature.State -ne "Enabled") {
            Write-Log "Enabling Windows feature: $featureName"
            $featureResult = Enable-WindowsOptionalFeature -Online -FeatureName $featureName -All -NoRestart
            if ($featureResult.RestartNeeded) {
                $needsReboot = $true
            }
        }
    }
    $featureState | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 (Join-Path $stage "windows-features-before.json")

    $bcd = (& bcdedit /enum "{current}" 2>&1 | Out-String)
    $bcd | Set-Content -Encoding UTF8 (Join-Path $stage "bcd-current.txt")
    if ($bcd -match "hypervisorlaunchtype\s+Off") {
        Write-Log "Enabling Windows hypervisor launch at boot."
        & bcdedit /set hypervisorlaunchtype auto | Out-Null
        $needsReboot = $true
    }

    if ($needsReboot) {
        Finish-Report -State "NEEDS_REBOOT" -ExitCode 3010 -Extra @{ host = $hostInfo }
        Write-Host ""
        Write-Host "Windows virtualization components were enabled."
        Write-Host "Restart Windows, then run the SAME command again."
        exit 3010
    }


}
else {
    Write-Log "RuntimeOnly: skipping admin-only registry, Windows feature, and BCD setup checks."
}

if (-not $SkipInstall -and -not $RuntimeOnly) {
    $existing = Get-AppxPackage | Where-Object {
        $_.Name -like "*WindowsSubsystemForAndroid*"
    } | Select-Object -First 1

    $needsPackageInstall = ($null -eq $existing)
    if ($existing) {
        $existing | Select-Object Name, PackageFullName, Version, InstallLocation | Format-List | Out-String | Set-Content -Encoding UTF8 (Join-Path $stage "existing-wsa.txt")

        if (-not ($existing.InstallLocation -like "$InstallRoot*")) {
            if (-not $ReplaceExistingWsa) {
                Finish-Report -State "EXISTING_WSA_CONFLICT" -ExitCode 13 -Extra @{
                    existing_package = $existing.PackageFullName
                    existing_location = $existing.InstallLocation
                }
                throw ("Another WSA installation already exists at '$($existing.InstallLocation)'. Pass -ReplaceExistingWsa only for an explicitly approved migration.")
            }

            $migrationBackup = Join-Path $WorkRoot ("profile-backups\wsa-flavor-migration-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
            New-Item -ItemType Directory -Force -Path $migrationBackup | Out-Null
            $packageData = Join-Path $env:LOCALAPPDATA "Packages\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe"
            $userdata = Join-Path $packageData "LocalCache\userdata.vhdx"
            if (Test-Path -LiteralPath $userdata -PathType Leaf) {
                Copy-Item -LiteralPath $userdata -Destination (Join-Path $migrationBackup "userdata.vhdx") -Force
            }
            @{
                package = $existing.PackageFullName
                install_location = $existing.InstallLocation
                flavor_before = if ($existing.InstallLocation -match "GApps") { "GApps" } else { "NoGApps" }
                flavor_after = $WsaFlavor
            } | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 (Join-Path $migrationBackup "migration.json")
            Write-Log "Backed up existing WSA userdata metadata to $migrationBackup."
            Stop-Process -Name "WsaClient","WindowsSubsystemForAndroid","WsaService","vmmemWSA" -Force -ErrorAction SilentlyContinue
            Remove-AppxPackage -Package $existing.PackageFullName -ErrorAction Stop
            $existing = $null
            $needsPackageInstall = $true
            Write-Log "Removed the current-user WSA registration for approved $WsaFlavor migration."
        }
        else {
            Write-Log "TUGARIN BOTS $WsaFlavor WSA package is already registered; keeping it."
        }
    }

    if ($needsPackageInstall) {
        $needDownload = $true
        if (Test-Path $ArchivePath) {
            $hash = Get-Sha256 -Path $ArchivePath
            if ($hash -eq $ArchiveSha256) {
                $needDownload = $false
                Write-Log "WSA archive already downloaded and SHA-256 matches."
            }
            else {
                Write-Log "Existing WSA archive hash mismatch; removing it."
                Remove-Item -Force $ArchivePath
            }
        }

        if ($needDownload) {
            Write-Log "Downloading WSABuilds LTS 8 $WsaFlavor/NoAmazon package for this Windows build."
            $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
            if ($curl) {
                & curl.exe -L --fail --retry 5 --retry-all-errors --output $ArchivePath $ArchiveUrl
                if ($LASTEXITCODE -ne 0) {
                    throw "curl.exe failed to download WSA archive."
                }
            }
            else {
                Invoke-WebRequest -UseBasicParsing -Uri $ArchiveUrl -OutFile $ArchivePath
            }
        }

        $hash = Get-Sha256 -Path $ArchivePath
        Set-Content -Encoding ASCII -Path (Join-Path $stage "archive-sha256.txt") -Value $hash
        if ($hash -ne $ArchiveSha256) {
            Finish-Report -State "ARCHIVE_HASH_MISMATCH" -ExitCode 14 -Extra @{ actual_sha256 = $hash }
            throw "WSA archive SHA-256 mismatch; refusing installation."
        }

        $sevenZip = @(
            "C:\Program Files\7-Zip\7z.exe",
            "C:\Program Files (x86)\7-Zip\7z.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1

        if (-not $sevenZip) {
            $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
            if (-not $winget) {
                Finish-Report -State "SEVENZIP_MISSING" -ExitCode 15
                throw "7-Zip is not installed and winget.exe is unavailable."
            }
            Write-Log "Installing 7-Zip through winget."
            & winget.exe install --id 7zip.7zip -e --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) {
                throw "winget failed to install 7-Zip."
            }
            $sevenZip = "C:\Program Files\7-Zip\7z.exe"
        }

        if (-not (Test-Path (Join-Path $InstallRoot "AppxManifest.xml"))) {
            if (Test-Path $InstallRoot) {
                Remove-Item -Recurse -Force $InstallRoot
            }
            New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
            Write-Log "Extracting WSA archive."
            & $sevenZip x $ArchivePath "-o$InstallRoot" -y | Out-File -Encoding UTF8 (Join-Path $stage "7zip.txt")
            if ($LASTEXITCODE -ne 0) {
                throw "7-Zip extraction failed."
            }
        }

        $manifest = Get-ChildItem -Path $InstallRoot -Filter AppxManifest.xml -Recurse -File | Select-Object -First 1
        if (-not $manifest) {
            Finish-Report -State "EXTRACTED_PACKAGE_INVALID" -ExitCode 16
            throw "AppxManifest.xml was not found after extraction."
        }
        $packageDir = $manifest.Directory.FullName
        Set-Content -Encoding UTF8 -Path (Join-Path $WorkRoot "package-path.txt") -Value $packageDir

        $installScript = Join-Path $packageDir "Install.ps1"
        if (-not (Test-Path $installScript)) {
            Finish-Report -State "INSTALL_SCRIPT_MISSING" -ExitCode 17
            throw "Install.ps1 was not found in extracted WSABuilds package."
        }

        # WSABuilds' optional MakePri merge can fail with PRI175/0x80073b26
        # on Windows 10 resource packages. Their own installer treats this as
        # non-fatal (WSA Settings may remain English) and continues. For WAR
        # BOT we do not need localized WSA Settings, so register the package
        # non-interactively without running the optional MakePri merge.
        Write-Log "Registering Windows Subsystem for Android (non-interactive, skipping optional MakePri localization merge)."
        Push-Location $packageDir
        try {
            [xml]$manifestXml = Get-Content -LiteralPath ".\AppxManifest.xml"
            $packageName = [string]$manifestXml.Package.Identity.Name
            $processorArchitecture = [string]$manifestXml.Package.Identity.ProcessorArchitecture
            $dependencies = @($manifestXml.Package.Dependencies.PackageDependency)

            foreach ($dep in $dependencies) {
                if ($null -eq $dep) { continue }
                $depName = [string]$dep.Name
                $minVersion = [version]([string]$dep.MinVersion)
                $installedDep = Get-AppxPackage -Name $depName | Where-Object {
                    $_.Architecture.ToString() -eq $processorArchitecture
                } | Sort-Object Version | Select-Object -Last 1

                $needDep = ($null -eq $installedDep)
                if (-not $needDep) {
                    $needDep = ([version]$installedDep.Version -lt $minVersion)
                }

                if ($needDep) {
                    $depPath = Join-Path $packageDir ("{0}_{1}.appx" -f $depName, $processorArchitecture)
                    if (-not (Test-Path $depPath)) {
                        throw "Required WSA dependency package is missing: $depPath"
                    }
                    Write-Log "Installing dependency $depName $processorArchitecture (minimum $minVersion)."
                    Add-AppxPackage -ForceApplicationShutdown -ForceUpdateFromAnyVersion -Path $depPath -ErrorAction Stop
                }
                else {
                    Write-Log "Dependency $depName already satisfies minimum $minVersion."
                }
            }

            # Do not launch the nested WsaClient.exe directly. It depends on
            # package identity / root DLL search paths and can show a false
            # gfxstream_backend.dll "missing" dialog when invoked as a plain exe.
            Stop-Process -Name "WsaClient","WindowsSubsystemForAndroid","WsaService" -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2

            Add-AppxPackage -ForceApplicationShutdown -ForceUpdateFromAnyVersion -Register ".\AppxManifest.xml" -ErrorAction Stop
            Write-Log "WSA AppX registration completed."
        }
        catch {
            try {
                Get-WinEvent -LogName "Microsoft-Windows-AppXDeploymentServer/Operational" -MaxEvents 80 |
                    Select-Object TimeCreated, Id, LevelDisplayName, Message |
                    Format-List | Out-String |
                    Set-Content -Encoding UTF8 (Join-Path $stage "appx-deployment-events.txt")
            }
            catch {}
            throw
        }
        finally {
            Pop-Location
        }
    }
}

$installed = Get-AppxPackage | Where-Object {
    $_.Name -like "*WindowsSubsystemForAndroid*"
} | Select-Object -First 1

if ($RuntimeOnly -and -not $installed) {
    Write-Log "WSA is not registered for the current interactive user; registering the already extracted package in this user context."
    $runtimeManifest = Get-ChildItem -Path $InstallRoot -Filter AppxManifest.xml -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $runtimeManifest) {
        Finish-Report -State "WSA_CURRENT_USER_PACKAGE_MISSING" -ExitCode 18 -Extra @{
            install_root = $InstallRoot
        }
        exit 18
    }

    $runtimePackageDir = $runtimeManifest.Directory.FullName
    $currentUserRegistrationError = $null
    try {
        Push-Location $runtimePackageDir
        [xml]$runtimeManifestXml = Get-Content -LiteralPath ".\AppxManifest.xml"
        $runtimeArchitecture = [string]$runtimeManifestXml.Package.Identity.ProcessorArchitecture
        $runtimeDependencies = @($runtimeManifestXml.Package.Dependencies.PackageDependency)

        foreach ($dep in $runtimeDependencies) {
            if ($null -eq $dep) { continue }
            $depName = [string]$dep.Name
            $minVersion = [version]([string]$dep.MinVersion)
            $installedDep = Get-AppxPackage -Name $depName | Where-Object {
                $_.Architecture.ToString() -eq $runtimeArchitecture
            } | Sort-Object Version | Select-Object -Last 1

            $needDep = ($null -eq $installedDep)
            if (-not $needDep) {
                $needDep = ([version]$installedDep.Version -lt $minVersion)
            }

            if ($needDep) {
                $depPath = Join-Path $runtimePackageDir ("{0}_{1}.appx" -f $depName, $runtimeArchitecture)
                if (-not (Test-Path -LiteralPath $depPath -PathType Leaf)) {
                    throw "Required current-user WSA dependency is missing: $depPath"
                }
                Write-Log "Installing current-user dependency $depName $runtimeArchitecture (minimum $minVersion)."
                Add-AppxPackage -ForceApplicationShutdown -ForceUpdateFromAnyVersion -Path $depPath -ErrorAction Stop
            }
        }

        Add-AppxPackage -ForceApplicationShutdown -ForceUpdateFromAnyVersion -Register ".\AppxManifest.xml" -ErrorAction Stop
        Write-Log "WSA registered successfully for the current interactive user."
    }
    catch {
        $currentUserRegistrationError = $_
        ($_ | Out-String) | Set-Content -Encoding UTF8 (Join-Path $stage "current-user-registration-error.txt")
    }
    finally {
        Pop-Location
    }

    if ($currentUserRegistrationError) {
        Finish-Report -State "WSA_CURRENT_USER_REGISTRATION_FAILED" -ExitCode 18 -Extra @{
            install_location = $runtimePackageDir
            registration_error = $currentUserRegistrationError.Exception.Message
        }
        exit 18
    }

    $installed = Get-AppxPackage | Where-Object {
        $_.Name -like "*WindowsSubsystemForAndroid*"
    } | Select-Object -First 1
}

if (-not $installed) {
    Finish-Report -State "WSA_NOT_REGISTERED" -ExitCode 18
    exit 18
}
$installed | Select-Object Name, PackageFullName, Version, InstallLocation | Format-List | Out-String | Set-Content -Encoding UTF8 (Join-Path $stage "installed-wsa.txt")

if ($PrepareOnly) {
    $preparedVersion = $installed.Version.ToString()
    $preparedLocation = $installed.InstallLocation

    if ($installed.InstallLocation -like "$InstallRoot*") {
        Write-Log "Removing the managed WSA registration from the elevated setup account so the interactive user can own the unpackaged app registration."
        try {
            Remove-AppxPackage -Package $installed.PackageFullName -ErrorAction Stop
            Start-Sleep -Seconds 2
            Write-Log "Elevated-account WSA registration removed; extracted package files are preserved."
        }
        catch {
            ($_ | Out-String) | Set-Content -Encoding UTF8 (Join-Path $stage "prepare-unregister-error.txt")
            Finish-Report -State "WSA_PREPARE_UNREGISTER_FAILED" -ExitCode 18 -Extra @{
                installed_version = $preparedVersion
                installed_location = $preparedLocation
                unregister_error = $_.Exception.Message
                phase = "prepare"
            }
            exit 18
        }
    }

    Write-Log "WSA setup prerequisites are ready. Returning to the normal interactive user session for current-user registration and runtime startup."
    Finish-Report -State "WSA_PREPARE_PASS" -ExitCode 0 -Extra @{
        installed_version = $preparedVersion
        installed_location = $preparedLocation
        elevated_registration_removed = $true
        phase = "prepare"
    }
    exit 0
}

if ($RuntimeOnly) {
    Write-Log "Starting WSA runtime verification in the normal interactive user session."
}

$packagePreflight = [ordered]@{
    install_location = $installed.InstallLocation
    install_path_length = if ($installed.InstallLocation) { $installed.InstallLocation.Length } else { 0 }
    filesystem = ""
    path_short_enough = $true
    appx_manifest = ""
    manifest_min_version = ""
    manifest_has_custom_install = $false
    win10_patch_required = ($build -eq 19045)
    wsapatch_dll_present = $null
    patched_icu_present = $null
    gfxstream_paths = @()
}
try {
    if ($installed.InstallLocation) {
        $rootPath = [System.IO.Path]::GetPathRoot($installed.InstallLocation)
        if ($rootPath -match "^([A-Za-z]):") {
            $driveLetter = $matches[1]
            $volume = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
            $packagePreflight.filesystem = [string]$volume.FileSystem
            if ($volume.FileSystem -ne "NTFS") {
                Finish-Report -State "WSA_INSTALL_VOLUME_UNSUPPORTED" -ExitCode 18 -Extra @{
                    filesystem = $volume.FileSystem
                    install_location = $installed.InstallLocation
                }
                throw "WSA unpackaged registration requires an NTFS installation volume."
            }
        }

        if ($installed.InstallLocation.Length -gt 120) {
            $packagePreflight.path_short_enough = $false
            Write-Log "WARNING: WSA install path is unusually long; WSABuilds documents long extracted paths as a cause of Settings/app startup crashes."
        }

        $manifestPath = Join-Path $installed.InstallLocation "AppxManifest.xml"
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            $manifestPath = (Get-ChildItem -LiteralPath $installed.InstallLocation -Filter AppxManifest.xml -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
        }
        if ($manifestPath) {
            $packagePreflight.appx_manifest = $manifestPath
            [xml]$preflightManifest = Get-Content -LiteralPath $manifestPath
            $targetFamilies = @($preflightManifest.Package.Dependencies.TargetDeviceFamily)
            $desktopTarget = $targetFamilies | Where-Object { $_.Name -eq "Windows.Desktop" } | Select-Object -First 1
            if ($desktopTarget) {
                $packagePreflight.manifest_min_version = [string]$desktopTarget.MinVersion
            }
            $manifestText = Get-Content -LiteralPath $manifestPath -Raw
            $packagePreflight.manifest_has_custom_install = [bool]($manifestText -match "(?i)windows\.customInstall|customInstallActions")
        }

        $wsaClientDir = Join-Path $installed.InstallLocation "WsaClient"
        $packagePreflight.wsapatch_dll_present = Test-Path -LiteralPath (Join-Path $wsaClientDir "WsaPatch.dll") -PathType Leaf
        $packagePreflight.patched_icu_present = Test-Path -LiteralPath (Join-Path $wsaClientDir "icu.dll") -PathType Leaf
        $packagePreflight.gfxstream_paths = @(
            Get-ChildItem -LiteralPath $installed.InstallLocation -Filter "gfxstream_backend.dll" -File -Recurse -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty FullName
        )

        if ($build -eq 19045) {
            $manifestCompatible = $true
            if ($packagePreflight.manifest_min_version) {
                try {
                    $manifestCompatible = ([version]$packagePreflight.manifest_min_version -le [version]$fullBuild)
                }
                catch {}
            }

            if (-not $packagePreflight.wsapatch_dll_present -or
                -not $packagePreflight.patched_icu_present -or
                -not $manifestCompatible -or
                $packagePreflight.manifest_has_custom_install) {
                $packagePreflight | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-package-preflight.json")
                Finish-Report -State "WSA_WIN10_PATCH_INVALID" -ExitCode 18 -Extra @{
                    wsapatch_dll_present = $packagePreflight.wsapatch_dll_present
                    patched_icu_present = $packagePreflight.patched_icu_present
                    manifest_min_version = $packagePreflight.manifest_min_version
                    manifest_has_custom_install = $packagePreflight.manifest_has_custom_install
                }
                throw "The installed WSA package does not satisfy the Windows 10 WSAPatch prerequisites."
            }
        }
    }
}
catch {
    if ($_.Exception.Message -match "requires an NTFS|WSAPatch prerequisites") { throw }
    Write-Log "Could not fully inspect WSA package preflight: $($_.Exception.Message)"
}
$packagePreflight | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-package-preflight.json")

$portReservationOk = Ensure-WsaAdbPortReservation -ReportStage $stage
$loopbackExemptionOk = Ensure-WsaLoopbackExemption -ReportStage $stage

Save-WsaUserContextDiagnostics -ReportStage $stage

$wsaApplications = @()
try {
    $wsaApplications = @(Get-WsaApplicationCatalog -Package $installed)
}
catch {
    Write-Log "Could not enumerate WSA AUMIDs from package manifest: $($_.Exception.Message)"
}
$wsaApplications | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-applications.json")

$settingsAumid = ""
$clientAumid = ""
$settingsApp = $wsaApplications | Where-Object { $_.id -eq "SettingsApp" } | Select-Object -First 1
if ($settingsApp) { $settingsAumid = [string]$settingsApp.aumid }
$clientApp = $wsaApplications | Where-Object {
    $_.id -eq "App" -or $_.executable -match "(?i)WsaClient\\WsaClient\.exe$|WsaClient\.exe$"
} | Select-Object -First 1
if ($clientApp) { $clientAumid = [string]$clientApp.aumid }

if (-not $settingsAumid) {
    $settingsAumid = $installed.PackageFamilyName + "!SettingsApp"
}
if (-not $clientAumid) {
    $clientAumid = $installed.PackageFamilyName + "!App"
}

[ordered]@{
    package_family = $installed.PackageFamilyName
    settings_aumid = $settingsAumid
    client_aumid = $clientAumid
} | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-aumids.json")

$packagedActivationAttempted = $false
$packagedActivationSucceeded = $false
$packagedActivationRetried = $false
$developerDeepLinkAttempted = $false
$upstreamLaunchRequested = $false

function Invoke-WsaPackagedWake {
    param([switch]$DeveloperSettings)

    $ok = $false
    $packagedActivationAttempted = $true

    if ($settingsAumid) {
        try {
            $settingsPid = Invoke-PackagedApplication -Aumid $settingsAumid
            Write-Log "Packaged activation succeeded for SettingsApp AUMID '$settingsAumid' (pid=$settingsPid)."
            $ok = $true
        }
        catch {
            Write-Log "Packaged SettingsApp activation failed: $($_.Exception.Message)"
        }
    }

    if ($clientAumid) {
        $arguments = if ($DeveloperSettings) {
            "/deeplink wsa-client://developer-settings"
        }
        else {
            "/launch wsa://com.android.settings"
        }
        try {
            $clientPid = Invoke-PackagedApplication -Aumid $clientAumid -Arguments $arguments
            Write-Log "Packaged WsaClient activation succeeded for '$clientAumid' args='$arguments' (pid=$clientPid)."
            $ok = $true
        }
        catch {
            Write-Log "Packaged WsaClient activation failed: $($_.Exception.Message)"
        }
    }

    return $ok
}

Write-Log "Launching WSA through registered AppX AUMIDs so WsaClient keeps package identity."
$packagedActivationSucceeded = Invoke-WsaPackagedWake

if (-not $packagedActivationSucceeded) {
    Write-Log "Packaged activation did not start WSA; trying the upstream wsa:// URI as a secondary route."
    try {
        Start-Process "wsa://com.android.settings" -ErrorAction Stop | Out-Null
        $upstreamLaunchRequested = $true
        Write-Log "Upstream wsa://com.android.settings activation requested."
    }
    catch {
        Write-Log "Upstream WSA URI activation failed: $($_.Exception.Message)"
    }
}

try {
    Start-Process explorer.exe "shell:AppsFolder\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe!SettingsApp" -ErrorAction SilentlyContinue | Out-Null
}
catch {
    Write-Log "Could not open the WSA Settings app through Explorer: $($_.Exception.Message)"
}

$adb = "C:\Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path $adb)) {
    Finish-Report -State "ADB_MISSING" -ExitCode 19
    throw "Android control tool not found at $adb."
}

Write-Host ""
Write-Host "P0 control-channel gate: if Developer mode is OFF in the opened subsystem settings, turn it ON now."
Write-Host "The verifier will keep retrying automatically while the settings window is open."

function Invoke-AdbSafe {
    param(
        [string[]]$Arguments,
        [int]$TimeoutSeconds = 20
    )

    $token = [Guid]::NewGuid().ToString("N")
    $stdoutPath = Join-Path $env:TEMP ("warbot-android-out-" + $token + ".txt")
    $stderrPath = Join-Path $env:TEMP ("warbot-android-err-" + $token + ".txt")
    try {
        $proc = Start-Process -FilePath $adb -ArgumentList $Arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $finished = $proc.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)
        if (-not $finished) {
            try { $proc.Kill() } catch {}
            try { $proc.WaitForExit() } catch {}
        }
        $stdout = ""
        $stderr = ""
        if (Test-Path $stdoutPath) {
            $rawStdout = Get-Content -Raw $stdoutPath
            if ($null -ne $rawStdout) { $stdout = $rawStdout.Trim() }
        }
        if (Test-Path $stderrPath) {
            $rawStderr = Get-Content -Raw $stderrPath
            if ($null -ne $rawStderr) { $stderr = $rawStderr.Trim() }
        }
        $parts = @()
        if ($stdout) { $parts += $stdout }
        if ($stderr) { $parts += $stderr }
        $combined = ($parts -join [Environment]::NewLine).Trim()
        $exitCode = 124
        if ($finished) {
            try {
                $proc.Refresh()
                $exitCode = [int]$proc.ExitCode
            }
            catch {
                # ExitCode is diagnostic only; callers must use semantic output
                # (for example get-state == device) as the success authority.
                $exitCode = -1
            }
        }
        else {
            if ($stderr) { $stderr += [Environment]::NewLine }
            $stderr += "ADB command timed out after $TimeoutSeconds seconds."
            $combined = (($stdout, $stderr | Where-Object { $_ }) -join [Environment]::NewLine).Trim()
        }
        return [pscustomobject]@{
            ExitCode = $exitCode
            Stdout = $stdout
            Stderr = $stderr
            Text = $combined
            TimedOut = (-not $finished)
        }
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $stdoutPath, $stderrPath
    }
}

function Save-AdbDiagnostic {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = 20
    )
    try {
        $result = Invoke-AdbSafe -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds
        $text = $result.Text
        if (-not $text) {
            $text = "exit_code=$($result.ExitCode)"
        }
        $text | Set-Content -Encoding UTF8 (Join-Path $stage $Name)
    }
    catch {
        ("diagnostic collection failed: " + $_.Exception.Message) |
            Set-Content -Encoding UTF8 (Join-Path $stage $Name)
    }
}

function Test-PrivateIPv4 {
    param([string]$Address)
    if (-not $Address) { return $false }
    if ($Address -match "^10\.") { return $true }
    if ($Address -match "^192\.168\.") { return $true }
    if ($Address -match "^172\.(1[6-9]|2[0-9]|3[0-1])\.") { return $true }
    return $false
}

function Get-WsaEndpointCandidates {
    param([string]$ExplicitSerial)

    $records = New-Object System.Collections.ArrayList
    $seen = @{}

    function Add-WsaCandidate {
        param([string]$Endpoint, [string]$Source)
        if (-not $Endpoint) { return }
        $key = $Endpoint.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { return }
        $seen[$key] = $true
        [void]$records.Add([pscustomobject]@{
            endpoint = $Endpoint
            source = $Source
        })
    }

    Add-WsaCandidate -Endpoint $ExplicitSerial -Source "explicit"
    Add-WsaCandidate -Endpoint "127.0.0.1:58526" -Source "localhost-default"

    try {
        $devices = Invoke-AdbSafe -Arguments @("devices") -TimeoutSeconds 5
        foreach ($line in (($devices.Stdout -split "`r?`n") | Where-Object { $_ -match "\t(device|unauthorized|offline)$" })) {
            $deviceSerial = ($line -split "\s+")[0]
            Add-WsaCandidate -Endpoint $deviceSerial -Source "adb-devices"
        }
    }
    catch {}

    try {
        $mdns = Invoke-AdbSafe -Arguments @("mdns", "services") -TimeoutSeconds 5
        foreach ($m in [regex]::Matches([string]$mdns.Text, "(?<!\d)((?:\d{1,3}\.){3}\d{1,3}:\d+)")) {
            Add-WsaCandidate -Endpoint $m.Groups[1].Value -Source "adb-mdns"
        }
    }
    catch {}

    try {
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
            $_.IPAddress -and
            $_.IPAddress -ne "127.0.0.1" -and
            -not $_.IPAddress.StartsWith("169.254.")
        } | ForEach-Object {
            Add-WsaCandidate -Endpoint ($_.IPAddress + ":58526") -Source ("host-ip:" + $_.InterfaceAlias)
        }
    }
    catch {}

    $guestIps = New-Object System.Collections.Generic.HashSet[string]
    try {
        if (Get-Command Get-HnsEndpoint -ErrorAction SilentlyContinue) {
            $hnsJson = (Get-HnsEndpoint | ConvertTo-Json -Depth 20)
            foreach ($m in [regex]::Matches([string]$hnsJson, "(?<!\d)((?:\d{1,3}\.){3}\d{1,3})(?!\d)")) {
                $ip = $m.Groups[1].Value
                if (Test-PrivateIPv4 -Address $ip) { [void]$guestIps.Add($ip) }
            }
        }
    }
    catch {}

    try {
        $hnsdiag = Get-Command hnsdiag.exe -ErrorAction SilentlyContinue
        if ($hnsdiag) {
            $hnsText = (& $hnsdiag.Source list endpoints 2>&1 | Out-String)
            foreach ($m in [regex]::Matches([string]$hnsText, "(?<!\d)((?:\d{1,3}\.){3}\d{1,3})(?!\d)")) {
                $ip = $m.Groups[1].Value
                if (Test-PrivateIPv4 -Address $ip) { [void]$guestIps.Add($ip) }
            }
        }
    }
    catch {}

    try {
        Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
            $_.InterfaceAlias -match "(?i)WSA|vEthernet|Hyper-V" -and
            (Test-PrivateIPv4 -Address $_.IPAddress)
        } | ForEach-Object {
            [void]$guestIps.Add($_.IPAddress)
        }
    }
    catch {}

    foreach ($ip in $guestIps) {
        Add-WsaCandidate -Endpoint ($ip + ":5555") -Source "guest-ip:5555"
        Add-WsaCandidate -Endpoint ($ip + ":58526") -Source "guest-ip:58526"
    }

    return @($records)
}

function Invoke-WsaCandidateProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$Source
    )

    $tcpOpen = $null
    if ($Endpoint -match "^([^:]+):(\d+)$") {
        $tcpOpen = Test-TcpEndpoint -HostName $matches[1] -Port ([int]$matches[2]) -TimeoutMs 700
    }

    $alreadyKnown = ($Source -eq "adb-devices")
    $connectResult = $null
    if ($alreadyKnown -or $null -eq $tcpOpen -or $tcpOpen) {
        $connectResult = Invoke-AdbSafe -Arguments @("connect", $Endpoint) -TimeoutSeconds 6
    }
    else {
        $connectResult = [pscustomobject]@{
            ExitCode = 0
            Stdout = ""
            Stderr = ""
            Text = "tcp_closed"
            TimedOut = $false
        }
    }

    $stateResult = Invoke-AdbSafe -Arguments @("-s", $Endpoint, "get-state") -TimeoutSeconds 5
    $stateText = ([string]$stateResult.Stdout).Trim()
    $model = ""
    $boot = ""
    if ($stateText -eq "device") {
        try {
            $model = ([string](Invoke-AdbSafe -Arguments @("-s", $Endpoint, "shell", "getprop", "ro.product.model") -TimeoutSeconds 5).Stdout).Trim()
            $boot = ([string](Invoke-AdbSafe -Arguments @("-s", $Endpoint, "shell", "getprop", "sys.boot_completed") -TimeoutSeconds 5).Stdout).Trim()
        }
        catch {}
    }

    $isWsa = ($stateText -eq "device" -and $model -eq "Subsystem for Android(TM)")
    return [pscustomobject]@{
        endpoint = $Endpoint
        source = $Source
        tcp_open = $tcpOpen
        connect_exit = $connectResult.ExitCode
        connect = $connectResult.Text
        state_exit = $stateResult.ExitCode
        state = $stateText
        state_error = $stateResult.Stderr
        model = $model
        boot_completed = $boot
        is_wsa = $isWsa
    }
}

Start-Sleep -Seconds 8

if (($PairEndpoint -and -not $PairCode) -or ($PairCode -and -not $PairEndpoint)) {
    Finish-Report -State "PAIRING_ARGUMENTS_INCOMPLETE" -ExitCode 22 -Extra @{
        pair_endpoint = $PairEndpoint
    }
    throw "Both -PairEndpoint and -PairCode are required for one-time Android pairing."
}
if ($PairEndpoint -and $PairCode) {
    Write-Log "Attempting one-time Android control-channel pairing at $PairEndpoint."
    $pairResult = Invoke-AdbSafe -Arguments @("pair", $PairEndpoint, $PairCode)
    $pairResult.Text | Set-Content -Encoding UTF8 (Join-Path $stage "android-pair.txt")
    if ($pairResult.Text -notmatch "(?i)success") {
        Finish-Report -State "ANDROID_PAIR_FAILED" -ExitCode 23 -Extra @{
            pair_endpoint = $PairEndpoint
            pair_result = $pairResult.Text
            pair_exit_code = $pairResult.ExitCode
        }
        throw "Android pairing failed. Check the pairing endpoint/code shown by the subsystem."
    }
    Write-Log "Android pairing completed."
}

$attempts = @()
$runtimeSnapshots = @()
$serialCandidateMap = @{}
$onlineSerial = $null
$probeStartedAt = Get-Date
$connectDeadline = $probeStartedAt.AddMinutes(3)
$round = 0
$runtimeEverSeen = $false
$runtimeRecycled = $false
$developerFallbackAttempted = $false
$developerFallbackBackup = $null

while (-not $onlineSerial -and (Get-Date) -lt $connectDeadline) {
    $round++
    $snapshot = Get-WsaRuntimeSnapshot
    $runtimeSnapshots += $snapshot
    if ($snapshot.runtime_alive) {
        $runtimeEverSeen = $true
    }

    $candidates = @(Get-WsaEndpointCandidates -ExplicitSerial $Serial)
    foreach ($candidateRecord in $candidates) {
        $candidate = [string]$candidateRecord.endpoint
        $source = [string]$candidateRecord.source
        if (-not $serialCandidateMap.ContainsKey($candidate)) {
            $serialCandidateMap[$candidate] = $source
        }

        $probe = Invoke-WsaCandidateProbe -Endpoint $candidate -Source $source
        $attempts += [ordered]@{
            round = $round
            timestamp = (Get-Date).ToString("o")
            serial = $candidate
            source = $source
            tcp_open = $probe.tcp_open
            connect_exit = $probe.connect_exit
            connect = $probe.connect
            state_exit = $probe.state_exit
            state = $probe.state
            state_error = $probe.state_error
            model = $probe.model
            boot_completed = $probe.boot_completed
            is_wsa = $probe.is_wsa
        }

        if ($probe.state -eq "unauthorized" -or
            $probe.state_error -match "(?i)unauthorized|authenticate" -or
            $probe.connect -match "(?i)unauthorized|failed to authenticate") {
            Write-Log "Android endpoint $candidate is reachable but awaiting ADB host-key authorization."
        }

        if ($probe.state -eq "device" -and -not $probe.is_wsa) {
            Write-Log "Ignoring Android endpoint $candidate from $source because model '$($probe.model)' is not WSA."
            continue
        }

        if ($probe.is_wsa) {
            if ($probe.boot_completed -eq "1") {
                $onlineSerial = $candidate
                Write-Log "WSA control channel accepted on $candidate from $source (model=$($probe.model), boot_completed=1)."
                break
            }
            Write-Log "WSA endpoint $candidate is connected but Android boot is not complete yet."
        }
    }

    if ($onlineSerial) { break }

    $elapsed = ((Get-Date) - $probeStartedAt).TotalSeconds

    if (-not $runtimeEverSeen -and -not $packagedActivationRetried -and $elapsed -ge 15) {
        $packagedActivationRetried = $true
        Write-Log "No WSA runtime process is visible after initial package activation; retrying packaged AUMID launch."
        if (Invoke-WsaPackagedWake) {
            $packagedActivationSucceeded = $true
        }
    }
    elseif ($runtimeEverSeen -and -not $developerDeepLinkAttempted -and $elapsed -ge 25) {
        $developerDeepLinkAttempted = $true
        Write-Log "WSA runtime is alive but ADB is not exposed yet; requesting Developer settings through packaged WsaClient activation."
        if (Invoke-WsaPackagedWake -DeveloperSettings) {
            $packagedActivationSucceeded = $true
        }
    }
    elseif (-not $developerFallbackAttempted -and -not $NoAutoDeveloperModePatch -and $elapsed -ge 45) {
        $developerFallbackAttempted = $true
        Write-Log "ADB is still unavailable after runtime/network discovery; applying one reversible Developer-mode repair before the final packaged relaunch."
        $developerFallbackBackup = Enable-DeveloperModeFallback -ReportStage $stage
        if ($developerFallbackBackup) {
            if (Invoke-WsaPackagedWake -DeveloperSettings) {
                $packagedActivationSucceeded = $true
            }
            elseif (-not $upstreamLaunchRequested) {
                try {
                    Start-Process "wsa://com.android.settings" -ErrorAction SilentlyContinue | Out-Null
                    $upstreamLaunchRequested = $true
                }
                catch {}
            }
        }
    }
    elseif (-not $runtimeRecycled -and $elapsed -ge 120) {
        $runtimeRecycled = $true
        Write-Log "All non-destructive startup routes were exhausted; performing the single allowed WSA recycle."
        Stop-Process -Name "WsaSettings","WsaClient","WindowsSubsystemForAndroid","WsaService","vmmemWSA" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 4
        if (Invoke-WsaPackagedWake) {
            $packagedActivationSucceeded = $true
        }
        elseif (-not $upstreamLaunchRequested) {
            try {
                Start-Process "wsa://com.android.settings" -ErrorAction SilentlyContinue | Out-Null
                $upstreamLaunchRequested = $true
            }
            catch {}
        }
    }

    Start-Sleep -Seconds 5
}

$serialCandidates = @($serialCandidateMap.Keys)
$runtimeSnapshots | ConvertTo-Json -Depth 9 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-runtime-snapshots.json")
$attempts | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $stage "android-connect-attempts.json")
$portNetstat = ""
$excludedRanges = ""
try {
    $portNetstat = (& netstat.exe -ano | Select-String -Pattern "58526" | Out-String)
    $portNetstat | Set-Content -Encoding UTF8 (Join-Path $stage "port-58526.txt")
}
catch {}
try {
    $excludedRanges = (& netsh.exe interface ipv4 show excludedportrange protocol=tcp 2>&1 | Out-String)
    $excludedRanges | Set-Content -Encoding UTF8 (Join-Path $stage "excluded-tcp-ranges.txt")
}
catch {}

Save-WsaHostDiagnostics -ReportStage $stage -Since $p0StartedAt

if (-not $onlineSerial) {
    $unauthorized = [bool](@($attempts | Where-Object {
        $_.state -eq "unauthorized" -or
        $_.state_error -match "(?i)unauthorized|authenticate" -or
        $_.connect -match "(?i)unauthorized|failed to authenticate"
    }).Count)

    if ($unauthorized) {
        Finish-Report -State "ANDROID_AUTHORIZATION_REQUIRED" -ExitCode 24 -Extra @{
            installed_version = $installed.Version.ToString()
            attempted_serials = @($serialCandidates)
            authorization_required = $true
            port_reservation_ok = $portReservationOk
            loopback_exemption_ok = $loopbackExemptionOk
        }
        Write-Host ""
        Write-Host "WSA is reachable, but ADB authorization is pending. Approve the debugging prompt and rerun the same command."
        exit 24
    }

    if ($developerFallbackBackup -and (Test-Path $developerFallbackBackup)) {
        $settingsPath = Join-Path $env:LOCALAPPDATA "Packages\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe\Settings\settings.dat"
        Stop-Process -Name "WsaClient","WindowsSubsystemForAndroid","WsaService","vmmemWSA" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        try {
            Copy-Item -LiteralPath $developerFallbackBackup -Destination $settingsPath -Force
            Write-Log "Developer-mode repair did not recover ADB; original settings were restored."
        }
        catch {
            Write-Log "WARNING: could not restore original WSA settings automatically: $($_.Exception.Message)"
        }
    }

    $finalSnapshot = Get-WsaRuntimeSnapshot
    $runtimeAliveNow = [bool]$finalSnapshot.runtime_alive
    $tcpOpenAny = [bool](@($attempts | Where-Object { $_.tcp_open -eq $true }).Count)
    $foreignAndroidSeen = [bool](@($attempts | Where-Object { $_.state -eq "device" -and $_.is_wsa -ne $true }).Count)
    $refused = [bool](@($attempts | Where-Object {
        $_.connect -match "10061|actively refused|отверг" -or $_.connect -eq "tcp_closed"
    }).Count)

    $clientCrashSeen = $false
    $applicationEventsPath = Join-Path $stage "wsa-application-events.txt"
    if (Test-Path $applicationEventsPath) {
        try {
            $eventText = Get-Content -LiteralPath $applicationEventsPath -Raw
            $clientCrashSeen = [bool]($eventText -match "(?i)WsaClient\.exe" -and $eventText -match "(?i)fault|crash|сбой|exception|0xc000")
        }
        catch {}
    }

    $state = "ANDROID_CONTROL_CHANNEL_OFFLINE"
    if ($clientCrashSeen) {
        $state = "WSA_CLIENT_CRASHED"
    }
    elseif (-not $packagedActivationSucceeded -and -not $runtimeEverSeen -and -not $runtimeAliveNow) {
        $state = "WSA_PACKAGE_ACTIVATION_FAILED"
    }
    elseif (-not $runtimeEverSeen -and -not $runtimeAliveNow) {
        $state = "WSA_RUNTIME_NOT_STARTED"
    }
    elseif (($runtimeEverSeen -or $runtimeAliveNow) -and -not $tcpOpenAny) {
        $state = "WSA_ADB_NOT_EXPOSED"
    }
    elseif ($refused) {
        $state = "ANDROID_CONTROL_CHANNEL_REFUSED"
    }

    Finish-Report -State $state -ExitCode 20 -Extra @{
        installed_version = $installed.Version.ToString()
        attempted_serials = @($serialCandidates)
        pair_attempted = [bool]($PairEndpoint -and $PairCode)
        port_58526_refused = $refused
        port_reservation_ok = $portReservationOk
        loopback_exemption_ok = $loopbackExemptionOk
        runtime_ever_seen = $runtimeEverSeen
        runtime_alive_final = $runtimeAliveNow
        tcp_endpoint_seen = $tcpOpenAny
        foreign_android_seen = $foreignAndroidSeen
        developer_repair_attempted = $developerFallbackAttempted
        packaged_activation_attempted = $packagedActivationAttempted
        packaged_activation_succeeded = $packagedActivationSucceeded
        packaged_activation_retried = $packagedActivationRetried
        raw_wsaclient_launch_attempted = $false
        client_crash_seen = $clientCrashSeen
        upstream_uri_launch_requested = $upstreamLaunchRequested
    }
    Write-Host ""
    Write-Host "TUGARIN BOTS P0 stopped at state: $state"
    Write-Host "The report now contains host runtime, HNS, adapter, event-log and WSA diagnostics for the exact failed layer."
    exit 20
}

$Serial = $onlineSerial
Set-Content -Encoding UTF8 -Path (Join-Path $stage "selected-serial.txt") -Value $Serial
Write-Log "Android control channel is online at $Serial."

$props = @(
    "ro.build.version.release",
    "ro.product.cpu.abi",
    "ro.product.cpu.abilist",
    "ro.dalvik.vm.native.bridge",
    "ro.product.model",
    "sys.boot_completed"
)
$propLines = @()
foreach ($prop in $props) {
    $probe = Invoke-AdbSafe -Arguments @("-s", $Serial, "shell", "getprop", $prop) -TimeoutSeconds 10
    $value = $probe.Stdout.Trim()
    $propLines += "$prop=$value"
}
$propLines | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-properties.txt")

$bootstrapArgs = @(
    ".\warbot_cli.py",
    "bootstrap",
    "--backend", "wsa",
    "--serial", $Serial,
    "--output", (Join-Path $stage "wsa-bootstrap.png"),
    "--game-stability-seconds", "120"
)
if ($CleanGame) { $bootstrapArgs += "--clean-game" }

Write-Log "Installing/launching Kingshot through WSA backend."
$bootstrapExit = 999
$bootstrapLog = Join-Path $stage "wsa-bootstrap.txt"
$bootstrapErr = Join-Path $stage "wsa-bootstrap-stderr.txt"
$dedicatedPython = Join-Path $WorkRoot "tugarin-venv\Scripts\python.exe"
$pythonExe = if (Test-Path -LiteralPath $dedicatedPython -PathType Leaf) {
    $dedicatedPython
}
else {
    (Get-Command python.exe -ErrorAction Stop).Source
}
try {
    # Windows PowerShell 5.1 converts native stderr into PowerShell ErrorRecords
    # when ErrorActionPreference=Stop. Run Python through Start-Process so the
    # full traceback and the real native exit code are always preserved.
    $argLine = @($bootstrapArgs | ForEach-Object {
        $v = [string]$_
        if ($v -match '[\s"]') {
            '"' + ($v -replace '"', '\"') + '"'
        }
        else { $v }
    }) -join ' '

    $proc = Start-Process -FilePath $pythonExe -ArgumentList $argLine -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $bootstrapLog -RedirectStandardError $bootstrapErr
    $bootstrapExit = [int]$proc.ExitCode

    if (Test-Path $bootstrapLog) {
        Get-Content -LiteralPath $bootstrapLog | Write-Host
    }
    if (Test-Path $bootstrapErr) {
        $stderrText = Get-Content -LiteralPath $bootstrapErr -Raw
        if ($stderrText) { Write-Host $stderrText }
    }
}
catch {
    $bootstrapExit = 998
    ($_ | Out-String) | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-bootstrap-exception.txt")
}

Save-AdbDiagnostic -Name "wsa-crash-buffer.txt" -Arguments @("-s", $Serial, "logcat", "-b", "crash", "-d", "-v", "threadtime") -TimeoutSeconds 15
Save-AdbDiagnostic -Name "wsa-logcat-tail.txt" -Arguments @("-s", $Serial, "logcat", "-d", "-t", "2500", "-v", "threadtime") -TimeoutSeconds 20
Save-AdbDiagnostic -Name "wsa-package-path.txt" -Arguments @("-s", $Serial, "shell", "pm", "path", "com.got.globalru") -TimeoutSeconds 10
Save-AdbDiagnostic -Name "wsa-game-pid.txt" -Arguments @("-s", $Serial, "shell", "pidof", "com.got.globalru") -TimeoutSeconds 10
Save-AdbDiagnostic -Name "wsa-connectivity.txt" -Arguments @("-s", $Serial, "shell", "dumpsys", "connectivity") -TimeoutSeconds 15
Save-AdbDiagnostic -Name "wsa-audio.txt" -Arguments @("-s", $Serial, "shell", "dumpsys", "audio") -TimeoutSeconds 15
Save-AdbDiagnostic -Name "wsa-processes.txt" -Arguments @("-s", $Serial, "shell", "dumpsys", "activity", "processes") -TimeoutSeconds 15
try {
    $healthOut = Join-Path $stage "wsa-health.json"
    $healthErr = Join-Path $stage "wsa-health-stderr.txt"
    $healthArgs = '.\warbot_cli.py status --backend wsa --serial "' + $Serial + '"'
    Start-Process -FilePath $pythonExe -ArgumentList $healthArgs -Wait -WindowStyle Hidden `
        -RedirectStandardOutput $healthOut -RedirectStandardError $healthErr
}
catch {}
if (-not (Test-Path (Join-Path $stage "wsa-bootstrap.png"))) {
    try {
        $failureFrame = Join-Path $stage "wsa-failure-frame.png"
        $failureText = Join-Path $stage "wsa-failure-frame.txt"
        $failureErr = Join-Path $stage "wsa-failure-frame-stderr.txt"
        $failureArgs = '.\warbot_cli.py screenshot --backend wsa --serial "' + $Serial + '" --output "' + $failureFrame + '"'
        Start-Process -FilePath $pythonExe -ArgumentList $failureArgs -Wait -WindowStyle Hidden `
            -RedirectStandardOutput $failureText -RedirectStandardError $failureErr
    }
    catch {}
}

if ($bootstrapExit -eq 0) {
    Finish-Report -State "WSA_GAME_PASS" -ExitCode 0 -Extra @{
        installed_version = $installed.Version.ToString()
        bootstrap_exit = $bootstrapExit
    }
    exit 0
}

Finish-Report -State "WSA_GAME_FAIL" -ExitCode $bootstrapExit -Extra @{
    installed_version = $installed.Version.ToString()
    bootstrap_exit = $bootstrapExit
}
exit $bootstrapExit
