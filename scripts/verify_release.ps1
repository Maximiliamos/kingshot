param(
    [switch]$SkipUnitTests,
    [switch]$SkipDependencySync,
    [switch]$CleanGame,
    [string]$TargetUser = "Программист1",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Resolve-ReleasePython {
    param([string]$ExplicitPython)

    $candidates = @()
    if ($ExplicitPython) { $candidates += $ExplicitPython }
    $candidates += @(
        "C:\warbot_wsa\tugarin-venv\Scripts\python.exe",
        (Join-Path $Root ".venv\Scripts\python.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $systemPython) { $systemPython = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $systemPython) {
        throw "No Python interpreter found. Expected dedicated runtime at C:\warbot_wsa\tugarin-venv\Scripts\python.exe."
    }

    # Never mutate the user's global Python just because the production venv is
    # missing. Build an isolated verifier environment instead.
    $releaseVenvRoot = "C:\warbot_wsa\release-venv"
    $releaseVenvPython = Join-Path $releaseVenvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $releaseVenvPython -PathType Leaf)) {
        Write-Host "Dedicated production venv not found; creating isolated release venv at $releaseVenvRoot"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $releaseVenvRoot) | Out-Null
        & $systemPython.Source -m venv $releaseVenvRoot
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $releaseVenvPython -PathType Leaf)) {
            throw "Could not create isolated release venv with $($systemPython.Source)."
        }
    }
    return (Resolve-Path -LiteralPath $releaseVenvPython).Path
}

function Assert-PythonRuntime {
    param([Parameter(Mandatory = $true)][string]$Interpreter)

    $version = (& $Interpreter -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $version) {
        throw "Could not execute release Python: $Interpreter"
    }
    Write-Host "Release Python: $Interpreter ($version)"

    if (-not $SkipDependencySync) {
        Write-Host "Synchronizing pinned TUGARIN BOTS runtime dependencies..."
        & $Interpreter -m pip install --disable-pip-version-check -r (Join-Path $Root "requirements.txt")
        if ($LASTEXITCODE -ne 0) {
            throw "Pinned runtime dependency installation failed for $Interpreter."
        }
    }

    & $Interpreter -c "import mss, cv2, numpy, PySide6; print('Runtime imports: OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "Release Python is missing required TUGARIN BOTS modules."
    }
}

Write-Host "=== TUGARIN BOTS RELEASE ACCEPTANCE ==="

$ReleasePython = Resolve-ReleasePython -ExplicitPython $PythonExe
Assert-PythonRuntime -Interpreter $ReleasePython
$venvScripts = Split-Path -Parent $ReleasePython
$env:PATH = "$venvScripts;$env:PATH"
$env:PYTHONIOENCODING = "utf-8"

if (-not $SkipUnitTests) {
    Write-Host "1/3 Hosted-equivalent unit/integration suite"
    $env:QT_QPA_PLATFORM = "offscreen"
    & $ReleasePython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    Write-Host "1/3 Unit/integration suite skipped explicitly"
}

Write-Host ""
Write-Host "2/3 WSA + Kingshot real-host gate"
$verifyArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "verify_mvp.ps1"),
    "-PythonExe", $ReleasePython
)
if ($CleanGame) { $verifyArgs += "-CleanGame" }
& powershell.exe @verifyArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "3/3 Dedicated-user stale-process audit"
$auditArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "audit_runtime_processes.ps1"),
    "-TargetUser", $TargetUser
)
& powershell.exe @auditArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "RELEASE HOST GATE PASS"
exit 0
