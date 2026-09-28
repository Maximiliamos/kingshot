param(
    [string]$Serial = "127.0.0.1:58526",
    [string]$PairEndpoint = "",
    [string]$PairCode = "",
    [switch]$CleanGame,
    [switch]$SkipInstall
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

    $quoted = $args | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }
    $proc = Start-Process powershell.exe -Verb RunAs -PassThru -Wait -ArgumentList ($quoted -join " ")
    if ($null -eq $proc) {
        return 98
    }
    return $proc.ExitCode
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
if (-not (Test-IsAdmin)) {
    Write-Host "Administrator rights are required for the Android subsystem setup."
    Write-Host "Requesting elevation..."
    $childExit = Invoke-SelfElevated
    exit $childExit
}

New-Item -ItemType Directory -Force -Path $WorkRoot, $DownloadRoot, $RuntimeRoot | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = Join-Path $env:TEMP ("warbot-wsa-report-" + $stamp + "-" + $PID)
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$manifestPath = Join-Path $stage "manifest.json"
$consolePath = Join-Path $stage "console.txt"

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
    $commit = (& git rev-parse HEAD).Trim()
    $payload = [ordered]@{
        schema = 1
        kind = "wsa-poc"
        state = $State
        exit_code = $ExitCode
        commit = $commit
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
    Upload-Report
    Write-Host ""
    Write-Host "WSA PoC state: $State"
    Write-Host "Local report: $stage"
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
    $ReleaseTag = "Windows_11_2407.40000.4.0_LTS_8"
    $ArchiveName = "WSA_2407.40000.4.0_x64_Release-Nightly-NoGApps-NoAmazon.7z"
    $ArchiveSha256 = "9c51759762f14cdebde7da08ccf94deb220484215468526e1ef688fd669ab7c1"
    $InstallRoot = Join-Path $WorkRoot "WSA_LTS8_Windows11"
    Write-Log "Selected WSABuilds LTS 8 package for Windows 11 ($fullBuild)."
}
elseif ($build -eq 19045 -and $ubr -ge 2311) {
    $ReleaseTag = "Windows_10_2407.40000.4.0_LTS_8"
    $ArchiveName = "WSA_2407.40000.4.0_x64_Release-Nightly-NoGApps-NoAmazon_Windows_10.7z"
    $ArchiveSha256 = "366c344eee70e610e905c7588f661ce028faef8ae55ec9cc6c8dd348ec2cb7c8"
    $InstallRoot = Join-Path $WorkRoot "WSA_LTS8_Windows10"
    Write-Log "Selected WSABuilds LTS 8 package for Windows 10 22H2 ($fullBuild)."
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

& reg.exe add "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" /t REG_DWORD /f /v "AllowDevelopmentWithoutDevLicense" /d "1" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to enable Windows developer package registration."
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

if (-not $SkipInstall) {
    $existing = Get-AppxPackage | Where-Object {
        $_.Name -like "*WindowsSubsystemForAndroid*"
    } | Select-Object -First 1

    if ($existing) {
        $existing | Select-Object Name, PackageFullName, Version, InstallLocation | Format-List | Out-String | Set-Content -Encoding UTF8 (Join-Path $stage "existing-wsa.txt")

        if (-not ($existing.InstallLocation -like "$InstallRoot*")) {
            Finish-Report -State "EXISTING_WSA_CONFLICT" -ExitCode 13 -Extra @{
                existing_package = $existing.PackageFullName
                existing_location = $existing.InstallLocation
            }
            throw ("Another WSA installation already exists at '$($existing.InstallLocation)'. WAR BOT will not uninstall or overwrite it automatically.")
        }
        Write-Log "WAR BOT WSA package is already registered; keeping it."
    }
    else {
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
            Write-Log "Downloading WSABuilds LTS 8 NoGApps/NoAmazon package for this Windows build (~556 MB)."
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

            $wsaClient = Get-Command WsaClient.exe -ErrorAction SilentlyContinue
            if ($wsaClient) {
                try {
                    Start-Process $wsaClient.Source -Wait -ArgumentList "/shutdown" -ErrorAction SilentlyContinue
                }
                catch {}
            }
            Stop-Process -Name "WsaClient" -Force -ErrorAction SilentlyContinue

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
if (-not $installed) {
    Finish-Report -State "WSA_NOT_REGISTERED" -ExitCode 18
    throw "WSA package is still not registered after installation."
}
$installed | Select-Object Name, PackageFullName, Version, InstallLocation | Format-List | Out-String | Set-Content -Encoding UTF8 (Join-Path $stage "installed-wsa.txt")

Write-Log "Launching Android subsystem settings and waking the Android environment."
try {
    Start-Process explorer.exe "shell:AppsFolder\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe!SettingsApp"
}
catch {
    Write-Log "Could not launch subsystem Settings automatically: $($_.Exception.Message)"
}

$adb = "C:\Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path $adb)) {
    Finish-Report -State "ADB_MISSING" -ExitCode 19
    throw "Android control tool not found at $adb."
}

$client = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe\WsaClient.exe"
if (Test-Path $client) {
    try {
        Start-Process -FilePath $client -ArgumentList "/launch", "wsa://com.android.settings" -ErrorAction SilentlyContinue
        Write-Log "Requested Android Settings launch to wake the Android environment."
    }
    catch {
        Write-Log "Android Settings wake request was not available: $($_.Exception.Message)"
    }
    try {
        Start-Process -FilePath $client -ArgumentList "/deeplink", "wsa-client://developer-settings" -ErrorAction SilentlyContinue
        Write-Log "Opened subsystem developer settings for the P0 control-channel gate."
    }
    catch {
        Write-Log "Developer-settings deep link was not available: $($_.Exception.Message)"
    }
}
Write-Host ""
Write-Host "P0 control-channel gate: if Developer mode is OFF in the opened subsystem settings, turn it ON now."
Write-Host "The verifier will keep retrying automatically while the settings window is open."

function Invoke-AdbSafe {
    param([string[]]$Arguments)

    $token = [Guid]::NewGuid().ToString("N")
    $stdoutPath = Join-Path $env:TEMP ("warbot-android-out-" + $token + ".txt")
    $stderrPath = Join-Path $env:TEMP ("warbot-android-err-" + $token + ".txt")
    try {
        $proc = Start-Process -FilePath $adb -ArgumentList $Arguments -NoNewWindow -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
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
        return [pscustomobject]@{
            ExitCode = $proc.ExitCode
            Stdout = $stdout
            Stderr = $stderr
            Text = $combined
        }
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $stdoutPath, $stderrPath
    }
}

Start-Sleep -Seconds 20

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
    if ($pairResult.ExitCode -ne 0 -or $pairResult.Text -notmatch "(?i)success") {
        Finish-Report -State "ANDROID_PAIR_FAILED" -ExitCode 23 -Extra @{
            pair_endpoint = $PairEndpoint
            pair_result = $pairResult.Text
        }
        throw "Android pairing failed. Check the pairing endpoint/code shown by the subsystem."
    }
    Write-Log "Android pairing completed."
}

$serialCandidates = New-Object System.Collections.Generic.List[string]
$serialCandidates.Add($Serial)

if ($Serial -match ":58526$") {
    try {
        $routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop | Sort-Object RouteMetric, InterfaceMetric
        foreach ($route in $routes) {
            $addresses = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue | Where-Object {
                $_.IPAddress -and
                $_.IPAddress -ne "127.0.0.1" -and
                -not $_.IPAddress.StartsWith("169.254.")
            }
            foreach ($address in $addresses) {
                $candidate = "$($address.IPAddress):58526"
                if (-not $serialCandidates.Contains($candidate)) {
                    $serialCandidates.Add($candidate)
                }
            }
        }
    }
    catch {
        Write-Log "Could not enumerate alternate host IPv4 endpoints: $($_.Exception.Message)"
    }
}

$attempts = @()
$onlineSerial = $null
$connectDeadline = (Get-Date).AddMinutes(4)
$round = 0
$runtimeRecycled = $false
while (-not $onlineSerial -and (Get-Date) -lt $connectDeadline) {
    $round++
    foreach ($candidate in $serialCandidates) {
        $connectResult = Invoke-AdbSafe -Arguments @("connect", $candidate)
        $stateResult = Invoke-AdbSafe -Arguments @("-s", $candidate, "get-state")
        $stateText = ([string]$stateResult.Stdout).Trim()
        $attempts += [ordered]@{
            round = $round
            serial = $candidate
            connect_exit = $connectResult.ExitCode
            connect = $connectResult.Text
            state_exit = $stateResult.ExitCode
            state = $stateText
            state_error = $stateResult.Stderr
        }
        Write-Log "Android control endpoint $candidate -> connect='$($connectResult.Text)' state='$stateText'."
        if ($stateResult.ExitCode -eq 0 -and $stateText -eq "device") {
            $onlineSerial = $candidate
            break
        }
    }

    # A correctly installed WSA can occasionally leave the localhost bridge
    # unbound after first launch. Recycle the subsystem once, then keep probing.
    if (-not $onlineSerial -and -not $runtimeRecycled -and $round -ge 6) {
        $runtimeRecycled = $true
        Write-Log "Control channel is still offline; recycling the Android subsystem once."
        if (Test-Path $client) {
            try {
                Start-Process -FilePath $client -ArgumentList "/shutdown" -Wait -ErrorAction SilentlyContinue
            }
            catch {}
        }
        Start-Sleep -Seconds 5
        try {
            Start-Process explorer.exe "shell:AppsFolder\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe!SettingsApp"
        }
        catch {}
        try {
            if (Test-Path $client) {
                Start-Process -FilePath $client -ArgumentList "/deeplink", "wsa-client://developer-settings" -ErrorAction SilentlyContinue
            }
        }
        catch {}
        Start-Sleep -Seconds 20
    }
    elseif (-not $onlineSerial) {
        Start-Sleep -Seconds 10
    }
}

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

if (-not $onlineSerial) {
    $refused = [bool](@($attempts | Where-Object { $_.connect -match "10061|actively refused|отверг" }).Count)
    $state = if ($refused) { "ANDROID_CONTROL_CHANNEL_REFUSED" } else { "ANDROID_CONTROL_CHANNEL_OFFLINE" }
    Finish-Report -State $state -ExitCode 20 -Extra @{
        installed_version = $installed.Version.ToString()
        attempted_serials = @($serialCandidates)
        pair_attempted = [bool]($PairEndpoint -and $PairCode)
        port_58526_refused = $refused
    }
    Write-Host ""
    Write-Host "The Android subsystem is installed successfully, but its local control channel is not online."
    Write-Host "Complete these P0 steps in the subsystem Settings:"
    Write-Host "1. Open Advanced settings and turn Developer mode ON."
    Write-Host "2. Open the developer/wireless-debugging section and note the pairing endpoint/code if shown."
    Write-Host "3. Pair once by rerunning with -PairEndpoint IP:PORT -PairCode CODE."
    Write-Host "4. Use -Serial IP:PORT if the connection endpoint shown by the subsystem is not $Serial."
    Write-Host "The report also contains excluded-tcp-ranges.txt for the known Windows/Hyper-V port-58526 issue."
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
    $value = (& $adb -s $Serial shell getprop $prop 2>&1 | Out-String).Trim()
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
try {
    & python @bootstrapArgs 2>&1 | Tee-Object -FilePath (Join-Path $stage "wsa-bootstrap.txt") | Write-Host
    $bootstrapExit = $LASTEXITCODE
}
catch {
    $bootstrapExit = 998
    ($_ | Out-String) | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-bootstrap-exception.txt")
}

& $adb -s $Serial logcat -b crash -d -v threadtime 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-crash-buffer.txt")
& $adb -s $Serial logcat -d -t 2500 -v threadtime 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-logcat-tail.txt")
& $adb -s $Serial shell pm path com.got.globalru 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-package-path.txt")
& $adb -s $Serial shell pidof com.got.globalru 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-game-pid.txt")
& $adb -s $Serial shell dumpsys connectivity 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-connectivity.txt")
& $adb -s $Serial shell dumpsys audio 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-audio.txt")
& $adb -s $Serial shell dumpsys activity processes 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-processes.txt")
try {
    & python .\warbot_cli.py status --backend wsa --serial $Serial 2>&1 |
        Set-Content -Encoding UTF8 (Join-Path $stage "wsa-health.json")
}
catch {}
if (-not (Test-Path (Join-Path $stage "wsa-bootstrap.png"))) {
    try {
        & python .\warbot_cli.py screenshot --backend wsa --serial $Serial --output (Join-Path $stage "wsa-failure-frame.png") 2>&1 |
            Set-Content -Encoding UTF8 (Join-Path $stage "wsa-failure-frame.txt")
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
