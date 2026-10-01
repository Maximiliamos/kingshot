param(
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"

$Version = "4.1"
$ExpectedSha256 = "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae"
$Url = "https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-server-v4.1"

$Root = Split-Path -Parent $PSScriptRoot
if (-not $Destination) {
    $Destination = Join-Path $Root "runtime-tools\scrcpy-server-v4.1"
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

$parent = Split-Path -Parent $Destination
New-Item -ItemType Directory -Force -Path $parent | Out-Null

if (Test-Path -LiteralPath $Destination -PathType Leaf) {
    $current = Get-Sha256 -Path $Destination
    if ($current -eq $ExpectedSha256) {
        Write-Host "scrcpy-server v$Version ready: $Destination"
        exit 0
    }
    Write-Host "Existing scrcpy-server hash mismatch; replacing it."
    Remove-Item -LiteralPath $Destination -Force
}

$temp = "$Destination.download"
Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue

Write-Host "Downloading pinned scrcpy-server v$Version..."
$previousTls = [Net.ServicePointManager]::SecurityProtocol
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $temp
}
finally {
    [Net.ServicePointManager]::SecurityProtocol = $previousTls
}

$actual = Get-Sha256 -Path $temp
if ($actual -ne $ExpectedSha256) {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    throw "scrcpy-server SHA-256 mismatch: expected=$ExpectedSha256 actual=$actual"
}

Move-Item -LiteralPath $temp -Destination $Destination -Force
Write-Host "scrcpy-server v$Version verified: $Destination"
exit 0
