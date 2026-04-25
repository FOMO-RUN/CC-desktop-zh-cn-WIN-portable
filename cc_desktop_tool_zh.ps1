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

  Write-Host "未找到 Python 3。请安装 Python 3 或启用 py 启动器。" -ForegroundColor Red
  $script:PatchStatus = 1
}

function Pause-Menu {
  Write-Host ""
  Read-Host "按回车继续"
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
      Write-Host "已通过兼容启动器启动: $Launcher" -ForegroundColor Green
    } else {
      Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe)
      Write-Host "已启动: $Exe" -ForegroundColor Green
    }
    Write-Host "Claude 已单独运行。这个工具窗口只是菜单，可以关闭，或按回车返回菜单。" -ForegroundColor Yellow
  } else {
    Write-Host "未找到汉化版 Claude: $Exe" -ForegroundColor Red
    Write-Host "请先选择 1 生成汉化副本。" -ForegroundColor Yellow
  }
}

function Apply-ThirdPartyInference {
  Run-Patcher @("--show-third-party-inference")
  Write-Host ""
  $Apply = Read-Host "是否把检测到的本地 Claude Code 网关配置写入 Desktop? (Y/N)"
  if ($Apply -match "^[Yy]") {
    Run-Patcher @("--apply-third-party-inference")
  } else {
    Write-Host "已取消。"
  }
}

function Update-PatchedClaude {
  Run-Patcher @("--check-update")
  $CheckStatus = $script:PatchStatus

  if ($CheckStatus -eq 0) {
    Write-Host ""
    Write-Host "已经是最新版，无需更新。" -ForegroundColor Green
    Run-Patcher @("--apply-user-settings")
    $Launch = Read-Host "是否启动汉化版 Claude? (Y/N)"
    if ($Launch -match "^[Yy]") {
      Start-PatchedClaude
    }
    return
  }

  if ($CheckStatus -ne 10) {
    Write-Host "版本检查失败。" -ForegroundColor Red
    return
  }

  Write-Host ""
  $Answer = Read-Host "是否现在更新汉化版 Claude? (Y/N)"
  if ($Answer -notmatch "^[Yy]") {
    Write-Host "已取消更新。"
    return
  }

  Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Run-Patcher @("--force-download", "--launch")
}

while ($true) {
  Write-Host ""
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host " CC Desktop 中文绿色版工具" -ForegroundColor Cyan
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host "1. 汉化 / 更新 / 启动汉化版"
  Write-Host "2. 检查版本更新"
  Write-Host "3. 定位用户配置/账号数据"
  Write-Host "4. 清理用户配置/账号数据"
  Write-Host "5. 启动汉化版 Claude"
  Write-Host "6. 创建桌面和开始菜单快捷方式"
  Write-Host "7. 完全清理绿色版文件"
  Write-Host "8. 应用本地 Claude Code 网关配置"
  Write-Host "9. 应用 Cowork 兼容修复"
  Write-Host "0. 退出"
  Write-Host ""

  $Choice = Read-Host "请选择"

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
    $Open = Read-Host "是否打开主要 Claude 用户数据文件夹? (Y/N)"
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
    Write-Host "这会退出 Claude 登录状态，并重置本地应用状态。" -ForegroundColor Yellow
    Write-Host "数据不会永久删除，会移动备份到 %LOCALAPPDATA%\ClaudeZhCN\user-data-backups。" -ForegroundColor Yellow
    Run-Patcher @("--show-user-data")
    Write-Host ""
    $Confirm = Read-Host "输入 DELETE 确认清理用户配置/账号数据"
    if ($Confirm -eq "DELETE") {
      Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
      Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
      Run-Patcher @("--clean-user-data", "--yes")
    } else {
      Write-Host "已取消。"
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
    Write-Host "这会删除汉化副本、下载缓存、备份和快捷方式。" -ForegroundColor Yellow
    Write-Host "不会删除 Claude 用户配置/账号数据。" -ForegroundColor Yellow
    $Confirm = Read-Host "输入 DELETE 确认完全清理绿色版文件"
    if ($Confirm -eq "DELETE") {
      Get-Process Claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
      Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
      Run-Patcher @("--full-clean", "--yes")
    } else {
      Write-Host "已取消。"
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

  Write-Host "未知选项: $Choice" -ForegroundColor Red
  Pause-Menu
}
