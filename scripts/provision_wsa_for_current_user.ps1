param(
    [switch]$AdminPhase
)

$ErrorActionPreference = "Stop"

$PackageDir = "C:\warbot_wsa\WSA_LTS8_Windows10\WSA_2407.40000.4.0_x64"
$Manifest = Join-Path $PackageDir "AppxManifest.xml"
$PackageName = "MicrosoftCorporationII.WindowsSubsystemForAndroid"
$PackageFamily = "MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Path $Manifest)) {
    throw "WSA package manifest was not found: $Manifest"
}

if ($AdminPhase) {
    if (-not (Test-IsAdmin)) {
        throw "WSA provisioning phase requires administrator rights."
    }

    # Remove only registrations backed by WAR BOT's own installation folder.
    # This avoids the unpackaged-per-user conflict before DISM provisioning.
    $registrations = Get-AppxPackage -AllUsers -Name $PackageName | Where-Object {
        $_.InstallLocation -like "C:\warbot_wsa\*"
    }
    foreach ($registration in $registrations) {
        Write-Host "Removing per-user WAR BOT WSA registration: $($registration.PackageFullName)"
        Remove-AppxPackage -Package $registration.PackageFullName -AllUsers -ErrorAction Stop
    }

    Write-Host "Provisioning WSA for Windows users through DISM..."
    & dism.exe /Online /Add-ProvisionedAppxPackage "/FolderPath:$PackageDir" /SkipLicense /Region:all
    if ($LASTEXITCODE -ne 0) {
        throw "DISM WSA provisioning failed with exit code $LASTEXITCODE."
    }
    exit 0
}

if (-not (Test-IsAdmin)) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-AdminPhase"
    )
    $quoted = $arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }
    $process = Start-Process powershell.exe -Verb RunAs -PassThru -Wait -ArgumentList ($quoted -join " ")
    if ($null -eq $process -or $process.ExitCode -ne 0) {
        $code = if ($null -eq $process) { 98 } else { $process.ExitCode }
        throw "Administrator WSA provisioning failed with exit code $code."
    }
}

Write-Host "Registering provisioned WSA for the interactive user..."
Add-AppxPackage -RegisterByFamilyName -MainPackage $PackageFamily -ForceApplicationShutdown -ErrorAction Stop

$installed = Get-AppxPackage -Name $PackageName | Select-Object -First 1
if ($null -eq $installed) {
    throw "WSA provisioning completed, but the package is not registered for the interactive user."
}

$installed | Select-Object Name, PackageFullName, Version, InstallLocation | Format-List
Start-Process explorer.exe "shell:AppsFolder\$PackageFamily!SettingsApp"
Write-Host "WSA_CURRENT_USER_PASS"
