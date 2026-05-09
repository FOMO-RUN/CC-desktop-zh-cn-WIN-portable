# Changelog

## v0.3.10 - 2026-05-09

### Changed

- 补齐内置 Skill 的显示层中文名称和说明，保留 `schedule`、`setup-cowork`、`consolidate-memory`、`context` 等内部命令 ID 为英文，避免破坏默认技能列表和协作窗口匹配。
- 优化顶部菜单提示、技能页入口和任务列表页文案，修复最近使用进入任务列表后 `Tasks`、`Active`、`Archived`、`All` 等细节回退问题。

### Fixed

- 创建 / 重建快捷方式时会清理误指向绿色版 `Claude.exe` 的裸 `Claude.lnk` 入口，避免绕过 `Claude zh-CN` 启动器、独立用户数据和 Cowork 兼容环境。
- 清理逻辑只会处理目标位于 `%LOCALAPPDATA%\ClaudeZhCN` 下的直开快捷方式，不会误删官方 Claude 安装版快捷方式。

## v0.3.9 - 2026-05-09

### Fixed

- 进一步收窄前端硬编码替换范围，不再全局替换 `Skill`、`Skills`、`Search`、`Back`、`All`、`Author`、`Description` 等短字符串，避免误伤前端内部枚举、分类或数据匹配。
- 保留更具体的 `children:`、`aria-label:`、`title:` 和句子级替换，降低中文化对功能数据的影响。

## v0.3.8 - 2026-05-09

### Fixed

- 修复 v0.3.6/v0.3.7 中把 `schedule`、`setup-cowork`、`context` 等内置技能 ID 当作显示文案替换，导致默认内置技能列表为空的问题。
- 保留内置技能 ID 原文，只翻译明确的显示层文案，避免破坏前端数据匹配。

## v0.3.7 - 2026-05-09

### Changed

- 调整技能术语口径：独立 `Skill` / `Skills` 显示为 `技能（Skill）` / `技能（Skills）`，操作入口继续使用 `浏览技能`、`创建技能`、`上传技能` 等自然中文。
- 保留 `Skills/*/SKILL.md` 等规范路径写法，避免把专业文件结构误翻译。

## v0.3.6 - 2026-05-09

### Changed

- 优化顶部工具栏 tooltip：`Search`、`Collapse sidebar`、`Back` 等提示现在会显示为中文。
- 统一技能页文案，将技能相关入口中的“技巧”改为“技能”，并补齐创建、上传、浏览技能等菜单文案。
- 补齐内置技能列表的名称与描述中文兜底，包括 `schedule`、`setup-cowork`、`consolidate-memory` 和 `context`。
- 优化最近使用进入任务列表后的 `Tasks`、`Active`、`Archived`、`All` 等页面文案。

## v0.3.5 - 2026-05-09

### Fixed

- 修复 Python 3.7.x / 3.8.x / 3.9.x 运行 Cowork / VM 兼容补丁时，因 `zip(..., strict=False)` 触发 `TypeError: zip() takes no keyword arguments` 的问题。
- 将脚本类型注解降级为 Python 3.7 兼容写法，避免 `list[...]`、`dict[...]`、`str | None` 等新式注解在旧版 Python 或会求值注解的工具中造成兼容性问题。

## v0.3.4 - 2026-05-08

### Added

- 新增 Claude Code 管理菜单：查看安装状态、安装/修复、更新和完全卸载。
- 支持检测 Claude Code 安装来源：官方原生安装、WinGet、npm 全局包和 PATH 中的未知来源。
- 安装默认使用官方 CMD 原生安装器，失败后回退 npm；每一步都会二次检测 `claude --version`，避免安装器提示完成但实际不可用。

### Changed

- 主菜单扩展为 14 项，`10` 为 Claude Code 管理，清理/OAuth/API 模式入口顺延到 `11`-`14`。
- 完全卸载 Claude Code 时，程序卸载和 `~\.claude` 等配置/授权/MCP 数据删除分开确认，避免误删工作状态。
- 删除 Claude Code 配置目录时会先处理 Windows 只读文件；若仍被占用，会提示残留路径而不是中断菜单。
- 下载到的 Claude Desktop MSIX 会先校验格式；如果网络/代理返回 HTML 错误页，不再显示 Python traceback，而是提示检查网络并删除无效缓存。
- 首次初始化现在会再次清理 `disableDeploymentModeChooser`，默认保留 Anthropic 账号登录和 API 模式两个入口。
- 登录页和开发者设置里的 `Gateway` 相关文案改为面向用户更清楚的 `API 模式` / `API 地址`。
- Claude Code 安装失败时会提示网络/代理可能连不到官方发布源，尤其是 `ECONNREFUSED`、版本获取失败或超时这类情况。

