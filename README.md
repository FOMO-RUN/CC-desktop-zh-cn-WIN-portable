# WIN CC Desktop zh-CN Portable

> 中文绿色版和官方英文 MSIX 版虽然已经做了 Cowork VM 命名空间隔离，但两边仍然共用同一套 Windows HCS / Hyper-V / VM 服务状态。
> 当前版本会在菜单 `1` / `5` / `9` 和启动器重建时自动准备新版 Cowork 所需目录；菜单 `11` 主要作为残留清理和环境修复入口。

一键生成可与官方安装版共存的中文绿色版 CC Desktop。

它会从官方 Windows MSIX 或本机已安装应用生成一个独立的中文副本，放在 `%LOCALAPPDATA%\ClaudeZhCN` 下运行。原版 Claude Desktop 不会被修改，汉化版和原版可以共存。

即使你没有安装过 Claude Desktop，也可以通过本工具下载官方 MSIX 并生成中文绿色版。仓库只包含补丁脚本和翻译资源，不包含官方应用、安装包、账号数据或访问令牌。

> CC 在本项目中指 Claude/Claude Code 相关桌面体验的缩写。本项目是独立社区工具，非官方项目。发布或使用前请阅读 [DISCLAIMER.md](DISCLAIMER.md)。

## 项目特色

- 独立绿色副本：默认安装到 `%LOCALAPPDATA%\ClaudeZhCN\Claude`，不覆盖官方安装。
- 中文化体验：合并前端中文资源，并补丁处理部分硬编码菜单、设置页、Code[代码] / Cowork[协作] 文案。
- 配置选择权：第三方大模型推理配置向导可以保持全新，也可以同步 Claude Desktop 或 Claude Code 的已有配置。
- 可跳过登录模式选择：导入或生成配置后，工具会启用 `disableDeploymentModeChooser`，直接进入第三方大模型推理模式。
- 原版共存：开始菜单里的原版 Claude 仍然是英文官方版，`Claude zh-CN` 快捷方式启动的是中文绿色版，并使用独立的 `%APPDATA%\ClaudeZhCN-3p` 用户数据目录避免单实例冲突。
- Code[代码] / Cowork[协作] 共存：修复绿色版路径检测，并隔离 Cowork VM 的管道、网络和存储命名空间，降低与官方 MSIX 版同时运行时的冲突。
- 快捷方式自动创建：首次汉化或更新后自动创建 `Claude zh-CN` 和 `Claude Code` 的桌面 / 开始菜单快捷方式。
- 更新友好：版本相同则跳过下载；官方下载接口异常时会回退到本机已安装 Claude。

## 工作方式

汉化版不会改动官方安装目录，而是生成一个独立副本后再补丁资源：

```mermaid
flowchart TD
    A["官方 Claude Desktop MSIX 或本机已安装应用"] --> B["复制 / 解包到 %LOCALAPPDATA%\\ClaudeZhCN\\Claude"]
    B --> C["注入 zh-CN 语言白名单"]
    C --> D["合并 frontend / desktop / statsig 中文资源"]
    D --> E["补丁硬编码文案和桌面菜单"]
    E --> F["修复 Code[代码] / Cowork[协作] 绿色版检测与 VM 命名空间"]
    F --> J["使用 %APPDATA%\\ClaudeZhCN-3p 独立用户数据"]
    F --> G["创建 Claude zh-CN 快捷方式"]
    F --> H["创建 Claude Code 快捷方式"]
    A -.-> I["原版 Claude Desktop 继续保留，不修改官方安装"]
```

第三方大模型推理配置默认不会强行导入，用户自己选择：

```mermaid
flowchart TD
    A["菜单 8：第三方大模型推理配置向导"] --> B{"用户选择"}
    B -- "保持全新" --> C["不导入、不修改第三方大模型推理配置"]
    B -- "同步 Desktop 配置" --> D["复制 configLibrary 并备份目标配置"]
    B -- "从 Claude Code 生成" --> E["读取 ANTHROPIC_BASE_URL 和访问令牌[token]"]
    D --> F["启用 disableDeploymentModeChooser"]
    E --> F
    F --> G["启动时直接进入第三方大模型推理模式"]
```

## 快速开始

推荐双击英文菜单入口，兼容 Windows PowerShell 5.1：

```text
cc_desktop_tool.bat
```

选择：

```text
1. Patch / update / launch zh-CN Claude
```

中文菜单入口也可使用：

```text
cc_desktop_tool_zh.bat
```

如果你喜欢 PowerShell：

```powershell
cd C:\Users\TC\Downloads\claude-desktop-zh-cn-main
.\cc_desktop_tool.ps1
```

