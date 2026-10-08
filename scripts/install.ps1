param(
    [string]$Target = "C:\warbot",
    [switch]$WithUiAutomator
)

$ErrorActionPreference = "Stop"

Write-Host "Installing TUGARIN BOTS into $Target"

New-Item -ItemType Directory -Force -Path $Target | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target "templates") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target "docs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target "scripts") | Out-Null

$files = @(
    "bot.py",
    "tutorial_vision.py",
    "task_engine.py",
    "vision_replay.py",
    "resource_diagnostics.py",
    "resource_readiness.py",
    "gui.py",
    "device_backend.py",
    "frame_stream.py",
    "scrcpy_transport.py",
    "runtime_events.py",
    "runtime_recovery.py",
    "runtime_watchdog.py",
    "native_arm64_poc.py",
    "warbot_cli.py",
    "run_gui.bat",
    "run_gui.vbs",
    "install_tugarin_bots.ps1",
    "install_tugarin_bots.cmd",
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

foreach ($scriptName in @(
    "verify_mvp.ps1",
    "run_and_report.ps1",
    "run_full_mvp_and_report.ps1",
    "upload_runtime_report.ps1",
    "audit_runtime_processes.ps1",
    "verify_release.ps1",
    "verify_preview.ps1",
    "verify_gui.ps1",
    "verify_google_services.ps1",
    "verify_operator_io.ps1",
    "verify_recovery.ps1",
    "verify_game_flow.ps1",
    "verify_resource_readiness.ps1",
    "verify_replay_regression.ps1",
    "verify_soak.ps1",
    "verify_mvp_full.ps1",
    "provision_scrcpy_server.ps1",
    "setup_dedicated_wsa_user.ps1",
    "stop_gapps_migration_task.ps1"
)) {
    $scriptPath = Join-Path $PSScriptRoot $scriptName
    if (Test-Path $scriptPath) {
        Copy-Item $scriptPath (Join-Path $Target "scripts\$scriptName") -Force
    }
}

# Ship the pinned, reviewed replay manifest and any real screenshot corpus.
# Do not silently generate dummy fixture images during installation.
$fixtureSource = Join-Path "$PSScriptRoot\.." "tests\fixtures\tutorial_replay"
if (Test-Path -LiteralPath $fixtureSource -PathType Container) {
    $fixtureTarget = Join-Path $Target "tests\fixtures\tutorial_replay"
    New-Item -ItemType Directory -Force -Path $fixtureTarget | Out-Null
    Get-ChildItem -LiteralPath $fixtureSource -File |
        Where-Object { $_.Extension -in @(".json", ".png") } |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $fixtureTarget $_.Name) -Force
        }
}

if (Test-Path "$PSScriptRoot\..\templates") {
    Get-ChildItem "$PSScriptRoot\..\templates" -Filter *.png -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item $_.FullName (Join-Path $Target "templates" $_.Name) -Force
        }
}

python -m pip install -r "$PSScriptRoot\..\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Core dependency installation failed." }

if ($WithUiAutomator) {
    python -m pip install -r "$PSScriptRoot\..\requirements-android-optional.txt"
    if ($LASTEXITCODE -ne 0) { throw "Android helper dependency installation failed." }
}

Write-Host ""
Write-Host "Installed."
Write-Host "GUI:          wscript.exe //B $Target\run_gui.vbs"
Write-Host "Reset state:  python $Target\bot.py --reset-state"
Write-Host "Native probe: python $Target\native_arm64_poc.py probe"
Write-Host "MVP verify:   powershell -ExecutionPolicy Bypass -File $Target\scripts\verify_mvp_full.ps1"
Write-Host "Remote cycle: powershell -ExecutionPolicy Bypass -File $Target\scripts\run_and_report.ps1"
