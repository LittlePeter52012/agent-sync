# 最近同步问题汇总修复实施计划

> **执行要求：** 在当前隔离工作区内按测试驱动方式逐项实施；主分支只在完整验证通过后合并。

**目标：** 汇总最近 Codex 会话中仍未闭环的 `agent-sync`、Agent Hub、Exa 与 Multica 问题，以现有“单能力单所有者”架构为基础完成最小修复并重新同步。

**架构：** 不再新增第二套所有权系统。`agent-sync` 继续负责发现产品插件、计算每个目标的唯一有效能力所有者并分发 Hub 回退；Agent Hub 只保存无凭据的期望状态和策略；Multica 仍由显式 `agent-sync multica --apply` 单向同步技能内容。

**技术栈：** Bash、Python 3 标准库、`unittest`、JSON 策略文件、Git worktree。

**执行状态：** 已完成。`agent-sync` 修复已合并到 `main`（`551df2d`），Agent Hub 配置已合并到 `main`（`6d1c6ef`）；两端此前均已推送并与 `origin/main` 对齐。

## 全局约束

- 每次变更只通过一个接口写入，不并行修改同一外部对象。
- 产品原生插件优先时，Hub 同时抑制对应 Skill 与 MCP；插件不可确认时闭合失败并保留 Hub 回退。
- Agent Hub 不保存 Exa、Cloudflare 或其他云服务凭据。
- 所有新增设计和实施文档使用中文。
- 只处理本计划列出的已复现问题，不改动演示文稿方案及无关工具配置。

---

### 任务 1：修复所有权命令文本输出回归

**文件：**
- 修改：`tests/test_ownership_cli.py`
- 修改：`scripts/ownership.py`

- [x] 添加无 `--target`、无 `--json` 的真实 CLI 测试，断言命令退出码为 0 且输出目标和能力所有者。
- [x] 运行该测试，确认当前以 `KeyError: effective_owner` 失败。
- [x] 让文本渲染正确遍历总报告的 `targets`，同时保留单目标和单能力输出。
- [x] 运行 `tests/test_ownership_cli.py`，确认全部通过。

### 任务 2：消除交互式测试挂起并给 Multica 加超时

**文件：**
- 修改：`tests/test_deploy_remote.py`
- 修改：`tests/test_sync_multica.py`
- 修改：`scripts/sync-multica.py`

- [x] 为远端部署测试增加真实 PTY/继承 stdin 的限时回归场景，证明无输入命令不会等待终端 EOF。
- [x] 修改 fake SSH：只在归档或激活确实携带 stdin 时读取，预检命令不读取终端。
- [x] 添加 Multica 假可执行文件挂起时的测试，断言有限时间内返回明确超时错误。
- [x] 为所有 Multica 子进程统一设置有限超时并转换为 `SyncError`。
- [x] 运行两组聚焦测试。

### 任务 3：让 dry-run 与插件探测反映真实状态

**文件：**
- 修改：`tests/test_agent_doctor.py` 或新增聚焦 CLI 测试
- 修改：`bin/agent-sync`
- 修改：`scripts/verify-all.sh`
- 修改：`scripts/list-sync.sh`

- [x] 添加健康配置下 `agent-sync fix --dry-run` 不宣称存在修复动作的测试，以及存在漂移时列出具体动作的测试。
- [x] 添加计数型插件探测器测试，断言同一目标在一次 `verify`/`status` 中只解析一次所有权快照。
- [x] 让 dry-run 复用只读检查结果，只有实际漂移时显示对应动作并以可区分状态结束。
- [x] 在单个 Python 进程内缓存每目标所有权解析结果，避免“每技能 × 每目标”重复启动插件 CLI。
- [x] 运行聚焦测试并对 `status`、`verify` 做计时检查。

### 任务 4：配置 Exa 的唯一所有者和安全回退

**文件：**
- 修改：Agent Hub `mcp/shared-servers.json`
- 修改：Agent Hub `policies/tool-scopes.json`
- 修改：Agent Hub `tests/test_hub_governance.py`
- 修改：Agent Hub `TOOLING.zh-CN.md`
- 修改：Agent Hub `skills/academic-search-router/SKILL.md` 及必要引用

- [x] 添加失败治理测试：Exa 使用官方 `https://mcp.exa.ai/mcp`，Cursor 原生 `exa` 插件接管时抑制 Hub MCP，其他目标使用 Hub 回退；若 Codex 可确认原生 Exa 插件身份，再同样配置，否则保持 Hub。
- [x] 更新无凭据共享 MCP 和能力所有权策略，明确本地配置与数据发送到 Exa 云 API 是两件事。
- [x] 在学术检索路由中把 Exa 定位为高召回补充，不替代 Scholar/DOI/出版商页面的精确元数据核验。
- [x] 运行 Hub 治理测试和 `agent-sync ownership exa --json`。

### 任务 5：收敛 Multica、验证、合并与同步

**文件：**
- 只修改 Multica 已登记的 4 个 Hub 技能副本，不写入 MCP、凭据、任务历史或长期记忆。

- [x] 运行 `agent-sync multica --apply`，随后再次运行确认无漂移。
- [x] 运行 `agent-sync deps check --json`；只有已审核安全的精确版本才允许继续。
- [x] 运行 `agent-sync test`、Hub 治理测试、`agent-sync all`、严格 Runtime Doctor 和 `agent-sync verify`。
- [x] 检查两个仓库的 diff、提交历史与敏感信息边界。
- [x] 将已验证的 `agent-sync` 和 Agent Hub 提交合并到各自主分支；仅推送本次相关提交，不借机修改无关工作。

## 最终验证记录

- 2026-08-09 复核时发现 `agent-sync all` 的 FIFO 集成测试把完整同步流程硬限制为 10 秒；FIFO 本身未阻塞，但冷启动在整套测试负载下会越过该边界。现保留有限保护并将集成测试上限调整为 30 秒，聚焦回归连续 3 次通过。
- `agent-sync`：363 项 Python 测试通过，45 项 Bash 集成测试通过。
- Agent Hub：40 项治理测试通过。
- `agent-sync verify`：20/20 个共享 Skills、10/10 个共享 MCP 覆盖通过。
- 严格 Runtime Doctor：`HEALTHY`，无 findings。
- `agent-sync fix --dry-run`：无待执行修复动作。
- `agent-sync multica`：managed state 已收敛。
- Exa 所有权：Cursor 使用官方插件；Codex 与其他无原生插件目标使用 Hub 回退，不重复注册。
