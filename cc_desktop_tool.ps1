$ErrorActionPreference = "Stop"

$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PatchScript = Join-Path $ToolDir "cc_desktop_zh_cn_windows.py"

$Py = Get-Command py -ErrorAction SilentlyContinue
$Python = Get-Command python -ErrorAction SilentlyContinue

function Run-Patcher {
  param([string[]]$PatchArgs)

  if ($Py) {
    & $Py.Source -3 $PatchScript @PatchArgs
    $script:PatchStatus = $LASTEXITCODE
    return
  }

  if ($Python) {
    & $Python.Source $PatchScript @PatchArgs
    $script:PatchStatus = $LASTEXITCODE
    return
  }

  Write-Host "Python 3 was not found. Please install Python 3 or enable the py launcher." -ForegroundColor Red
  $script:PatchStatus = 1
}

function Pause-Menu {
  Write-Host ""
  Read-Host "Press Enter to continue"
}

function Start-PatchedClaude {
  $Exe = Join-Path $env:LOCALAPPDATA "ClaudeZhCN\Claude\Claude.exe"
  $Launcher = Join-Path $env:LOCALAPPDATA "ClaudeZhCN\launch_claude_zh_cn.vbs"
  if (Test-Path $Exe) {
    if (-not (Test-Path $Launcher)) {
      Run-Patcher @("--apply-cowork-compat")
    }
    if (Test-Path $Launcher) {
      Start-Process -FilePath "wscript.exe" -ArgumentList "`"$Launcher`""
      Write-Host "Launched through compatibility launcher: $Launcher" -ForegroundColor Green
    } else {
      Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe)
      Write-Host "Launched: $Exe" -ForegroundColor Green
    }
    Write-Host "Claude is running separately. You can close this tool window or press Enter to return to the menu." -ForegroundColor Yellow
  } else {
    Write-Host "Patched Claude was not found: $Exe" -ForegroundColor Red
    Write-Host "Choose option 1 first to create the patched copy." -ForegroundColor Yellow
  }
}

function Apply-ThirdPartyInference {
  Run-Patcher @("--third-party-wizard")
}

function Offer-ThirdPartyWizard {
  Run-Patcher @("--check-third-party-sources")
  if ($script:PatchStatus -eq 0) {
    Write-Host ""
    Write-Host "You can keep the portable copy fresh, or import third-party model inference config." -ForegroundColor Yellow
    $OpenWizard = Read-Host "Open third-party model inference config wizard now? (Y/N)"
    if ($OpenWizard -match "^[Yy]") {
      Run-Patcher @("--third-party-wizard")
    } else {
      Write-Host "Skipped config import. You can run option 8 later."
    }
  }
}

function Stop-ClaudeProcesses {
  Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  $PortableCoworkSvc = Join-Path $env:LOCALAPPDATA "ClaudeZhCN\Claude\resources\cowork-svc.exe"
  if (Test-Path $PortableCoworkSvc) {
    Get-CimInstance Win32_Process -Filter "Name = 'cowork-svc.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.ExecutablePath -eq $PortableCoworkSvc } |
      ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
  }
}

function Launch-AfterPatch {
  if ($script:PatchStatus -eq 0) {
    Offer-ThirdPartyWizard
    Start-PatchedClaude
  }
}

function Get-LocalMsixCandidates {
  $candidates = @(
    (Join-Path $env:USERPROFILE "Downloads\Claude.msix"),
    (Join-Path $env:LOCALAPPDATA "ClaudeZhCN\downloads\Claude.msix"),
    (Join-Path $ToolDir "Claude.msix")
  )

  $seen = @{}
  foreach ($path in $candidates) {
    if (-not $path) {
      continue
    }
    $fullPath = [System.IO.Path]::GetFullPath($path)
    $key = $fullPath.ToLowerInvariant()
    if (-not $seen.ContainsKey($key)) {
      $seen[$key] = $true
      $fullPath
    }
  }
}

function Find-PreferredLocalMsix {
  foreach ($path in Get-LocalMsixCandidates) {
    if (Test-Path $path) {
      return $path
    }
  }
  return $null
}

function Compare-LocalMsixVersion {
  param([Parameter(Mandatory = $true)][string]$MsixPath)

  Run-Patcher @("--compare-local-msix", $MsixPath)
}

function Install-FromLocalMsix {
  param(
    [Parameter(Mandatory = $true)][string]$MsixPath,
    [switch]$LaunchWhenCurrent,
    [switch]$AllowOlder
  )

  if (-not (Test-Path $MsixPath)) {
    Write-Host "Local Claude.msix was not found: $MsixPath" -ForegroundColor Red
    return
  }

  Write-Host ""
  Write-Host "Found local package: $MsixPath" -ForegroundColor Cyan
  Compare-LocalMsixVersion -MsixPath $MsixPath
  $CompareStatus = $script:PatchStatus

  if ($CompareStatus -eq 10) {
    Write-Host ""
    Write-Host "The local Claude.msix is newer. Updating from the local package first." -ForegroundColor Green
    Stop-ClaudeProcesses
    Run-Patcher @("--source", $MsixPath)
    Launch-AfterPatch
    return
  }

  if ($CompareStatus -eq 0) {
    Write-Host ""
    Write-Host "The patched zh-CN copy already matches this local Claude.msix version." -ForegroundColor Green
    $Reinstall = Read-Host "Reapply / reinstall this local package anyway? (Y/N)"
    if ($Reinstall -match "^[Yy]") {
      Stop-ClaudeProcesses
      Run-Patcher @("--source", $MsixPath)
      Launch-AfterPatch
      return
    }

    Run-Patcher @("--apply-user-settings")
    Offer-ThirdPartyWizard
    if ($LaunchWhenCurrent) {
      $Launch = Read-Host "Launch patched Claude now? (Y/N)"
      if ($Launch -match "^[Yy]") {
        Start-PatchedClaude
      }
    }
    return
  }

  if ($CompareStatus -eq 11) {
    Write-Host ""
    Write-Host "The local Claude.msix is older than the current patched zh-CN copy." -ForegroundColor Yellow
    if (-not $AllowOlder) {
      Write-Host "Skipping the local package to avoid downgrading automatically." -ForegroundColor Yellow
      return
    }

    $Downgrade = Read-Host "Use this older local package and overwrite the current zh-CN copy? (Y/N)"
    if ($Downgrade -match "^[Yy]") {
      Stop-ClaudeProcesses
      Run-Patcher @("--source", $MsixPath)
      Launch-AfterPatch
    } else {
      Write-Host "Cancelled."
    }
    return
  }

  Write-Host ""
  Write-Host "Could not compare the local Claude.msix version. Check whether the file is complete and usable." -ForegroundColor Red
}

function Update-PatchedClaude {
  $LocalMsix = Find-PreferredLocalMsix
  if ($LocalMsix) {
    Install-FromLocalMsix -MsixPath $LocalMsix -LaunchWhenCurrent
    return
  }

  Run-Patcher @("--check-update")
  $CheckStatus = $script:PatchStatus

  if ($CheckStatus -eq 0) {
    Write-Host ""
    Write-Host "Already up to date. Nothing to do." -ForegroundColor Green
    Run-Patcher @("--apply-user-settings")
    Offer-ThirdPartyWizard
    $Launch = Read-Host "Launch patched Claude now? (Y/N)"
    if ($Launch -match "^[Yy]") {
      Start-PatchedClaude
    }
    return
  }

  if ($CheckStatus -ne 10) {
    Write-Host "Version check failed. Falling back to the locally installed Claude if available." -ForegroundColor Yellow
    Stop-ClaudeProcesses
    Run-Patcher @()
    Launch-AfterPatch
    return
  }

  Write-Host ""
  $Answer = Read-Host "Update patched Claude now? (Y/N)"
  if ($Answer -notmatch "^[Yy]") {
    Write-Host "Update cancelled."
    return
  }

  Stop-ClaudeProcesses
  Run-Patcher @("--force-download")
  if ($script:PatchStatus -ne 0) {
    Write-Host "Download/update failed. Falling back to the locally installed Claude if available." -ForegroundColor Yellow
    Run-Patcher @()
  }
  Launch-AfterPatch
}

function Install-FromExplicitLocalMsixMenu {
  $PreferredMsix = Find-PreferredLocalMsix

  Write-Host ""
  Write-Host "This will update the zh-CN copy from a local Claude.msix package." -ForegroundColor Cyan
  if ($PreferredMsix) {
    Write-Host "Detected default package: $PreferredMsix" -ForegroundColor Green
    $UsePreferred = Read-Host "Use this default path? (Y/N)"
    if ($UsePreferred -match "^[Yy]") {
      Install-FromLocalMsix -MsixPath $PreferredMsix -LaunchWhenCurrent -AllowOlder
      return
    }
  } else {
    Write-Host "No Claude.msix was found in the default locations:" -ForegroundColor Yellow
    foreach ($candidate in Get-LocalMsixCandidates) {
      Write-Host " - $candidate"
    }
  }

  Write-Host ""
  $ManualPath = Read-Host "Enter the full path to Claude.msix"
  if ([string]::IsNullOrWhiteSpace($ManualPath)) {
    Write-Host "Cancelled."
    return
  }

  Install-FromLocalMsix -MsixPath $ManualPath.Trim() -LaunchWhenCurrent -AllowOlder
}

while ($true) {
  Write-Host ""
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host " WIN CC Desktop zh-CN Portable" -ForegroundColor Cyan
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host "1. Patch / update / launch zh-CN Claude"
  Write-Host "2. Check latest version"
  Write-Host "3. Locate user config/account data"
  Write-Host "4. Clean user config/account data"
  Write-Host "5. Launch patched Claude"
  Write-Host "6. Create Claude and Claude Code shortcuts"
  Write-Host "7. Full clean portable zh-CN tool files"
  Write-Host "8. Third-party model inference config wizard"
  Write-Host "9. Reapply Cowork patch and rebuild launcher"
  Write-Host "10. Repair official Claude MSIX Cowork sandbox (advanced)"
  Write-Host "11. Repair / prepare Cowork environment"
  Write-Host "12. Update zh-CN Claude from local Claude.msix"
  Write-Host "0. Exit"
  Write-Host ""

  $Choice = Read-Host "Choose an option"

  if ($Choice -eq "0") {
    exit 0
  }

  if ($Choice -eq "1") {
    Update-PatchedClaude
    Pause-Menu
    continue
  }

  if ($Choice -eq "2") {
    Run-Patcher @("--check-update")
    Pause-Menu
    continue
  }

  if ($Choice -eq "3") {
    Run-Patcher @("--show-user-data")
    Write-Host ""
    $Open = Read-Host "Open the portable zh-CN user data folder? (Y/N)"
    if ($Open -match "^[Yy]") {
      $MainData = Join-Path $env:APPDATA "ClaudeZhCN-3p"
      if (Test-Path $MainData) {
        Start-Process explorer.exe $MainData
      } else {
        Start-Process explorer.exe $env:APPDATA
      }
    }
    Pause-Menu
    continue
  }

  if ($Choice -eq "4") {
    Write-Host ""
    Write-Host "This will sign Claude out and reset local app state." -ForegroundColor Yellow
    Write-Host "Data will be moved to a backup under %LOCALAPPDATA%\ClaudeZhCN\user-data-backups." -ForegroundColor Yellow
    Run-Patcher @("--show-user-data")
    Write-Host ""
    $Confirm = Read-Host "Type DELETE to clean user config/account data"
    if ($Confirm -eq "DELETE") {
      Stop-ClaudeProcesses
      Run-Patcher @("--clean-user-data", "--yes")
    } else {
      Write-Host "Cancelled."
    }
    Pause-Menu
    continue
  }

  if ($Choice -eq "5") {
    Run-Patcher @("--apply-user-settings")
    Start-PatchedClaude
    Pause-Menu
    continue
  }

  if ($Choice -eq "6") {
    Run-Patcher @("--create-shortcuts")
    Pause-Menu
    continue
  }

  if ($Choice -eq "7") {
    Write-Host ""
    Write-Host "This deletes patched app, download cache, and shortcuts. Backups are kept." -ForegroundColor Yellow
    Write-Host "It does not delete Claude user config/account data." -ForegroundColor Yellow
    $Confirm = Read-Host "Type DELETE to full clean portable files"
    if ($Confirm -eq "DELETE") {
      Stop-ClaudeProcesses
      Run-Patcher @("--full-clean", "--yes")
    } else {
      Write-Host "Cancelled."
    }
    Pause-Menu
    continue
  }

  if ($Choice -eq "8") {
    Apply-ThirdPartyInference
    Pause-Menu
    continue
  }

  if ($Choice -eq "9") {
    Stop-ClaudeProcesses
    Run-Patcher @("--apply-cowork-compat")
    Run-Patcher @("--create-shortcuts")
    Pause-Menu
    continue
  }

  if ($Choice -eq "10") {
    Write-Host ""
    Write-Host "This touches the official Claude MSIX sandbox and may start CoworkVMService." -ForegroundColor Yellow
    Write-Host "Use it only if the official Claude app loses Cowork after using the portable copy." -ForegroundColor Yellow
    $Confirm = Read-Host "Type REPAIR to continue"
    if ($Confirm -eq "REPAIR") {
      Run-Patcher @("--sync-msix-cowork")
    } else {
      Write-Host "Cancelled."
    }
    Pause-Menu
    continue
  }

  if ($Choice -eq "11") {
    Write-Host ""
    Write-Host "This tool cleans stale Cowork processes/VMs and syncs required files for the target environment." -ForegroundColor Yellow
    Write-Host "1. Repair / prepare portable zh-CN Claude" -ForegroundColor Cyan
    Write-Host "2. Repair / prepare official Claude MSIX" -ForegroundColor Cyan
    $Target = Read-Host "Choose target environment"
    if ($Target -eq "1") {
      Run-Patcher @("--prepare-cowork-switch", "portable")
    } elseif ($Target -eq "2") {
      Run-Patcher @("--prepare-cowork-switch", "official")
    } else {
      Write-Host "Cancelled."
    }
    Pause-Menu
    continue
  }

  if ($Choice -eq "12") {
    Install-FromExplicitLocalMsixMenu
    Pause-Menu
    continue
  }

  Write-Host "Unknown option: $Choice" -ForegroundColor Red
  Pause-Menu
}
