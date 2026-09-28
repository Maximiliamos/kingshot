param(
    [Parameter(Mandatory=$true)]
    [string]$ReportSource,
    [string]$ReportsBranch = "runtime-reports",
    [string]$Remote = "origin"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path $ReportSource)) {
    throw "Report source does not exist: $ReportSource"
}

$commit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $commit) {
    throw "Cannot determine current Git commit."
}
$short = $commit.Substring(0, 7)
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportName = "$stamp-$short"
$worktree = Join-Path $env:TEMP ("warbot-report-worktree-" + $PID)

function Assert-GitSuccess {
    param([string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "git failed during $Operation (exit $LASTEXITCODE)"
    }
}

function Invoke-GitWithRetry {
    param(
        [Parameter(Mandatory=$true)][scriptblock]$Command,
        [Parameter(Mandatory=$true)][string]$Operation,
        [int]$Attempts = 3
    )
    for ($i = 1; $i -le $Attempts; $i++) {
        & $Command
        if ($LASTEXITCODE -eq 0) { return }
        if ($i -lt $Attempts) {
            Write-Host "$Operation failed (attempt $i/$Attempts); retrying..."
            Start-Sleep -Seconds (2 * $i)
        }
    }
    throw "git failed during $Operation after $Attempts attempts (exit $LASTEXITCODE)"
}

try {
    Write-Host "Uploading TUGARIN BOTS report to GitHub branch '$ReportsBranch'..."
    Invoke-GitWithRetry -Operation "fetch reports branch" -Command {
        & git fetch $Remote $ReportsBranch
    }

    if (Test-Path $worktree) {
        Remove-Item -Recurse -Force $worktree
    }

    & git worktree add --detach $worktree "$Remote/$ReportsBranch"
    Assert-GitSuccess "create report worktree"

    $reportsRoot = Join-Path $worktree "runtime-reports"
    $dest = Join-Path $reportsRoot $reportName
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    Get-ChildItem -LiteralPath $ReportSource -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force
    }

    $latest = [ordered]@{
        report = $reportName
        commit = $commit
        created_at = (Get-Date).ToString("o")
        branch = (& git branch --show-current).Trim()
    }
    $latest | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $reportsRoot "LATEST.json")

    & git -C $worktree add runtime-reports
    Assert-GitSuccess "stage report"

    & git -C $worktree -c user.name="WAR BOT Runtime Reporter" -c user.email="warbot-runtime@local.invalid" commit -m "runtime: upload host report $reportName"
    if ($LASTEXITCODE -ne 0) {
        throw "git commit failed while uploading report."
    }

    Invoke-GitWithRetry -Operation "push report" -Command {
        & git -C $worktree push $Remote "HEAD:refs/heads/$ReportsBranch"
    }

    Write-Host ""
    Write-Host "Report uploaded successfully."
    Write-Host "GitHub branch: $ReportsBranch"
    Write-Host "Report folder: runtime-reports/$reportName"
}
finally {
    if (Test-Path $worktree) {
        & git worktree remove --force $worktree | Out-Null
    }
}
