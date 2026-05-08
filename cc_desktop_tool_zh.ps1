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

function Show-Header {
  Clear-Host
  Write-Host ""
  Write-Host "============================================================" -ForegroundColor Cyan
  Write-Host " WIN CC Desktop zh-CN Portable 工具" -ForegroundColor Cyan
  Write-Host "============================================================" -ForegroundColor Cyan
  Write-Host " 独立绿色版目录: %LOCALAPPDATA%\ClaudeZhCN" -ForegroundColor DarkCyan
  Write-Host " 独立用户空间:   %APPDATA%\ClaudeZhCN-3p" -ForegroundColor DarkCyan
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
      Write-Host "已通过兼容启动器启动汉化版。" -ForegroundColor Green
      Write-Host $Launcher -ForegroundColor DarkGray
    } else {
      Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe)
      Write-Host "已启动: $Exe" -ForegroundColor Green
    }
    Write-Host "工具窗口可以关闭，也可以按回车返回菜单。" -ForegroundColor Yellow
  } else {
    Write-Host "未找到汉化版 Claude: $Exe" -ForegroundColor Red
    Write-Host "请先选择 1 首次安装 / 初始化，或选择 4 更新并重新汉化一次。" -ForegroundColor Yellow
  }
}

function Offer-ThirdPartyWizard {
  Run-Patcher @("--check-third-party-sources")
  if ($script:PatchStatus -eq 0) {
    Write-Host ""
    Write-Host "检测到可复用的 API 配置。" -ForegroundColor Yellow
    $OpenWizard = Read-Host "是否现在打开 API 模式配置向导? (Y/N)"
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
    Write-Host "已经是最新版，无需更新。" -ForegroundColor Green
    Run-Patcher @("--apply-user-settings")
    Offer-ThirdPartyWizard
    return
  }

  if ($CheckStatus -ne 10) {
    Write-Host "版本检查失败，将回退到本机已安装 Claude 继续汉化。" -ForegroundColor Yellow
    Stop-PortableClaudeProcesses
    Run-Patcher @()
    if ($script:PatchStatus -eq 0) {
      Offer-ThirdPartyWizard
    }
    return
  }

  Write-Host ""
  $Answer = Read-Host "检测到可更新版本，是否现在更新汉化版? (Y/N)"
  if ($Answer -notmatch "^[Yy]") {
    Write-Host "已取消更新。"
    return
  }

  Stop-PortableClaudeProcesses
  Run-Patcher @("--force-download")
  if ($script:PatchStatus -ne 0) {
    Write-Host "下载/更新失败，将回退到本机已安装 Claude 继续汉化。" -ForegroundColor Yellow
    Run-Patcher @()
  }
  if ($script:PatchStatus -eq 0) {
    Offer-ThirdPartyWizard
  }
}

