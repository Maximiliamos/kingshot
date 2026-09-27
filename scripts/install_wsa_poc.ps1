param(
    [string]$Serial = "127.0.0.1:58526",
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
    if ($CleanGame) { $args += "-CleanGame" }
    if ($SkipInstall) { $args += "-SkipInstall" }

    $quoted = $args | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }
    Start-Process powershell.exe -Verb RunAs -ArgumentList ($quoted -join " ")
}

if (-not (Test-IsAdmin)) {
    Write-Host "Administrator rights are required for WSA installation."
    Write-Host "Requesting elevation..."
    Invoke-SelfElevated
    exit 0
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

$featureNames = @("VirtualMachinePlatform", "HypervisorPlatform")
$featureState = @{}
$needsReboot = $false
foreach ($feature in $featureNames) {
    $current = Get-WindowsOptionalFeature -Online -FeatureName $feature
    $featureState[$feature] = $current.State.ToString()
    if ($current.State -ne "Enabled") {
        Write-Log "Enabling Windows feature: $feature"
        $result = Enable-WindowsOptionalFeature -Online -FeatureName $feature -All -NoRestart
        if ($result.RestartNeeded) {
            $needsReboot = $true
        }
    }
}
$featureState | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 (Join-Path $stage "windows-features-before.json")

$bcd = (& bcdedit /enum "{current}" 2>&1 | Out-String)
$bcd | Set-Content -Encoding UTF8 (Join-Path $stage "bcd-current.txt")
if ($bcd -match "hypervisorlaunchtype\s+Off") {
    Write-Log "Enabling Hyper-V hypervisor launch at boot."
    & bcdedit /set hypervisorlaunchtype auto | Out-Null
    $needsReboot = $true
}

if ($needsReboot) {
    Finish-Report -State "NEEDS_REBOOT" -ExitCode 3010 -Extra @{ host = $hostInfo }
    Write-Host ""
    Write-Host "Windows virtualization components were enabled."
    Write-Host "Restart Windows, then run the SAME command again:"
    Write-Host "cd C:\warbot_git"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\install_wsa_poc.ps1"
    exit 3010
}

if (-not $SkipInstall) {
    $existing = Get-AppxPackage -AllUsers | Where-Object {
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
            $hash = (Get-FileHash -Algorithm SHA256 $ArchivePath).Hash.ToLowerInvariant()
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

        $hash = (Get-FileHash -Algorithm SHA256 $ArchivePath).Hash.ToLowerInvariant()
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

        Write-Log "Registering Windows Subsystem for Android."
        Push-Location $packageDir
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript 2>&1 | Tee-Object -FilePath (Join-Path $stage "wsa-install.txt") | Write-Host
            $installExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        if ($installExit -ne 0) {
            Finish-Report -State "WSA_INSTALL_FAILED" -ExitCode $installExit
            throw "WSABuilds Install.ps1 failed with exit code $installExit."
        }
    }
}

$installed = Get-AppxPackage -AllUsers | Where-Object {
    $_.Name -like "*WindowsSubsystemForAndroid*"
} | Select-Object -First 1
if (-not $installed) {
    Finish-Report -State "WSA_NOT_REGISTERED" -ExitCode 18
    throw "WSA package is still not registered after installation."
}
$installed | Select-Object Name, PackageFullName, Version, InstallLocation | Format-List | Out-String | Set-Content -Encoding UTF8 (Join-Path $stage "installed-wsa.txt")

Write-Log "Launching WSA Settings to wake the subsystem."
try {
    Start-Process explorer.exe "shell:AppsFolder\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe!SettingsApp"
}
catch {
    Write-Log "Could not launch WSA Settings automatically: $($_.Exception.Message)"
}

$adb = "C:\Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path $adb)) {
    Finish-Report -State "ADB_MISSING" -ExitCode 19
    throw "ADB not found at $adb."
}

Start-Sleep -Seconds 12
$connect = (& $adb connect $Serial 2>&1 | Out-String).Trim()
Set-Content -Encoding UTF8 -Path (Join-Path $stage "adb-connect.txt") -Value $connect
Write-Log "ADB connect result: $connect"

$state = (& $adb -s $Serial get-state 2>&1 | Out-String).Trim()
Set-Content -Encoding UTF8 -Path (Join-Path $stage "adb-state.txt") -Value $state
if ($state -ne "device") {
    Finish-Report -State "NEEDS_WSA_DEVELOPER_MODE" -ExitCode 20 -Extra @{
        adb_connect = $connect
        adb_state = $state
        installed_version = $installed.Version.ToString()
    }
    Write-Host ""
    Write-Host "WSA is installed. One Windows UI permission remains:"
    Write-Host "1. In the WSA Settings window open Advanced settings."
    Write-Host "2. Enable Developer mode."
    Write-Host "3. If WSA shows a different ADB IP:port, rerun with -Serial IP:PORT."
    Write-Host "4. Otherwise rerun the SAME command; default is $Serial."
    exit 20
}

Write-Log "WSA ADB transport is online."

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
& $adb -s $Serial shell pm path com.got.globalru 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-package-path.txt")
& $adb -s $Serial shell pidof com.got.globalru 2>&1 | Set-Content -Encoding UTF8 (Join-Path $stage "wsa-game-pid.txt")

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
