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

function Show-Header {
  Clear-Host
  Write-Host ""
  Write-Host "============================================================" -ForegroundColor Cyan
  Write-Host " WIN CC Desktop zh-CN Portable Tool" -ForegroundColor Cyan
  Write-Host "============================================================" -ForegroundColor Cyan
  Write-Host " Portable app root: %LOCALAPPDATA%\ClaudeZhCN" -ForegroundColor DarkCyan
  Write-Host " Portable profile:  %APPDATA%\ClaudeZhCN-3p" -ForegroundColor DarkCyan
  Write-Host "------------------------------------------------------------" -ForegroundColor DarkCyan
}

function Stop-PortableClaudeProcesses {
  $PortableRoot = (Join-Path $env:LOCALAPPDATA "ClaudeZhCN\Claude").ToLowerInvariant()
  Get-CimInstance Win32_Process -Filter "Name = 'Claude.exe'" -ErrorAction SilentlyContinue |
    Where-Object { ($_.ExecutablePath + "").ToLowerInvariant().StartsWith($PortableRoot) } |
    ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }

  $PortableCoworkSvc = Join-Path $env:LOCALAPPDATA "ClaudeZhCN\Claude\resources\cowork-svc.exe"
  if (Test-Path $PortableCoworkSvc) {
    Get-CimInstance Win32_Process -Filter "Name = 'cowork-svc.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.ExecutablePath -eq $PortableCoworkSvc } |
      ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
  }
}

function Stop-AllClaudeProcesses {
  Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "Name = 'cowork-svc.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
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
      Write-Host "Launched zh-CN Claude through compatibility launcher." -ForegroundColor Green
      Write-Host $Launcher -ForegroundColor DarkGray
    } else {
      Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe)
      Write-Host "Launched: $Exe" -ForegroundColor Green
    }
    Write-Host "You can close this tool window or press Enter to return to the menu." -ForegroundColor Yellow
  } else {
    Write-Host "Patched Claude was not found: $Exe" -ForegroundColor Red
    Write-Host "Choose option 1 for first install / initialization, or option 4 to update and patch." -ForegroundColor Yellow
  }
}

function Offer-ThirdPartyWizard {
  Run-Patcher @("--check-third-party-sources")
  if ($script:PatchStatus -eq 0) {
    Write-Host ""
    Write-Host "Reusable API mode config was detected." -ForegroundColor Yellow
    $OpenWizard = Read-Host "Open API mode config wizard now? (Y/N)"
    if ($OpenWizard -match "^[Yy]") {
      Run-Patcher @("--third-party-wizard")
    }
  }
}

function Update-PatchedClaude {
  Run-Patcher @("--check-update")
  $CheckStatus = $script:PatchStatus

  if ($CheckStatus -eq 0) {
    Write-Host ""
    Write-Host "Already up to date." -ForegroundColor Green
    Run-Patcher @("--apply-user-settings")
    Offer-ThirdPartyWizard
    return
  }

  if ($CheckStatus -ne 10) {
    Write-Host "Version check failed. Falling back to the locally installed Claude if available." -ForegroundColor Yellow
    Stop-PortableClaudeProcesses
    Run-Patcher @()
    if ($script:PatchStatus -eq 0) {
      Offer-ThirdPartyWizard
    }
    return
  }

  Write-Host ""
  $Answer = Read-Host "Update patched zh-CN Claude now? (Y/N)"
  if ($Answer -notmatch "^[Yy]") {
    Write-Host "Update cancelled."
    return
  }

  Stop-PortableClaudeProcesses
  Run-Patcher @("--force-download")
  if ($script:PatchStatus -ne 0) {
    Write-Host "Download/update failed. Falling back to the locally installed Claude if available." -ForegroundColor Yellow
    Run-Patcher @()
  }
  if ($script:PatchStatus -eq 0) {
    Offer-ThirdPartyWizard
  }
}