首次运行选项 `1` 后，工具会自动执行：检查版本、生成中文副本、应用中文资源、启用 Developer Mode[开发者模式]、创建快捷方式并启动汉化版。

如果检测到可复用的第三方大模型推理配置，工具会询问是否打开配置向导。直接选 `N` 或回车即可保持全新配置，之后也可以通过菜单 `8` 再打开向导。

## 旧版本历史数据迁移

如果你之前已经在其他版本的 Claude Desktop 或更早的中文便携版里积累了历史数据，然后再执行当前版本的菜单 `1`，可能会遇到下面几种现象：

- 中文版历史会话、Cowork 项目空间、skills 或 Code 会话看起来像“全没了”
- 会话列表已经恢复，但点开部分旧会话时没有正文内容

这通常不是数据真的丢失，而是因为旧版和当前版使用的用户数据路径与账号命名空间不同：

- 旧数据常见于 `%APPDATA%\Claude-3p`
- 当前中文版使用 `%APPDATA%\ClaudeZhCN-3p`
- 旧版活跃账号目录与当前中文版账号目录也可能不同

当前版本的菜单 `1` / 启动器已经内置自动迁移与修复逻辑，会：

- 从旧 profile 中选择历史数据更完整的一份作为迁移来源
- 将旧账号树映射到当前中文版正在读取的账号目录
- 复制旧会话的 transcript 文件，包括 Windows 长路径下的 `.claude\projects\...\<cliSessionId>.jsonl`

推荐恢复步骤：

```text
1. 完全退出中文版 Claude
2. 重新执行菜单 1
3. 再启动中文版
```

如果你之前已经在旧版本脚本上跑过一次迁移，但旧会话点开仍然没有内容，可以再按上面的步骤重跑一次；当前版本已经升级迁移标记并补上长路径 transcript 复制，通常会自动完成修复。

## 默认路径

绿色版应用：

```text
%LOCALAPPDATA%\ClaudeZhCN\Claude\Claude.exe
```

下载缓存：

```text
%LOCALAPPDATA%\ClaudeZhCN\downloads\Claude-latest.msix
```

启动器：

```text
%LOCALAPPDATA%\ClaudeZhCN\launch_claude_zh_cn.vbs
```

用户数据：

```text
%APPDATA%\ClaudeZhCN-3p
%APPDATA%\ClaudeZhCN
%APPDATA%\Claude
%APPDATA%\Claude-3p
%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude
%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude-3p
```

快捷方式：

```text
桌面\Claude zh-CN.lnk
桌面\Claude Code.lnk
开始菜单\Claude zh-CN.lnk
开始菜单\Claude Code.lnk
```

`Claude Code.lnk` 只有在本机能找到 `claude` 命令时才会自动创建。

## 菜单

英文菜单：

```text
1. Patch / update / launch zh-CN Claude
2. Check latest version
3. Locate user config/account data
4. Clean user config/account data
5. Launch patched Claude
6. Create Claude and Claude Code shortcuts
7. Full clean portable zh-CN tool files
8. Third-party model inference config wizard
9. Reapply Cowork patch and rebuild launcher
10. Repair official Claude MSIX Cowork sandbox (advanced)
11. Repair / prepare Cowork environment
0. Exit
```

中文菜单：

```text
1. 汉化 / 更新 / 启动汉化版
2. 检查版本更新
3. 定位用户配置/账号数据
4. 清理用户配置/账号数据
5. 启动汉化版 Claude
6. 创建 Claude 和 Claude Code 快捷方式
7. 完全清理绿色版文件
8. 第三方大模型推理配置向导
9. 重新应用 Cowork 补丁并重建启动器
10. 修复官方 Claude MSIX Cowork 沙箱（高级）
11. 修复 / 准备 Cowork 环境
0. 退出
```

## 更新逻辑

选项 `1` 会先检查官方最新版和本地绿色副本版本。

如果版本一致，工具会跳过下载和重建，只重新应用中文资源、用户界面设置、Cowork 兼容修复和快捷方式。

如果版本不同，工具会询问是否更新。确认后会下载官方最新版 MSIX，备份旧绿色副本，并重新生成新的中文副本。

如果官方下载接口返回 403 或版本检查失败，菜单会尝试回退到本机已安装的 Claude Desktop 继续生成绿色版。没有安装官方版时，可以稍后重试下载，或手动提供 MSIX。

## 第三方大模型推理配置向导

菜单 `8` 用来处理 Desktop 的 `Developer -> Configure Third-Party Inference[第三方大模型推理]` 配置。默认安装 / 更新不会强行导入第三方配置，只有你在向导里确认后才会写入。

向导提供几种选择：

