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
  Run-Patcher @("--show-third-party-inference")
  Write-Host ""
  $Apply = Read-Host "Apply detected local Claude Code gateway settings now? (Y/N)"
  if ($Apply -match "^[Yy]") {
    Run-Patcher @("--apply-third-party-inference")
  } else {
    Write-Host "Cancelled."
  }
}

function Update-PatchedClaude {
  Run-Patcher @("--check-update")
  $CheckStatus = $script:PatchStatus

  if ($CheckStatus -eq 0) {
    Write-Host ""
    Write-Host "Already up to date. Nothing to do." -ForegroundColor Green
    Run-Patcher @("--apply-user-settings")
    $Launch = Read-Host "Launch patched Claude now? (Y/N)"
    if ($Launch -match "^[Yy]") {
      Start-PatchedClaude
    }
    return
  }

  if ($CheckStatus -ne 10) {
    Write-Host "Version check failed." -ForegroundColor Red
    return
  }

  Write-Host ""
  $Answer = Read-Host "Update patched Claude now? (Y/N)"
  if ($Answer -notmatch "^[Yy]") {
    Write-Host "Update cancelled."
    return
  }

  Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Run-Patcher @("--force-download", "--launch")
}

while ($true) {
  Write-Host ""
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host " CC Desktop zh-CN Portable" -ForegroundColor Cyan
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host "1. Patch / update / launch zh-CN Claude"
  Write-Host "2. Check latest version"
  Write-Host "3. Locate user config/account data"
  Write-Host "4. Clean user config/account data"
  Write-Host "5. Launch patched Claude"
  Write-Host "6. Create Claude and Claude Code shortcuts"
  Write-Host "7. Full clean portable zh-CN tool files"
  Write-Host "8. Apply local Claude Code gateway settings"
  Write-Host "9. Apply Cowork compatibility fix"
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
    $Open = Read-Host "Open the main Claude user data folder? (Y/N)"
    if ($Open -match "^[Yy]") {
      $MainData = Join-Path $env:APPDATA "Claude"
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
      Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
      Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
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
    Write-Host "This deletes patched app, download cache, backups, and shortcuts." -ForegroundColor Yellow
    Write-Host "It does not delete Claude user config/account data." -ForegroundColor Yellow
    $Confirm = Read-Host "Type DELETE to full clean portable files"
    if ($Confirm -eq "DELETE") {
      Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
      Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
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
    Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Run-Patcher @("--apply-cowork-compat")
    Run-Patcher @("--create-shortcuts")
    Pause-Menu
    continue
  }

  Write-Host "Unknown option: $Choice" -ForegroundColor Red
  Pause-Menu
}