function OAuth-Menu {
  while ($true) {
    Show-Header
    Write-Host "Dual Launch / OAuth Login Repair" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. Show current claude:// callback handler"
    Write-Host "  2. Prepare zh-CN OAuth login"
    Write-Host "  3. Restore previous claude:// callback handler"
    Write-Host "  4. Launch zh-CN Claude"
    Write-Host "  0. Back"
    Write-Host ""
    $Choice = Read-Host "Choose"

    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") { Run-Patcher @("--show-oauth-protocol"); Pause-Menu; continue }
    if ($Choice -eq "2") {
      Write-Host ""
      Write-Host "Close official Claude before browser OAuth so the callback is not captured by the wrong instance." -ForegroundColor Yellow
      $Kill = Read-Host "Close all Claude processes before continuing? (Y/N)"
      if ($Kill -match "^[Yy]") {
        Stop-AllClaudeProcesses
      }
      Run-Patcher @("--prepare-oauth-login")
      if ($script:PatchStatus -eq 0) {
        Start-PatchedClaude
        Write-Host ""
        Write-Host "Finish login in zh-CN Claude. After login, use option 3 to restore the previous callback handler." -ForegroundColor Green
      }
      Pause-Menu
      continue
    }
    if ($Choice -eq "3") { Run-Patcher @("--restore-oauth-protocol"); Pause-Menu; continue }
    if ($Choice -eq "4") { Start-PatchedClaude; Pause-Menu; continue }
    Write-Host "Unknown option: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

function Shortcut-Menu {
  while ($true) {
    Show-Header
    Write-Host "Shortcut Manager" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. Create / rebuild Claude zh-CN and Claude Code shortcuts"
    Write-Host "  2. Show shortcuts and launcher paths"
    Write-Host "  0. Back"
    Write-Host ""
    $Choice = Read-Host "Choose"
    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") { Run-Patcher @("--create-shortcuts"); Pause-Menu; continue }
    if ($Choice -eq "2") { Run-Patcher @("--show-user-data"); Pause-Menu; continue }
    Write-Host "Unknown option: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

function ClaudeCode-Menu {
  while ($true) {
    Show-Header
    Write-Host "Claude Code Manager" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. Show install status"
    Write-Host "  2. Install / repair Claude Code - official CMD installer, then npm fallback"
    Write-Host "  3. Update Claude Code"
    Write-Host "  4. Fully uninstall Claude Code"
    Write-Host "  0. Back"
    Write-Host ""
    $Choice = Read-Host "Choose"
    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") { Run-Patcher @("--show-claude-code"); Pause-Menu; continue }
    if ($Choice -eq "2") { Run-Patcher @("--install-claude-code"); Pause-Menu; continue }
    if ($Choice -eq "3") { Run-Patcher @("--update-claude-code"); Pause-Menu; continue }
    if ($Choice -eq "4") {
      Write-Host "This uninstalls Claude Code. The script will ask separately before deleting ~/.claude config/auth/MCP data." -ForegroundColor Yellow
      Run-Patcher @("--uninstall-claude-code")
      Pause-Menu
      continue
    }
    Write-Host "Unknown option: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

function Clean-Menu {
  while ($true) {
    Show-Header
    Write-Host "Clean / Reset / Uninstall" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. Clean user config/account data (backup first)"
    Write-Host "  2. Full clean portable app files (keep user data and backups)"
    Write-Host "  3. Full clean app + user data (dangerous, backup first)"
    Write-Host "  0. Back"
    Write-Host ""
    $Choice = Read-Host "Choose"
    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") {
      Run-Patcher @("--show-user-data")
      $Confirm = Read-Host "Type DELETE to clean user config/account data"
      if ($Confirm -eq "DELETE") {
        Stop-PortableClaudeProcesses
        Run-Patcher @("--clean-user-data", "--yes")
      } else {
        Write-Host "Cancelled."
      }
      Pause-Menu
      continue
    }
    if ($Choice -eq "2") {
      Write-Host "This deletes patched app, download cache, and shortcuts. User data and backups are kept." -ForegroundColor Yellow
      $Confirm = Read-Host "Type DELETE to full clean portable app files"
      if ($Confirm -eq "DELETE") {
        Stop-PortableClaudeProcesses
        Run-Patcher @("--full-clean", "--yes")
      } else {
        Write-Host "Cancelled."
      }
      Pause-Menu
      continue
    }
    if ($Choice -eq "3") {
      Write-Host "Danger: this cleans user data first, then portable app files." -ForegroundColor Red
      $Confirm = Read-Host "Type FULLDELETE to confirm"
      if ($Confirm -eq "FULLDELETE") {
        Stop-PortableClaudeProcesses
        Run-Patcher @("--clean-user-data", "--yes")
        Run-Patcher @("--full-clean", "--yes")
      } else {
        Write-Host "Cancelled."
      }
      Pause-Menu
      continue
    }
    Write-Host "Unknown option: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

while ($true) {
  Show-Header
  Write-Host "  1. First install / initialize - install/repair, preseed API config, and keep account sign-in"
  Write-Host "  2. Launch zh-CN Claude"
  Write-Host "  3. Check for updates"
  Write-Host "  4. Update / rebuild zh-CN portable Claude"
  Write-Host "  5. API mode config"
  Write-Host "  6. Import / sync config"
  Write-Host "  7. Cowork / VM repair"
  Write-Host "  8. Show paths / diagnostics"
  Write-Host "  9. Shortcut manager"
  Write-Host " 10. Claude Code manager"
  Write-Host " 11. Clean / reset / uninstall"
  Write-Host " 12. Dual launch / OAuth login repair"
  Write-Host " 13. Enter API mode (skip account sign-in)"
  Write-Host " 14. Exit API mode (restore account sign-in)"
  Write-Host "  0. Exit"
  Write-Host ""

  $Choice = Read-Host "Choose"

  if ($Choice -eq "0") { exit 0 }
  if ($Choice -eq "1") { Run-Patcher @("--initialize"); Pause-Menu; continue }
  if ($Choice -eq "2") { Run-Patcher @("--apply-user-settings"); Start-PatchedClaude; Pause-Menu; continue }
  if ($Choice -eq "3") { Run-Patcher @("--check-update"); Pause-Menu; continue }
  if ($Choice -eq "4") { Update-PatchedClaude; Pause-Menu; continue }
  if ($Choice -eq "5") { Run-Patcher @("--third-party-wizard"); Pause-Menu; continue }
  if ($Choice -eq "6") { Run-Patcher @("--import-sync-wizard"); Pause-Menu; continue }
  if ($Choice -eq "7") { Run-Patcher @("--cowork-repair-wizard"); Pause-Menu; continue }
  if ($Choice -eq "8") { Run-Patcher @("--show-user-data"); Run-Patcher @("--show-oauth-protocol"); Pause-Menu; continue }
  if ($Choice -eq "9") { Shortcut-Menu; continue }
  if ($Choice -eq "10") { ClaudeCode-Menu; continue }
  if ($Choice -eq "11") { Clean-Menu; continue }
  if ($Choice -eq "12") { OAuth-Menu; continue }
  if ($Choice -eq "13") { Run-Patcher @("--enter-third-party-mode"); Pause-Menu; continue }
  if ($Choice -eq "14") { Run-Patcher @("--exit-third-party-mode"); Pause-Menu; continue }

  Write-Host "Unknown option: $Choice" -ForegroundColor Red
  Pause-Menu
}
