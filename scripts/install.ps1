param(
    [string]$Target = "C:\warbot"
)

$ErrorActionPreference = "Stop"

Write-Host "Installing Kingshot bot into $Target"

New-Item -ItemType Directory -Force -Path $Target | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target "templates") | Out-Null

Copy-Item "$PSScriptRoot\..\bot.py" (Join-Path $Target "bot.py") -Force

if (Test-Path "$PSScriptRoot\..\templates") {
    Get-ChildItem "$PSScriptRoot\..\templates" -Filter *.png -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item $_.FullName (Join-Path $Target "templates" $_.Name) -Force
        }
}

python -m pip install -r "$PSScriptRoot\..\requirements.txt"

Write-Host ""
Write-Host "Installed."
Write-Host "Reset state: python C:\warbot\bot.py --reset-state"
Write-Host "Run:         python C:\warbot\bot.py"
