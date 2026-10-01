#requires -version 5.1
param(
    [string]$TargetUser = "Программист1",
    [string]$Branch = "feature/unified-android-backend",
    [switch]$NoLogoffPrompt,
    [switch]$SkipDataBackup
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSCommandPath
Set-Location $Root

Write-Host "=============================================================="
Write-Host "TUGARIN BOTS installer"
Write-Host "Repository: $Root"
Write-Host "Branch:     $Branch"
Write-Host "Runtime:    Windows Subsystem for Android (WSA)"
Write-Host "=============================================================="

if (-not (Test-Path -LiteralPath (Join-Path $Root ".git") -PathType Container)) {
    throw "Run the installer from the TUGARIN BOTS Git checkout."
}

$git = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $git) { $git = Get-Command git -ErrorAction SilentlyContinue }
if (-not $git) {
    throw "Git is required for safe update/report workflows."
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) {
    throw "Python 3.12+ is required."
}

$pythonVersion = (& $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')").Trim()
try {
    $parsed = [version]$pythonVersion
}
catch {
    throw "Could not determine Python version: $pythonVersion"
}
if ($parsed -lt [version]"3.12") {
    throw "Python 3.12+ is required; found $pythonVersion."
}

$setup = Join-Path $Root "scripts\setup_dedicated_wsa_user.ps1"
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) {
    throw "Dedicated WSA setup script is missing: $setup"
}

$args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $setup,
    "-TargetUser", $TargetUser,
    "-RepoRoot", $Root,
    "-Branch", $Branch
)
if ($NoLogoffPrompt) { $args += "-NoLogoffPrompt" }
if ($SkipDataBackup) { $args += "-SkipDataBackup" }

& powershell.exe @args
exit [int]$LASTEXITCODE
