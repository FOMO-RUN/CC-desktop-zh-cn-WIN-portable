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
  Run-Patcher @("--third-party-wizard")
}

function Offer-ThirdPartyWizard {
  Run-Patcher @("--check-third-party-sources")
  if ($script:PatchStatus -eq 0) {
    Write-Host ""
    Write-Host "你可以保持全新配置，也可以导入第三方大模型推理配置。" -ForegroundColor Yellow
    $OpenWizard = Read-Host "是否现在打开第三方大模型推理配置向导? (Y/N)"
    if ($OpenWizard -match "^[Yy]") {
      Run-Patcher @("--third-party-wizard")
    } else {
      Write-Host "已跳过配置导入。之后可通过菜单 8 再打开。"
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

function Update-PatchedClaude {
  Run-Patcher @("--check-update")
  $CheckStatus = $script:PatchStatus

  if ($CheckStatus -eq 0) {
    Write-Host ""
    Write-Host "已经是最新版，无需更新。" -ForegroundColor Green
    Run-Patcher @("--apply-user-settings")
    Offer-ThirdPartyWizard
    $Launch = Read-Host "是否启动汉化版 Claude? (Y/N)"
    if ($Launch -match "^[Yy]") {
      Start-PatchedClaude
    }
    return
  }

  if ($CheckStatus -ne 10) {
    Write-Host "版本检查失败，将回退到本机已安装的 Claude 继续汉化/启动。" -ForegroundColor Yellow
    Stop-ClaudeProcesses
    Run-Patcher @()
    Launch-AfterPatch
    return
  }

  Write-Host ""
  $Answer = Read-Host "是否现在更新汉化版 Claude? (Y/N)"
  if ($Answer -notmatch "^[Yy]") {
    Write-Host "已取消更新。"
    return
  }

  Stop-ClaudeProcesses
  Run-Patcher @("--force-download")
  if ($script:PatchStatus -ne 0) {
    Write-Host "下载/更新失败，将回退到本机已安装的 Claude 继续汉化/启动。" -ForegroundColor Yellow
    Run-Patcher @()
  }
  Launch-AfterPatch
}

while ($true) {
  Write-Host ""
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host " WIN CC Desktop 中文绿色版工具" -ForegroundColor Cyan
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host "1. 汉化 / 更新 / 启动汉化版"
  Write-Host "2. 检查版本更新"
  Write-Host "3. 定位用户配置/账号数据"
  Write-Host "4. 清理用户配置/账号数据"
  Write-Host "5. 启动汉化版 Claude"
  Write-Host "6. 创建 Claude 和 Claude Code 快捷方式"
  Write-Host "7. 完全清理绿色版文件"
  Write-Host "8. 第三方大模型推理配置向导"
  Write-Host "9. 重新应用 Cowork 补丁并重建启动器"
  Write-Host "10. 修复官方 Claude MSIX Cowork 沙箱（高级）"
  Write-Host "11. 修复 / 准备 Cowork 环境"
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
    $Open = Read-Host "是否打开中文绿色版用户数据文件夹? (Y/N)"
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
    Write-Host "这会退出 Claude 登录状态，并重置本地应用状态。" -ForegroundColor Yellow
    Write-Host "数据不会永久删除，会移动备份到 %LOCALAPPDATA%\ClaudeZhCN\user-data-backups。" -ForegroundColor Yellow
    Run-Patcher @("--show-user-data")
    Write-Host ""
    $Confirm = Read-Host "输入 DELETE 确认清理用户配置/账号数据"
    if ($Confirm -eq "DELETE") {
      Stop-ClaudeProcesses
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
    Write-Host "这会删除汉化副本、下载缓存和快捷方式，但保留备份。" -ForegroundColor Yellow
    Write-Host "不会删除 Claude 用户配置/账号数据。" -ForegroundColor Yellow
    $Confirm = Read-Host "输入 DELETE 确认完全清理绿色版文件"
    if ($Confirm -eq "DELETE") {
      Stop-ClaudeProcesses
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
    Stop-ClaudeProcesses
    Run-Patcher @("--apply-cowork-compat")
    Run-Patcher @("--create-shortcuts")
    Pause-Menu
    continue
  }

  if ($Choice -eq "10") {
    Write-Host ""
    Write-Host "这个操作会触碰官方 Claude MSIX 沙箱，并可能启动 CoworkVMService。" -ForegroundColor Yellow
    Write-Host "只有在使用绿色版后，官方 Claude 的 Cowork 失效时才建议使用。" -ForegroundColor Yellow
    $Confirm = Read-Host "输入 REPAIR 确认继续"
    if ($Confirm -eq "REPAIR") {
      Run-Patcher @("--sync-msix-cowork")
    } else {
      Write-Host "已取消。"
    }
    Pause-Menu
    continue
  }

  if ($Choice -eq "11") {
    Write-Host ""
    Write-Host "这个工具用于清理残留 Cowork 进程/VM，并为目标环境补齐必要文件。" -ForegroundColor Yellow
    Write-Host "1. 修复 / 准备中文绿色版 Claude" -ForegroundColor Cyan
    Write-Host "2. 修复 / 准备官方 Claude MSIX" -ForegroundColor Cyan
    $Target = Read-Host "请选择目标环境"
    if ($Target -eq "1") {
      Run-Patcher @("--prepare-cowork-switch", "portable")
    } elseif ($Target -eq "2") {
      Run-Patcher @("--prepare-cowork-switch", "official")
    } else {
      Write-Host "已取消。"
    }
    Pause-Menu
    continue
  }

  Write-Host "未知选项: $Choice" -ForegroundColor Red
  Pause-Menu
}