```text
1. 保持全新：不导入、不修改第三方大模型推理配置。
2. 同步 Claude Desktop configLibrary：适合复用官方安装版里已经配置好的 Desktop 第三方大模型推理。
3. 从 Claude Code 配置生成：读取 Claude Code 里的 gateway[网关] 地址和访问令牌[token]，转换成 Desktop 可用的配置。
4. 只查看：显示检测到的配置来源和当前绿色版配置。
```

可能读取的 Desktop 配置库：

```text
%APPDATA%\Claude-3p\configLibrary
%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude-3p\configLibrary
```

可能读取的 Claude Code 配置：

```text
%USERPROFILE%\.claude\settings.json
%USERPROFILE%\.claude\settings.local.json
```

识别字段：

```text
ANTHROPIC_BASE_URL
ANTHROPIC_AUTH_TOKEN
ANTHROPIC_API_KEY
```

两种同步方式不是完全一样的：

- Desktop -> Desktop：复制同类 `configLibrary` JSON 文件，更适合已经在官方 Claude Desktop 里配置成功的用户。
- Claude Code -> Desktop：只读取 `ANTHROPIC_BASE_URL` 和访问令牌[token] / API key，再生成 Desktop gateway[网关] 配置；它不是完整搬运 Claude Code 的所有设置。

写入目标是绿色版优先使用的配置库：

```text
%APPDATA%\Claude-3p\configLibrary
```

同步前会备份目标配置库到：

```text
%LOCALAPPDATA%\ClaudeZhCN\user-data-backups
```

导入或生成配置后，工具会启用 `disableDeploymentModeChooser`，让绿色版启动时直接进入第三方大模型推理模式，减少第一次启动时的登录模式选择。敏感值在控制台输出时会打码。

如果没有检测到可复用配置，工具只会提示，不会写入空配置。

## Code[代码] / Cowork[协作] 兼容修复

Windows 版本的 Code[代码] / Cowork[协作] 页面会检测应用是否通过 MSIX / WindowsApps 路径启动。绿色版是解包运行，可能出现：

```text
Cowork requires Claude Desktop be installed with our modern installer
```

选项 `9` 会把该检测改为读取绿色版专用环境变量，并同步更新 ASAR 完整性信息与 `Claude.exe` 中记录的 ASAR hash。它用于重新应用程序补丁并重建启动器，不是常规启动前必须执行的步骤。

为了让官方 MSIX 版和中文绿色版可以同时运行，工具还会把绿色版的 Cowork VM 命名空间改成独立名称：

```text
cowork-vm-service -> ccdesk-vm-service
cowork-vm-nat     -> ccdesk-vm-nat
cowork-vm-store   -> ccdesk-vm-store
```

这会同时处理 `app.asar` 和 `resources\cowork-svc.exe`，并重建启动器。启动 `Claude zh-CN` 快捷方式时，启动器会先启动绿色版自己的 `cowork-svc.exe`，等待 `\\.\pipe\ccdesk-vm-service` 就绪后，再用 `--user-data-dir=%APPDATA%\ClaudeZhCN-3p` 打开 Claude。这样官方版已打开时，中文绿色版也不会被 Electron 单实例锁转交给官方窗口。

修复时会备份：

```text
%LOCALAPPDATA%\ClaudeZhCN\Claude\resources\app.asar.bak-before-cowork-compat-*
%LOCALAPPDATA%\ClaudeZhCN\Claude\resources\app.asar.bak-before-cowork-namespace-*
%LOCALAPPDATA%\ClaudeZhCN\Claude\resources\cowork-svc.exe.bak-before-cowork-namespace-*
%LOCALAPPDATA%\ClaudeZhCN\Claude\Claude.exe.bak-before-cowork-compat-*
%LOCALAPPDATA%\ClaudeZhCN\Claude\Claude.exe.bak-before-cowork-namespace-*
```

请通过桌面或开始菜单中的 `Claude zh-CN` 快捷方式启动。不要直接双击绿色副本里的 `Claude.exe`，否则可能绕过启动器环境变量和独立用户数据参数。

菜单 `10` 是高级修复项，只用于官方 MSIX 版在使用绿色版后出现 Cowork 启动失败的情况。它会尝试启动官方 `CoworkVMService`，并把绿色版生成的 `smol-bin.vhdx` 同步到官方 MSIX 沙箱目录。默认汉化 / 更新流程不会自动触碰官方版沙箱数据。

新版 Claude 可能会同时使用 `%LOCALAPPDATA%\Claude-3p` 和 `%APPDATA%\Claude-3p` 下的 VM 数据。工具会确保 `%APPDATA%\Claude-3p` 是真实目录，而不是 junction / symlink，并从 `%LOCALAPPDATA%\Claude-3p` 补齐新版配置 VM 所需的完整 bundle 文件，例如 `rootfs.vhdx`、`initrd`、`vmlinuz`、`smol-bin.vhdx` 和 `sessiondata.vhdx`。

