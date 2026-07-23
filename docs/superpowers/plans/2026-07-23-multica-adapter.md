# Multica 显式适配器实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> and superpowers:test-driven-development to implement this plan task-by-task.

**目标：** 增加默认只读、显式应用的 Multica 适配器，使私人 Hub 可以声明并验证
Multica Skills、Agent Skill 绑定和 Squad 协议，同时保持现有本地同步流程不变。

**架构：** 使用 Python 标准库实现声明式 reconciler。Shell 入口只分发参数；私人
Hub 保存 JSON 期望状态和正文文件；Multica CLI 是唯一远端读写接口。

**技术栈：** Bash 3.2、Python 3 标准库、`unittest`、JSON、Multica CLI。

### 任务 1：先锁定只读与安全边界

**文件：**

- 新建：`tests/test_sync_multica.py`
- 新建：`scripts/sync-multica.py`

- [x] 先写失败测试，覆盖默认只读、drift 返回 2、重名/越界/旧版本拒绝、禁止构造
  非托管命令。
- [x] 运行 `python3 -m unittest tests/test_sync_multica.py -v`，确认因实现缺失而红。
- [x] 实现最小配置加载、版本检查、CLI 读取和差异规划。
- [x] 重跑聚焦测试至绿。

### 任务 2：实现显式应用与最终复核

**文件：**

- 修改：`tests/test_sync_multica.py`
- 修改：`scripts/sync-multica.py`

- [x] 先写 `--apply` 失败测试，覆盖 Skill 正文/文件、Agent 完整绑定、Squad 更新与
  应用后复核。
- [x] 确认测试按预期失败。
- [x] 实现 Skills → 刷新 → Agent 绑定 → Squads → 最终检查的最小写入流程。
- [x] 验证重复执行幂等，并确保输出不含 UUID、正文和配置值。

### 任务 3：接入 CLI，但不接入 all

**文件：**

- 修改：`tests/test_sync_multica.py`
- 修改：`bin/agent-sync`

- [x] 先写入口失败测试，证明 `agent-sync multica [--apply]` 正确分发，`all`
  永不调用适配器，未知参数失败。
- [x] 实现入口并更新 help。
- [x] 运行新旧单元测试。

### 任务 4：公开说明、示例与版本

**文件：**

- 新建：`examples/multica/desired-state.json`
- 修改：`README.md`
- 修改：`CHANGELOG.md`
- 修改：`VERSION`

- [x] 提供不含私人数据的最小示例。
- [x] 说明默认只读、退出码、`--apply`、配置边界和不受管理的对象。
- [x] 将版本升级到 1.7.0。

### 任务 5：真实环境验收

- [x] 在私人 Hub 建立 `multica/desired-state.json`，执行默认只读检查。
- [x] 使用临时 Hub 制造 drift，确认返回 2 且远端不变。
- [x] 对真实期望状态执行一次 `--apply`，再确认只读返回 0。
- [ ] 运行全部单元测试、`agent-sync test`、`verify --strict` 和隐私审计。
- [ ] 合并、推送公开 agent-sync 和私人 Agent Hub，并核对远端 SHA。