function OAuth-Menu {
  while ($true) {
    Show-Header
    Write-Host "双开 / OAuth 登录修复" -ForegroundColor Yellow
    Write-Host "用于汉化版也要登录官方账号时，避免浏览器登录回调被原版 Claude 接走。" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  1. 查看当前 claude:// 回调指向 - 看浏览器登录完成后会唤起哪个 Claude"
    Write-Host "  2. 准备汉化版 OAuth 登录 - 备份当前回调，并临时指向汉化版启动器"
    Write-Host "  3. 恢复上一次 claude:// 回调指向 - 登录完成后可还原之前的处理器"
    Write-Host "  4. 启动汉化版 - 不改回调，只直接打开当前汉化版"
    Write-Host "  0. 返回"
    Write-Host ""
    $Choice = Read-Host "请选择"

    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") { Run-Patcher @("--show-oauth-protocol"); Pause-Menu; continue }
    if ($Choice -eq "2") {
      Write-Host ""
      Write-Host "为了避免浏览器 OAuth 回调被官方版接走，建议先关闭所有官方 Claude 窗口。" -ForegroundColor Yellow
      $Kill = Read-Host "是否关闭所有 Claude 进程后继续? (Y/N)"
      if ($Kill -match "^[Yy]") {
        Stop-AllClaudeProcesses
      }
      Run-Patcher @("--prepare-oauth-login")
      if ($script:PatchStatus -eq 0) {
        Start-PatchedClaude
        Write-Host ""
        Write-Host "请在汉化版里完成登录。登录完成后，可回到此菜单选择 3 恢复回调指向。" -ForegroundColor Green
      }
      Pause-Menu
      continue
    }
    if ($Choice -eq "3") { Run-Patcher @("--restore-oauth-protocol"); Pause-Menu; continue }
    if ($Choice -eq "4") { Start-PatchedClaude; Pause-Menu; continue }
    Write-Host "未知选项: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

function Shortcut-Menu {
  while ($true) {
    Show-Header
    Write-Host "快捷方式管理" -ForegroundColor Yellow
    Write-Host "用于重建桌面和开始菜单入口，尤其是更新后或快捷方式丢失时。" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  1. 创建 / 重建快捷方式 - 生成 Claude zh-CN 和 Claude Code 的桌面/开始菜单入口"
    Write-Host "  2. 查看快捷方式和启动器路径 - 只显示路径和大小，不修改文件"
    Write-Host "  0. 返回"
    Write-Host ""
    $Choice = Read-Host "请选择"
    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") { Run-Patcher @("--create-shortcuts"); Pause-Menu; continue }
    if ($Choice -eq "2") { Run-Patcher @("--show-user-data"); Pause-Menu; continue }
    Write-Host "未知选项: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

function ClaudeCode-Menu {
  while ($true) {
    Show-Header
    Write-Host "Claude Code 管理" -ForegroundColor Yellow
    Write-Host "用于查看安装来源，并按官方 CMD 原生 / npm 安装，更新或卸载时再按来源分流。" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  1. 查看安装状态 - 显示版本、来源和 claude 命令路径"
    Write-Host "  2. 安装 / 修复 Claude Code - 默认使用官方 CMD 原生安装器，失败后回退 npm"
    Write-Host "  3. 更新 Claude Code - 按检测到的安装来源执行更新"
    Write-Host "  4. 完全卸载 Claude Code - 卸载程序，并可选择删除配置/授权/MCP 数据"
    Write-Host "  0. 返回"
    Write-Host ""
    $Choice = Read-Host "请选择"
    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") { Run-Patcher @("--show-claude-code"); Pause-Menu; continue }
    if ($Choice -eq "2") { Run-Patcher @("--install-claude-code"); Pause-Menu; continue }
    if ($Choice -eq "3") { Run-Patcher @("--update-claude-code"); Pause-Menu; continue }
    if ($Choice -eq "4") {
      Write-Host "这会卸载 Claude Code。是否删除 ~/.claude 等配置数据，会由脚本再次询问。" -ForegroundColor Yellow
      Run-Patcher @("--uninstall-claude-code")
      Pause-Menu
      continue
    }
    Write-Host "未知选项: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

function Clean-Menu {
  while ($true) {
    Show-Header
    Write-Host "清理 / 重置 / 卸载" -ForegroundColor Yellow
    Write-Host "用于退出登录、重置数据或删除绿色版文件。危险操作都会要求二次确认。" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  1. 清理用户配置/账号数据 - 会退出登录并备份数据，适合重置汉化版状态"
    Write-Host "  2. 完全清理绿色版程序文件 - 删除程序副本/缓存/快捷方式，保留用户数据和备份"
    Write-Host "  3. 完全清理程序 + 用户数据 - 危险，尽量备份后删除绿色版相关内容"
    Write-Host "  0. 返回"
    Write-Host ""
    $Choice = Read-Host "请选择"
    if ($Choice -eq "0") { return }
    if ($Choice -eq "1") {
      Run-Patcher @("--show-user-data")
      $Confirm = Read-Host "输入 DELETE 确认清理用户配置/账号数据"
      if ($Confirm -eq "DELETE") {
        Stop-PortableClaudeProcesses
        Run-Patcher @("--clean-user-data", "--yes")
      } else {
        Write-Host "已取消。"
      }
      Pause-Menu
      continue
    }
    if ($Choice -eq "2") {
      Write-Host "这会删除汉化副本、下载缓存和快捷方式，但保留用户数据和备份。" -ForegroundColor Yellow
      $Confirm = Read-Host "输入 DELETE 确认完全清理绿色版程序文件"
      if ($Confirm -eq "DELETE") {
        Stop-PortableClaudeProcesses
        Run-Patcher @("--full-clean", "--yes")
      } else {
        Write-Host "已取消。"
      }
      Pause-Menu
      continue
    }
    if ($Choice -eq "3") {
      Write-Host "危险操作：会先清理用户数据，再清理绿色版程序文件。" -ForegroundColor Red
      $Confirm = Read-Host "输入 FULLDELETE 确认"
      if ($Confirm -eq "FULLDELETE") {
        Stop-PortableClaudeProcesses
        Run-Patcher @("--clean-user-data", "--yes")
        Run-Patcher @("--full-clean", "--yes")
      } else {
        Write-Host "已取消。"
      }
      Pause-Menu
      continue
    }
    Write-Host "未知选项: $Choice" -ForegroundColor Red
    Pause-Menu
  }
}

while ($true) {
  Show-Header
  Write-Host "  1. 首次安装 / 初始化 - 自动安装/修复，并预置 API 配置但保留账号登录入口"
  Write-Host "  2. 启动汉化版 - 直接打开当前已生成的 Claude zh-CN，不检查更新"
  Write-Host "  3. 检查更新 - 只比较官方最新版和本地汉化版版本，不下载、不修改"
  Write-Host "  4. 更新并重新汉化一次 - 已安装后用于更新或强制重建中文绿色版"
  Write-Host "  5. API 模式配置 - 配置/导入 API 地址和 API key，并可直进 API 模式"
  Write-Host "  6. 导入 / 同步配置 - 在官方版、绿色版、Claude Code 之间双向同步配置，写入前备份"
  Write-Host "  7. Cowork / VM 修复 - 修复 Cowork 启动、VM bundle、残留进程和官方沙箱问题"
  Write-Host "  8. 查看路径 / 诊断 - 显示程序、用户数据、API 配置、快捷方式和 OAuth 回调位置"
  Write-Host "  9. 快捷方式管理 - 创建或查看桌面/开始菜单快捷方式"
  Write-Host " 10. Claude Code 管理 - 安装、更新、检测来源或完全卸载 Claude Code"
  Write-Host " 11. 清理 / 重置 / 卸载 - 清理账号数据、程序副本、缓存或快捷方式"
  Write-Host " 12. 双开 / OAuth 登录修复 - 官方版和汉化版都要登录账号时，临时接管登录回调"
  Write-Host " 13. 进入 API 模式 - 使用已有 API 配置并隐藏账号登录入口"
  Write-Host " 14. 退出 API 模式 - 恢复账号登录/API 模式选择，保留 API 配置"
  Write-Host "  0. 退出"
  Write-Host ""

  $Choice = Read-Host "请选择"

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

  Write-Host "未知选项: $Choice" -ForegroundColor Red
  Pause-Menu
}
