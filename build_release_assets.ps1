[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$OutputDir = "."
)

$ErrorActionPreference = "Stop"

function Get-ReleaseVersion {
    if ($Version) {
        return $Version
    }

    $match = Select-String -Path "CHANGELOG.md" -Pattern '^##\s+v([0-9][0-9A-Za-z\.\-_]*)\s+-' | Select-Object -First 1
    if ($match) {
        return $match.Matches[0].Groups[1].Value
    }

    throw "Cannot infer version from CHANGELOG.md. Pass -Version explicitly."
}

function Get-GitCommit {
    $commit = (git rev-parse --verify HEAD).Trim()
    if (-not $commit) {
        throw "Cannot resolve current git commit."
    }
    return $commit
}

$releaseVersion = Get-ReleaseVersion
$outputRoot = Resolve-Path $OutputDir
$assetName = "WIN-CC-Desktop-zh-CN-Portable-v$releaseVersion.zip"
$zipPath = Join-Path $outputRoot $assetName
$shaPath = "$zipPath.sha256"
$commit = Get-GitCommit

git archive --format=zip --output="$zipPath" $commit
if (-not (Test-Path -LiteralPath $zipPath)) {
    throw "Failed to create archive: $zipPath"
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
"$hash *$assetName" | Set-Content -LiteralPath $shaPath -Encoding ascii -NoNewline

Write-Host "Created:"
Write-Host "  $zipPath"
Write-Host "  $shaPath"
Write-Host "SHA256:"
Write-Host "  $hash"
