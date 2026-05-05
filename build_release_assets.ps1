$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutDir = Join-Path $Root "dist"
$Name = "WIN-CC-Desktop-zh-CN-Portable"

function Get-VersionName {
  $Changelog = Join-Path $Root "CHANGELOG.md"
  if (Test-Path $Changelog) {
    $Line = Get-Content $Changelog | Where-Object { $_ -match "^## v[0-9]" } | Select-Object -First 1
    if ($Line -match "## (v[0-9][^ ]*)") {
      return $Matches[1]
    }
  }
  return (Get-Date -Format "yyyyMMdd-HHmmss")
}

$Version = Get-VersionName
$ZipPath = Join-Path $OutDir "$Name-$Version.zip"
$ShaPath = "$ZipPath.sha256"
$Stage = Join-Path $env:TEMP "$Name-$Version"

$Include = @(
  "cc_desktop_zh_cn_windows.py",
  "cc_desktop_tool.bat",
  "cc_desktop_tool.ps1",
  "cc_desktop_tool_zh.bat",
  "cc_desktop_tool_zh.ps1",
  "README.md",
  "CHANGELOG.md",
  "DISCLAIMER.md",
  "LICENSE",
  "resources"
)

if (Test-Path $Stage) {
  Remove-Item -LiteralPath $Stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

foreach ($Item in $Include) {
  $Source = Join-Path $Root $Item
  if (-not (Test-Path $Source)) {
    throw "Missing release asset input: $Source"
  }
  $Target = Join-Path $Stage $Item
  if ((Get-Item $Source).PSIsContainer) {
    Copy-Item -LiteralPath $Source -Destination $Target -Recurse
  } else {
    Copy-Item -LiteralPath $Source -Destination $Target
  }
}

if (Test-Path $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}
if (Test-Path $ShaPath) {
  Remove-Item -LiteralPath $ShaPath -Force
}

$StageItems = Get-ChildItem -LiteralPath $Stage -Force
Compress-Archive -LiteralPath $StageItems.FullName -DestinationPath $ZipPath -Force
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath
"$($Hash.Hash.ToLower())  $(Split-Path -Leaf $ZipPath)" | Set-Content -LiteralPath $ShaPath -Encoding ASCII

Write-Host "Created release zip: $ZipPath" -ForegroundColor Green
Write-Host "Created checksum:    $ShaPath" -ForegroundColor Green
