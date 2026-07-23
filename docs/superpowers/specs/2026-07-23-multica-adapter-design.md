# Multica 显式适配器设计

## 目标

为 `agent-sync` 增加一个可选的 Multica 控制面适配器：

```text
agent-sync multica
agent-sync multica --apply
```

默认命令只比较私人 Hub 中声明的期望状态与 Multica 工作区状态。只有显式传入
`--apply` 才允许更新 Multica。现有 `agent-sync sync`、`all`、`fix`、`update
--sync` 都不得隐式调用该适配器。

## 配置边界

适配器只读取：

```text
$AGENT_HUB_ROOT/multica/desired-state.json
```

公开仓库只提供通用 schema、示例和实现，不保存工作区 ID、Agent 名称、Squad
名称、本机路径、账号、端点或凭据。

配置允许声明：

- 最低 Multica CLI 版本；
- 从私人 Hub 发布到 Multica 工作区的 Skill 名称与本地相对源目录；
- Agent 名称到 Skill 名称的完整绑定集合；
- Squad 名称、可选旧名称、Leader、描述和 instructions 文件。

名称解析必须唯一。零匹配或多匹配时停止，禁止猜测 UUID。

## 管理范围

允许读取和收敛：

- 工作区 Skill 的 `SKILL.md` 内容及支持文件；
- 指定 Agent 的完整 Skill 绑定集合；
- 指定 Squad 的名称、Leader、描述和 instructions。

明确不管理：

- runtime、模型、登录态、Keychain、API key；
- MCP、产品插件或扩展；
- Issue、评论、任务历史和 Git 仓库；
- 未列入 allowlist 的 Skill、Agent 或 Squad。

因此 Multica 仍是跨 Agent 的任务控制面，而不是所有工具配置的总仓库。共享 Skills
和共享 MCP 的权威来源仍是私人 Hub；产品专属能力仍由所属产品管理。

## 运行语义

默认只读检查：

1. 校验 JSON schema、相对路径和最低 CLI 版本；
2. 从 Multica CLI 读取 Skills、Agents、Squads；
3. 按唯一名称解析对象；
4. 比较托管字段；
5. 无差异返回 0；存在差异返回 2；配置或运行错误返回 1。

`--apply` 按以下顺序执行：

1. 创建或更新 allowlist Skills，并收敛支持文件；
2. 重新读取 Skills，再以完整集合更新指定 Agent 的 Skill 绑定；
3. 更新指定 Squads；
4. 再运行一次只读检查；仍有差异则失败。

输出只显示对象名称和字段级动作，不打印 Skill 正文、instructions、UUID、端点或
配置值。

## Skill 文件规则

Skill 源目录必须位于 `$AGENT_HUB_ROOT` 内。`SKILL.md` 作为主内容；目录中的其他
普通文件作为支持文件，路径相对 Skill 根目录。同步器：

- 新增或更新不同的文件；
- 删除 Multica 中多余的托管 Skill 支持文件；
- 不跟随逃出 Skill 根目录的符号链接；
- 不读取 `.git`、缓存和临时文件。

## Squad 与 Agent 规则

Agent Skill 绑定采用替换语义，只对配置中明确列出的 Agent 执行。配置必须给出该
Agent 的完整期望 Skill 名称集合。

Squad 可以提供一个 `previous_name` 支持受控改名；当前名和旧名的并集必须只解析到
一个对象。Leader 必须按 Agent 名称唯一解析。

## 安全与幂等

- 默认无远端写入；
- `--apply` 是唯一远端变更开关；
- 所有写入都使用 Multica CLI，不直接访问数据库或服务端 API；
- 每次写入后重新读取并验证；
- 相同配置重复执行不得产生更新；
- `agent-sync all` 的本地同步语义保持不变。

## 验证要求

自动化测试必须证明：

- 默认命令发现 drift 但不调用任何写命令；
- `--apply` 只调用 allowlist 中的 Skill、Agent Skill 和 Squad 写命令；
- 重名、缺失对象、越界源路径和旧版本会在写入前失败；
- runtime、MCP、Issue、评论和 Git 命令不会被构造；
- `agent-sync all` 不会触发 Multica；
- 公开仓库不含私人名称、路径、端点或凭据。
