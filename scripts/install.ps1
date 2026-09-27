param(
    [string]$Target = "C:\warbot",
    [switch]$WithUiAutomator
)

$ErrorActionPreference = "Stop"

Write-Host "Installing WAR BOT into $Target"

New-Item -ItemType Directory -Force -Path $Target | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target "templates") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target "docs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target "scripts") | Out-Null

$files = @(
    "bot.py",
    "gui.py",
    "device_backend.py",
    "native_arm64_poc.py",
    "warbot_cli.py",
    "run_gui.bat",
    "requirements.txt",
    "requirements-android-optional.txt"
)

foreach ($name in $files) {
    $source = Join-Path "$PSScriptRoot\.." $name
    if (Test-Path $source) {
        Copy-Item $source (Join-Path $Target $name) -Force
    }
}

if (Test-Path "$PSScriptRoot\..\docs") {
    Get-ChildItem "$PSScriptRoot\..\docs" -Filter *.md -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item $_.FullName (Join-Path $Target "docs" $_.Name) -Force
        }
}

if (Test-Path "$PSScriptRoot\verify_mvp.ps1") {
    Copy-Item "$PSScriptRoot\verify_mvp.ps1" (Join-Path $Target "scripts\verify_mvp.ps1") -Force
}

if (Test-Path "$PSScriptRoot\..\templates") {
    Get-ChildItem "$PSScriptRoot\..\templates" -Filter *.png -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item $_.FullName (Join-Path $Target "templates" $_.Name) -Force
        }
}

python -m pip install -r "$PSScriptRoot\..\requirements.txt"

if ($WithUiAutomator) {
    python -m pip install -r "$PSScriptRoot\..\requirements-android-optional.txt"
}

Write-Host ""
Write-Host "Installed."
Write-Host "GUI:          python $Target\gui.py"
Write-Host "Reset state:  python $Target\bot.py --reset-state"
Write-Host "Native probe: python $Target\native_arm64_poc.py probe"
Write-Host "MVP verify:   powershell -ExecutionPolicy Bypass -File $Target\scripts\verify_mvp.ps1"
