# Changelog

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
