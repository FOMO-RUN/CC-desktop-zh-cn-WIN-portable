# Changelog

## v0.3.2 - 2026-05-08

### Changed

- 深度润色 Claude Code / Cowork 运行界面文案，修复 `跑步`、`跑了`、`努力`、`型号`、`绕过权限` 等机翻问题。
- 将模型菜单、推理强度菜单、操作权限菜单统一为 `模型`、`推理强度`、`操作权限`、`执行前询问`、`自动应用编辑`、`仅计划`、`跳过确认` 等更清晰的表达。
- 清理旧补丁残留的 `Code[代码]`、`Cowork[协作]`、`New session[新会话]`，改为自然中文 `代码`、`协作`、`新会话`。
- 修复 `Webhook[被动接口]`、`OAuth[开放授权]`、`Bearer[令牌认证]`、`MCP[模型上下文协议]` 等词典式括号标注。
- 修正 `康威`、`Claude 码`、`编目`、`旁路模式`、`工作量更改`、`基础架构` 等不自然或错误翻译。

### Fixed

- 重新应用中文资源时，现在会迁移旧硬编码补丁留下的中英混排标签。
- 登录页运行时中文兜底补充权限确认按钮文案，例如 `Always allow in this project (local)`。

## v0.3.1 - 2026-05-07

### Added

- 首次安装 / 初始化现在会自动安装或修复中文绿色版，并从 Claude Code 预置 Desktop 网关配置，但默认保留 Anthropic 登录和 Gateway 两种模式选择。
- 新增显式 3P/API 模式切换：菜单 `12` 进入 3P/API 模式，菜单 `13` 退出强制 3P/API 模式并保留网关配置。
- 登录页 / 首次模式选择页新增运行时中文兜底，补齐 `You can change this later by signing out.`、`Or continue with Gateway`、Privacy Policy 新标签页提示等英文残留。

### Changed

- 启动器固定使用 `%APPDATA%\ClaudeZhCN-3p` 作为绿色版用户数据目录，避免被误写成 `CLAUDE_USER_DATA_DIR=1` 或复用官方数据空间。
- 从 Claude Code 生成网关配置时，不再默认强制跳过登录模式选择；需要直进 3P/API 时由用户显式选择。
- 退出 3P/API 模式时只移除强制模式字段和跳过模式选择字段，继续保留 `configLibrary` 中的 网关 地址、凭据和认证方式。

### Fixed

- 修复生成第三方推理配置元数据时，首次写入没有备份文件会触发 `UnboundLocalError: backup` 的问题。
- 修复首次安装后同步了 API / 网关配置但界面没有显示可选 Gateway 入口的问题。
- 改进前端缓存清理和登录页补丁注入，减少更新后旧英文或旧模式页面残留。

## v0.2.5 - 2026-05-06

### Fixed

- 修复 Python 3.11 及更低版本解析 OAuth 回调注册命令时可能出现的 `SyntaxError: f-string expression part cannot include a backslash`。
- 改进 OAuth 回调启动器路径生成逻辑，保持对较旧 Python 版本的兼容性。

## v0.2.4 - 2026-05-05

### Changed

- 重新设计中英文 PowerShell 菜单，按初始化、启动、检查更新、更新重汉化、第三方推理、导入同步、Cowork/VM 修复、诊断、快捷方式和清理卸载分组。
- 中文 PowerShell 菜单现在为每个主选项和高风险子选项补充用途说明，减少误操作。
- 常见运行输出改为中文，包括版本检查、路径诊断、初始化、OAuth 回调、快捷方式、同步和 Cowork/VM 修复提示。
- 初始化流程现在只迁移旧绿色版数据和做基础检查；官方 Desktop 与绿色版之间的账号、OAuth、3P 数据同步必须通过菜单明确选择。
- 导入 / 同步配置支持官方 Desktop -> 绿色版、绿色版 -> 官方 Desktop、自选来源/目标、单独同步 `configLibrary`，写入前会备份目标轻量数据。
- 配置同步默认排除 `vm_bundles`，避免 Cowork / VM 大文件被复制出多份。
- Cowork / VM 修复拆成子菜单：重新应用兼容补丁、修复绿色版 runtime bundle、清理绿色版残留、官方 MSIX 高级修复和路径大小诊断。
- 优化计划任务、自定义页面、项目页和对话记录视图的中文文案。
- 将 Code 中误译的 `Branch` 从“分行”修正为“分支”，将 `Fork` 从“叉子”修正为“分叉”。
- 补齐 `Pinned`、`New project`、`Personal plugins`、`Browse plugins`、`Connectors`、`Skills` 等未汉化或机翻味较重的界面文字。

### Added

- 新增双开 / OAuth 登录修复入口：可备份当前 `claude://` 协议处理器，临时指向汉化版启动器，登录完成后恢复。
- 启动器现在会转发浏览器传入的 `claude://...` 回调参数，并同时保留绿色版 `--user-data-dir=%APPDATA%\ClaudeZhCN-3p`。
- 新增旧绿色版用户数据迁移检查，复制缺失的轻量配置和会话数据，但不自动导入官方 Desktop 数据。

## v0.2.3 - 2026-04-29

### Fixed

- 启动器现在使用独立的 `%APPDATA%\ClaudeZhCN-3p` 用户数据目录，避免官方 Claude 已打开时，中文绿色版被 Electron 单实例锁转交给官方窗口。
- 修复从 MSIX 解包时 `%40` 没有还原为 `@` 的问题，避免 `app.asar.unpacked\node_modules\@ant\claude-native` 原生模块加载失败。
- `--create-shortcuts` 会重建带独立用户数据参数的 VBS 启动器。

### Changed

- 第三方大模型推理配置默认写入中文绿色版专用的 `%APPDATA%\ClaudeZhCN-3p`，仍可通过向导从官方 Claude Desktop 或 Claude Code 同步。

## v0.2.2 - 2026-04-29

### Added

- 新增绿色版 Cowork VM 命名空间隔离：将管道、NAT 网络和存储名从 `cowork-vm-*` 改为 `ccdesk-vm-*`，降低与官方 MSIX 版同时运行时的冲突。
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

- 补齐 Code 会话筛选菜单中的硬编码英文，包括状态、项目、环境、最后活动、分组、活跃、全部、所有项目、不分组等文案。

## v0.2.0 - 2026-04-27

### Added

- 新增第三方大模型推理配置向导，用户可以选择保持全新、同步 Claude Desktop 配置，或从 Claude Code 配置生成 Desktop 网关配置。
- 新增 Claude Desktop `configLibrary` 同步能力，同步前会备份目标配置库。
- 新增第三方配置来源检测，菜单选项 `1` 在检测到可复用配置时会询问是否打开向导。
- 英文菜单和中文菜单都加入下载 / 版本检查失败后的本机已安装 Claude 回退流程。

### Changed

- 项目展示名调整为 `WIN CC Desktop zh-CN Portable`，强调 Windows、中文绿色版、可与官方安装版共存。
- 默认安装 / 更新不再自动导入 Third-Party Inference 第三方大模型推理配置，避免影响希望保持全新环境的用户。
- 第三方配置导入或生成后，会启用 `disableDeploymentModeChooser`，减少首次启动时的登录模式选择。
- 完全清理绿色版文件时保留 `user-data-backups`，避免误删备份。
- 优化 README，补充汉化、跳过登录模式选择、配置向导和共存机制说明。

### Fixed

- 修正一批典型机翻问题，包括 token[词元]、Bearer、OAuth、MCP、Webhook 等术语。
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
