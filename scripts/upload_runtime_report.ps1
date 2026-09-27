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

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git command failed: git $($Args -join ' ')"
    }
}

try {
    Write-Host "Uploading WAR BOT report to GitHub branch '$ReportsBranch'..."
    Invoke-Git fetch $Remote $ReportsBranch

    if (Test-Path $worktree) {
        Remove-Item -Recurse -Force $worktree
    }

    Invoke-Git worktree add --detach $worktree "$Remote/$ReportsBranch"

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

    Invoke-Git -C $worktree add runtime-reports

    & git -C $worktree -c user.name="WAR BOT Runtime Reporter" -c user.email="warbot-runtime@local.invalid" commit -m "runtime: upload host report $reportName"
    if ($LASTEXITCODE -ne 0) {
        throw "git commit failed while uploading report."
    }

    Invoke-Git -C $worktree push $Remote "HEAD:refs/heads/$ReportsBranch"

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
