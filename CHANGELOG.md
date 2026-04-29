# Changelog

## v0.2.4 - 2026-04-29

### Compatibility note

- 中文绿色版和官方英文 MSIX 版虽然已经做了 Cowork VM 命名空间隔离，但两边仍然共用同一套 Windows HCS / Hyper-V / VM 服务状态。
- 当前版本的目标是“手动 `prepare` 后可稳定切换使用”，不是“中英文同时并行稳定执行各自的 VM bash”。

### Added

- 新增 `--cleanup-cowork-residue`，用于在不误伤当前活跃窗口的前提下，手动清理绿色版 / 官方版遗留的 `cowork-svc.exe` 与 HCS VM 残留。
- 新增 `--prepare-cowork-switch portable|official`，用于在中文绿色版和官方 MSIX 版之间手动切换前，先做定向残留收敛和必要的 Cowork 准备。
- 英文与中文 PowerShell 菜单新增 `11. Prepare clean Cowork switch / 准备干净的 Cowork 切换`。

### Fixed

- 修复从旧版本或英文版历史数据切换到当前中文版并执行菜单 `1` 后，中文 profile 因路径切换和账号命名空间切换，导致旧会话、Code 会话、skills、Cowork 项目空间未被当前中文版读取的问题。
- 修复旧 Cowork 会话只恢复索引但点开没有正文的问题：迁移流程现在会复制 Windows 长路径下的 transcript 文件，例如 `.claude\projects\...\<cliSessionId>.jsonl`。
- 迁移流程现在会记录长路径复制失败；如果仍有关键文件无法复制，则不会误写“迁移已完成”标记，方便后续重新执行菜单 `1` 自动补迁。

### Changed

- 绿色版启动器现在只会清理自己的 `ccdesk-vm-*` 残留；只有在官方英文界面未运行时，才会顺手清理官方侧 `cowork-vm-*` 残留，降低互相误伤的概率。
- `prepare-cowork-switch` 采用按目标切换的清理策略：切到中文时强制收敛官方侧残留，切到官方时强制收敛中文侧残留，便于手动切换验证。
- 菜单 `1` / 中文启动器现在会自动检查并迁移旧版 `Claude-3p` / 英文版历史数据到 `%APPDATA%\ClaudeZhCN-3p`，减少旧会话、skills、Cowork 项目空间看起来“消失”的情况。

### Notes

- 当前已经验证“手动 `prepare` 后再启动对应中 / 英文”可以完成双向 Cowork 切换。
- 官方版退出后偶发遗留 `cowork-vm-*` HCS VM 仍更像上游 Cowork / HCS 退出链路问题；项目当前通过手动 `prepare-cowork-switch` 进行兜底收敛。
- 对于感觉“数据丢了”的旧版本用户，当前推荐恢复步骤是：完全退出中文版 Claude，重新执行菜单 `1`，然后再启动中文版。

## v0.2.3 - 2026-04-29

### Fixed

- 启动器现在使用独立的 `%APPDATA%\ClaudeZhCN-3p` 用户数据目录，避免官方 Claude 已打开时，中文绿色版被 Electron 单实例锁转交给官方窗口。
- 修复从 MSIX 解包时 `%40` 没有还原成 `@` 的问题，避免 `app.asar.unpacked\node_modules\@ant\claude-native` 原生模块加载失败。
- `--create-shortcuts` 会重建带独立用户数据参数的 VBS 启动器。

### Changed

- 第三方大模型推理配置默认写入中文绿色版专用的 `%APPDATA%\ClaudeZhCN-3p`，仍可通过向导从官方 Claude Desktop 或 Claude Code 同步。

## v0.2.2 - 2026-04-29

### Added

- 新增绿色版 Cowork VM 命名空间隔离：将管道、NAT 网络和存储命名从 `cowork-vm-*` 改为 `ccdesk-vm-*`，降低与官方 MSIX 版同时运行时的冲突。
- 启动器会先启动绿色版自己的 `cowork-svc.exe`，并等待 `\\.\pipe\ccdesk-vm-service` 就绪后再启动 Claude。
- 新增高级菜单项，用于在官方 MSIX 版 Cowork 受绿色版影响时手动修复官方沙箱中的 `smol-bin.vhdx`。

### Changed

- `--apply-cowork-compat` 现在会同时应用路径检测修复和 Cowork 命名空间隔离。
- 菜单停止 Claude 进程时会按精确路径清理绿色版残留 `cowork-svc.exe`，不影响官方 `CoworkVMService`。
- `--dry-run` 现在不会再创建启动器或快捷方式，只输出将要执行的操作。

### Thanks

- 感谢 [@chrichuang218](https://github.com/chrichuang218) 的 PR 对 Cowork VM 管道冲突、启动器就绪检测和官方 MSIX 沙箱问题提供实测线索。

## v0.2.1 - 2026-04-27

### Fixed

- 补齐 Code[代码] 会话筛选菜单中的硬编码英文，包括状态、项目、环境、最后活动、分组、活跃、全部、所有项目、不分组等文案。

## v0.2.0 - 2026-04-27

### Added

- 新增第三方大模型推理配置向导，用户可以选择保持全新、同步 Claude Desktop 配置，或从 Claude Code 配置生成 Desktop gateway[网关] 配置。
- 新增 Claude Desktop `configLibrary` 同步能力，同步前会备份目标配置库。
- 新增第三方配置来源检测，菜单选项 `1` 在检测到可复用配置时会询问是否打开向导。
- 英文菜单和中文菜单都加入下载 / 版本检查失败后的本机已安装 Claude 回退流程。

### Changed

- 项目展示名调整为 `WIN CC Desktop zh-CN Portable`，强调 Windows、中文绿色版、可与官方安装版共存。
- 默认安装 / 更新不再自动导入 Third-Party Inference[第三方大模型推理] 配置，避免影响希望保持全新环境的用户。
- 第三方配置导入或生成后，会启用 `disableDeploymentModeChooser`，减少首次启动时的登录模式选择。
- 完全清理绿色版文件时保留 `user-data-backups`，避免误删备份。
- 优化 README，补充汉化、跳过登录模式选择、配置向导和共存机制说明。

### Fixed

- 修正一批典型机翻问题，包括 token[词元]、Bearer[令牌认证]、OAuth[开放授权]、MCP[模型上下文协议]、Webhook[被动接口] 等术语。
- 修复多处 Claude、Code、Cowork 等产品名与中文之间缺少空格的问题。
- 修正部分设置页小字说明和第三方推理配置文案，使其更符合中文用户习惯。

### Thanks

- 感谢 [javaht/claude-desktop-zh-cn](https://github.com/javaht/claude-desktop-zh-cn) 提供中文化实践参考。
- 感谢 [@chrichuang218](https://github.com/chrichuang218) 的 fork 对翻译修正、配置复用和下载回退思路提供改进参考。

## v0.1.0

- 首个公开版本。
- 支持生成 Windows 中文绿色版 CC Desktop。
- 支持与官方 Claude Desktop 共存。
- 支持自动创建桌面 / 开始菜单快捷方式。
- 支持清理绿色版文件和备份用户配置 / 账号数据。