## v0.3.3 - 2026-05-08

### Fixed

- 修复设置页新版本 i18n key 未进入 `ion-dist/i18n/zh-CN.json`，导致 `Avatar`、`Instructions for Claude`、`Preferences` 等仍显示英文的问题。
- 补齐 API 模式隐私说明、通知偏好、Artifacts、Skills / Connectors 迁移提示、本地会话和自动 PR 设置页文案。
- 重新验证 `--apply-locale` 会把上述 key 写入真实安装目录，并在关闭 Claude 后清理绿色版前端缓存。

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

- 首次安装 / 初始化现在会自动安装或修复中文绿色版，并从 Claude Code 预置 Desktop API 配置，但默认保留 Anthropic 登录和 API 模式两个入口。
- 新增显式 API 模式切换：菜单 `13` 进入 API 模式，菜单 `14` 退出强制 API 模式并保留 API 配置。
- 登录页 / 首次模式选择页新增运行时中文兜底，补齐 `You can change this later by signing out.`、`Or continue with Gateway`、Privacy Policy 及拆分链接文本等英文残留。

### Changed

- 启动器固定使用 `%APPDATA%\ClaudeZhCN-3p` 作为绿色版用户数据目录，避免被误写成 `CLAUDE_USER_DATA_DIR=1` 或复用官方数据空间。
- 从 Claude Code 生成 API 配置时，不再默认隐藏账号登录入口；需要直进 API 模式时由用户显式选择。
- 退出 API 模式时只移除强制模式字段和直进 API 模式字段，继续保留 `configLibrary` 中的 API 地址、凭据和认证方式。

### Fixed

- 修复生成 API 配置元数据时，首次写入没有备份文件会触发 `UnboundLocalError: backup` 的问题。
- 修复首次安装后同步了 API 配置但界面没有显示可选 API 模式入口的问题。
- 改进前端缓存清理和登录页补丁注入，减少更新后旧英文或旧模式页面残留。

## v0.2.5 - 2026-05-06

### Fixed

- 修复 Python 3.11 及更低版本解析 OAuth 回调注册命令时可能出现的 `SyntaxError: f-string expression part cannot include a backslash`。
- 改进 OAuth 回调启动器路径生成逻辑，保持对较旧 Python 版本的兼容性。

## v0.2.4 - 2026-05-05

### Changed

- 重新设计中英文 PowerShell 菜单，按初始化、启动、检查更新、更新重汉化、API 模式、导入同步、Cowork/VM 修复、诊断、快捷方式和清理卸载分组。
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

- API 配置默认写入中文绿色版专用的 `%APPDATA%\ClaudeZhCN-3p`，仍可通过向导从官方 Claude Desktop 或 Claude Code 同步。

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

- 新增 API 模式配置向导，用户可以选择保持全新、同步 Claude Desktop 配置，或从 Claude Code 配置生成 Desktop API 配置。
- 新增 Claude Desktop `configLibrary` 同步能力，同步前会备份目标配置库。
- 新增 API 配置来源检测，菜单选项 `1` 在检测到可复用配置时会询问是否打开向导。
- 英文菜单和中文菜单都加入下载 / 版本检查失败后的本机已安装 Claude 回退流程。

### Changed

- 项目展示名调整为 `WIN CC Desktop zh-CN Portable`，强调 Windows、中文绿色版、可与官方安装版共存。
- 默认安装 / 更新不再自动导入 API 模式配置，避免影响希望保持全新环境的用户。
- API 配置导入或生成后，可通过显式入口直进 API 模式。
- 完全清理绿色版文件时保留 `user-data-backups`，避免误删备份。
- 优化 README，补充汉化、API 模式、配置向导和共存机制说明。

### Fixed

- 修正一批典型机翻问题，包括 token[词元]、Bearer、OAuth、MCP、Webhook 等术语。
- 修复多处 Claude、Code、Cowork 等产品名与中文之间缺少空格的问题。
- 修正部分设置页小字说明和 API 配置文案，使其更符合中文用户习惯。

### Thanks

- 感谢 [javaht/claude-desktop-zh-cn](https://github.com/javaht/claude-desktop-zh-cn) 提供中文化实践参考。
- 感谢 [@chrichuang218](https://github.com/chrichuang218) 的 fork 对翻译修正、配置复用和下载回退思路提供改进参考。

## v0.1.0

- 首个公开版本。
- 支持生成 Windows 中文绿色版 CC Desktop。
- 支持与官方 Claude Desktop 共存。
- 支持自动创建桌面 / 开始菜单快捷方式。
- 支持清理绿色版文件和备份用户配置 / 账号数据。
