# agent-sync 双语 README 与 GitHub 主页改版设计

## 1. 目标

把当前“功能完整但信息密度过高”的 README 改造成面向真实使用者的产品入口：访客在首屏看懂 `agent-sync` 是什么、解决什么问题、支持哪些 Agent，并能在 60 秒内完成安装和第一次同步。

成功标准：

- GitHub 默认主页使用英文，顶部提供 `English | 简体中文` 双向切换；中文内容位于 `README.zh-CN.md`。
- 首屏在桌面和移动端都能明确表达“一个私有 Hub 同步多个 Agent”的核心价值。
- 视觉具有适度科技感，但不依赖动画、外部脚本或大面积高饱和霓虹。
- 新用户优先看到问题、价值、安装和快速开始；高级策略随后按需展开。
- 所有能力、支持范围、测试数量和安全承诺均来自仓库现有实现，不制造下载量、用户量或性能数据。

## 2. 调研结论

当前 README 约 561 行、25 KB，技术说明充分，但存在三个主页问题：

1. 首屏只有标题、短句和工具列表，无法快速解释它与普通 MCP 配置同步器的差异。
2. 中英文混排，缺少明确语言入口，也不利于搜索和传播。
3. 核心价值、快速开始、产品所有权模型与高级内部策略处在近似同一视觉层级，新用户需要阅读较长内容才能建立完整认识。

同类成熟 CLI 和 Agent 管理工具的有效共性是：居中的产品首屏、清晰的一句话价值主张、少量可信徽章、问题导向说明、紧邻首屏的快速开始、可见的支持范围，以及把高级参考放到后半部分。GUI 产品常使用截图；`agent-sync` 是 CLI 与配置控制面，因此更适合用拓扑横幅和真实终端输出，而不是虚构界面截图。

## 3. 视觉方向

采用用户确认的 **Network Topology B+**：

- 主色：深海军蓝、青色和靛紫；浅色主题使用极浅蓝灰背景，深色主题使用蓝黑背景。
- 核心图形：中央 `Private Hub` 节点与两侧 Agent 节点连接，线路带轻微发光和电路纹理；不使用复杂三角形装饰。
- 横幅同时提供浅色与深色 SVG，通过 `<picture>` 和 `prefers-color-scheme` 自动选择。
- 横幅中的文字保持少而稳定：产品名、英文价值主张、`Skills · MCP · Rules · Ownership` 和代表性 Agent 节点。
- README 正文不使用大量 HTML 卡片，避免 GitHub 移动端和无障碍阅读退化；科技感主要集中在横幅、徽章、拓扑图和终端示例。

建议首屏：

1. 右上或顶部居中语言切换。
2. 双主题 SVG 横幅。
3. 一句话：`One private Hub for Skills, MCP servers, rules, and capability ownership across your AI agents.`
4. 可信徽章：版本、MIT、Python + Shell、local-first；只有存在真实工作流时才显示 CI 徽章。
5. 导航：`Why · Quick start · Supported tools · Safety model · Commands`。

## 4. 信息架构

英文 `README.md` 与中文 `README.zh-CN.md` 使用相同章节和相同相对顺序：

1. Hero 与语言切换
2. `Why agent-sync`：先说明多 Agent 配置分散、插件与 Hub 重复、凭据边界难维护三个问题
3. `What makes it different`：统一 Skills/MCP/Rules、单能力单所有者、保留本地凭据、Doctor/verify/fix、显式来源提升
4. `60-second quick start`：安装、`sources`、`sync --from ... --dry-run`、`sync`、`doctor`
5. `Supported tools`：按 Agent 表面列出 Skills/MCP/Rules 覆盖，不用只有名称的长列表
6. `How it works`：简化 Mermaid 数据流图
7. `Safety by design`：公共工具仓库与私有 Hub 分离、产品插件归产品管理、单次操作只用一个接口、本地执行不等于数据不出本机
8. `Common workflows`：四到六个高频命令
9. `Command reference`：紧凑表格，不在正文重复同一命令多次
10. `Advanced`：Antigravity 多表面、Multica、远端部署、依赖维护与策略合同使用折叠块或链接
11. `Contributing / License`

现有 README 中仍准确但不适合作为首页主叙事的细节不会删除事实含义；实施时压缩重复段落，并把必须保留的高级内容放到折叠区。本次不新增独立高级参考文档，避免扩大双语维护范围。

## 5. 双语策略

- `README.md` 是默认英文主页；`README.zh-CN.md` 是完整中文版本，不是摘要。
- 两份 README 顶部互相链接，章节顺序和命令示例保持一致。
- 命令、文件名、配置键、协议状态和产品官方名称保留英文；解释性文字本地化。
- SVG 横幅只保留英文稳定短语，避免维护两套图片；中文价值说明紧随横幅出现。
- 修改 README 结构时同时更新两种语言，测试检查主要章节和互链存在。

## 6. GitHub 仓库元数据

同步改善 GitHub 的可发现性：

- Description 改为准确、可搜索的一句话，覆盖 Skills、MCP、Rules、plugin ownership 和跨 Agent 同步。
- 添加经过实现验证的 Topics，例如 `agent-skills`、`mcp`、`ai-agents`、`configuration-management`、`codex`、`cursor`、`claude-code`、`gemini-cli`、`antigravity`、`opencode`。
- 不设置虚构产品网站；主页链接保持为空，直到有独立文档站。
- 不创建空 Release 或伪造 CI 状态来填充徽章。

## 7. 文件范围

计划修改：

- `README.md`
- 新增 `README.zh-CN.md`
- 新增 `assets/readme/agent-sync-hero-light.svg`
- 新增 `assets/readme/agent-sync-hero-dark.svg`
- 更新 `.gitignore`，排除本地 `.superpowers/` 视觉草稿
- 新增 `tests/test_readme.py`，检查语言互链、关键章节、SVG 本地资源和事实声明
- 通过 GitHub API 更新仓库 Description 与 Topics

不修改同步器生产逻辑、Agent Hub 私有配置、凭据、插件安装状态或版本号。

## 8. 验证与验收

- Markdown 链接和本地图片路径有效。
- 两份 README 的核心章节、命令示例和语言互链一致。
- SVG 不含外部脚本、远程资源、私有路径或敏感信息，并在浅色/深色背景下分别可读。
- `agent-sync test`、隐私审计和新增 README 测试通过。
- 使用 GitHub 渲染结果或本地等价预览检查桌面与窄屏首屏层级。
- GitHub Description、Topics 和默认分支上的 README 与本地提交一致。

## 9. 明确不做

- 不把 README 变成营销长页或加入夸张比较表。
- 不使用访问者计数器、虚假下载量、虚假性能数据或无来源“最好”声明。
- 不引入 JavaScript、动态图床、外部字体或追踪资源。
- 不把私有 Agent Hub 内容、真实 MCP 列表、账号、密钥或机器路径复制进公开仓库。