## Cowork 环境修复

正常启动和更新通常不需要手动执行菜单 `11`。如果 Cowork / VM 因残留进程、HCS VM 或 bundle 文件缺失而启动失败，可以使用菜单 `11` 做定向修复。

菜单 `11` 提供两种目标：

```text
1. Repair / prepare portable zh-CN Claude
2. Repair / prepare official Claude MSIX
```

也可以直接运行命令：

```powershell
python cc_desktop_zh_cn_windows.py --prepare-cowork-switch portable
python cc_desktop_zh_cn_windows.py --prepare-cowork-switch official
```

如果你只想手动清理残留而不做完整切换准备，可以使用：

```powershell
python cc_desktop_zh_cn_windows.py --cleanup-cowork-residue
```

说明：

- `prepare-cowork-switch portable` 会优先收敛官方侧残留，再准备中文绿色版所需的 Cowork 缓存、完整 VM bundle 和启动器。
- `prepare-cowork-switch official` 会优先收敛中文侧残留，再修复官方 MSIX 侧所需的 Cowork 沙箱兼容数据。
- 当前已经验证新版中文绿色版和新版官方英文 MSIX 均可启动 VM 并执行 bash。
- 官方版退出后偶发残留 `cowork-vm-*` HCS VM 更像上游 Cowork / HCS 退出链路问题；本项目当前通过手动 `prepare` 在下次切换前做兜底收敛。

## 清理

选项 `4` 会清理用户配置 / 账号数据。清理时不是永久删除，而是移动到：

```text
%LOCALAPPDATA%\ClaudeZhCN\user-data-backups
```

这会让应用下次启动时重新创建用户数据目录，通常需要重新登录。

选项 `7` 会删除绿色版相关文件，但保留备份：

```text
%LOCALAPPDATA%\ClaudeZhCN\Claude
%LOCALAPPDATA%\ClaudeZhCN\launch_claude_zh_cn.vbs
%LOCALAPPDATA%\ClaudeZhCN\downloads
桌面\Claude zh-CN.lnk
桌面\Claude Code.lnk
开始菜单\Claude zh-CN.lnk
开始菜单\Claude Code.lnk
```

选项 `7` 不会删除账号数据，也不会删除 `%LOCALAPPDATA%\ClaudeZhCN\user-data-backups`。如果也要重置账号数据，请先使用选项 `4`。

## 文件说明

- `cc_desktop_tool.bat`：英文菜单双击入口。
- `cc_desktop_tool.ps1`：英文菜单脚本，兼容性最好。
- `cc_desktop_tool_zh.bat`：中文菜单双击入口。
- `cc_desktop_tool_zh.ps1`：中文菜单脚本。
- `cc_desktop_zh_cn_windows.py`：核心补丁脚本。
- `resources/frontend-zh-CN.json`：前端中文翻译。
- `resources/desktop-zh-CN.json`：桌面壳层中文翻译。
- `resources/statsig-zh-CN.json`：statsig 中文资源。
- `CHANGELOG.md`：版本更新记录。
- `LICENSE`：MIT License。
- `DISCLAIMER.md`：免责声明。

## 参考与致谢

本项目的中文资源整理与补丁思路参考了 [javaht/claude-desktop-zh-cn](https://github.com/javaht/claude-desktop-zh-cn)。感谢原项目作者和贡献者对 Claude Desktop 中文化实践的探索与分享。

感谢 [@chrichuang218](https://github.com/chrichuang218) 的 fork 和 PR 对翻译修正、第三方配置复用、下载回退以及 Cowork 共存修复思路提供的改进参考。本项目已在保留用户选择权和配置备份的前提下吸收相关优点。

本项目在此基础上面向 Windows 绿色版 / 便携化使用场景做了独立实现与扩展。

## 开源发布注意事项

不要提交以下内容：

- 官方安装包、MSIX、APPX。
- 解包后的官方应用目录。
- `%LOCALAPPDATA%\ClaudeZhCN` 里的运行时文件、下载缓存或备份。
- `%APPDATA%\ClaudeZhCN-3p`、`%APPDATA%\ClaudeZhCN`、`%APPDATA%\Claude`、`%APPDATA%\Claude-3p` 或 `%USERPROFILE%\.claude` 中的账号数据、访问令牌[token]、API key。
- 任何本地 `.env`、`settings.local.json`、日志、缓存。

## License

MIT. See [LICENSE](LICENSE).

## 友情链接

- [LINUX DO](https://linux.do/)
