# 能力唯一归属核心实施计划

> **面向智能体执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项执行本计划。所有步骤使用复选框跟踪。

**目标：** 为 `agent-sync` 增加统一、可解释、可回退的能力所有权合同，并完成 Cursor Cloudflare 的首个去重迁移。

**架构：** 保留 `capability_resolver.py` 作为唯一裁决源，在现有精确 Skill/MCP 去重之上增加按能力声明的语义映射和用户显式选择。同步器只删除或恢复 Hub 所有的配置；产品插件只读检测，冲突由 Doctor 报告。新增 `ownership` 命令直接渲染同一个解析结果，不复制裁决逻辑。

**技术栈：** Python 3 标准库、Bash、`unittest`、JSON、现有 Agent Hub 策略文件。

## 全局约束

- 设计稿、实施方案和用户文档使用中文；代码标识、命令与状态值保留英文。
- 同一目标、同一能力最多一个有效提供方能力包。
- 用户显式选择高于默认官方插件优先策略。
- 未经核验的语义相似不得触发自动屏蔽。
- 普通同步不得调用模型，不得安装、禁用、认证或删除产品插件。
- 不读取、输出或提交账号凭据；执行位置和数据目的地分别记录。
- 保留旧 `prefer_marketplace` 读取兼容，但新的 Hub 策略只写新合同。
- 每项行为变更必须先观察对应测试按预期失败，再写最小实现。

---

## 文件职责

- `scripts/capability_resolver.py`：解析能力合同、插件状态和唯一所有者，输出同步与诊断共用的结构化结果。
- `scripts/ownership.py`：把解析结果渲染为文本或 JSON，不自行判断所有权。
- `scripts/config_inventory.py`：把能力裁决转换为共享 MCP 覆盖状态。
- `scripts/agent-doctor.py`：把所有权冲突、所有者缺失和不确定状态转换为带作用域的诊断。
- `bin/agent-sync`：注册 `ownership` 命令。
- `tests/test_capability_resolver.py`：验证新策略合同、优先级和可逆回退。
- `tests/test_ownership_cli.py`：验证公开命令的稳定输出和隐私边界。
- `tests/test_config_inventory.py`：验证覆盖状态与所有权状态一致。
- `tests/test_agent_doctor.py`：验证受管冲突使 Doctor 报警。
- `tests/test_promote_mcp.py`：验证实际同步删除和恢复 Hub MCP。
- `README.md`：公开说明能力唯一归属、迁移兼容和命令用法。
- 私有 Hub `policies/tool-scopes.json`：保存 Cloudflare 的首个能力映射和目标选择。

### 任务 1：解析能力所有权合同

**文件：**

- 修改：`scripts/capability_resolver.py`
- 修改：`tests/test_capability_resolver.py`

**接口：**

- 输入：现有 `resolve_target_policy()` 的 `target`、`home`、`hub` 和可注入
  `runner` 参数。
- 新增：`CapabilityDecision` 数据类，字段为 `capability`、`target`、`selection`、`desired_owner`、`effective_owner`、`state`、`reason`、`suppressed_skills`、`suppressed_mcp`、`fallback_owner`。
- 扩展：`TargetResolution.capabilities: Mapping[str, CapabilityDecision]` 与 `TargetResolution.findings: tuple[str, ...]`。
- 保持：`suppressed_skills`、`suppressed_mcp`、`skill_owners`、`mcp_owners`、`mcp_collisions` 的现有语义和调用兼容性。

- [ ] **步骤 1：写入 `auto` 完整覆盖的失败测试**

```python
def test_auto_selects_active_reviewed_plugin_and_suppresses_full_hub_bundle(self):
    self.write_capability_policy(
        selection="auto",
        plugin_id="cloudflare",
        skills=["cloudflare"],
        mcp=["cloudflare-local"],
    )
    self.cursor_plugin("cloudflare", "abc", skill="cloudflare", servers={})
    self.enable_cursor_plugin("cloudflare", "abc")

    result = resolve_target_policy("cursor", self.home, self.hub)

    self.assertEqual(result.suppressed_skills, frozenset({"cloudflare"}))
    self.assertEqual(result.suppressed_mcp, frozenset({"cloudflare-local"}))
    self.assertEqual(result.capabilities["cloudflare"].state, "plugin-owned")
```

- [ ] **步骤 2：运行测试并确认因尚未解析 `capability_ownership` 而失败**

运行：

```bash
python3 -m unittest tests.test_capability_resolver.CapabilityResolverTests.test_auto_selects_active_reviewed_plugin_and_suppresses_full_hub_bundle -v
```

预期：失败，`cloudflare-local` 不在 `suppressed_mcp` 中，或结果没有 `capabilities`。

- [ ] **步骤 3：写入显式选择、回退和无效策略的失败测试**

覆盖以下独立测试，输入与断言固定如下：

| 测试名 | 输入 | 必须断言 |
|---|---|---|
| `test_explicit_hub_reports_active_plugin_conflict_without_suppression` | `selection=hub` 且映射插件活动 | `state=ownership-conflict`、`effective_owner=hub`、Hub Skill/MCP 均不在屏蔽集合 |
| `test_explicit_plugin_missing_reports_owner_missing_without_hub_fallback` | `selection=plugin:cloudflare` 且插件缺失 | `state=owner-missing`、`effective_owner is None`、Hub 组件被屏蔽 |
| `test_auto_restores_hub_when_plugin_disappears` | `selection=auto` 且插件缺失 | `state=hub-managed`、屏蔽集合为空、`fallback_owner=hub` |
| `test_target_selection_requires_declared_provider` | `selection=target:local` 但未声明目标提供方 | `state=owner-missing` 且 finding 指明 `target:local` |
| `test_disabled_selection_suppresses_declared_hub_bundle` | `selection=disabled` | `state=disabled-by-policy`，映射的 Skill/MCP 全部被屏蔽 |
| `test_invalid_capability_reference_is_reported_before_resolution` | 映射不存在的 Hub MCP | finding 指明未知 MCP，且不屏蔽任何组件 |
| `test_legacy_prefer_marketplace_remains_readable` | 仅存在旧 `prefer_marketplace` | 保持旧测试的 `plugin-owned` 结果 |

- [ ] **步骤 4：运行新增测试并确认失败原因分别来自缺少新合同和状态模型**

运行：

```bash
python3 -m unittest tests.test_capability_resolver -v
```

预期：新测试失败，旧测试继续通过。

- [ ] **步骤 5：实现最小合同解析与裁决**

实现以下公开结构：

```python
@dataclass(frozen=True)
class CapabilityDecision:
    capability: str
    target: str
    selection: str
    desired_owner: str
    effective_owner: str | None
    state: str
    reason: str
    suppressed_skills: frozenset[str]
    suppressed_mcp: frozenset[str]
    fallback_owner: str | None

@dataclass(frozen=True)
class TargetResolution:
    # 保留原字段
    capabilities: Mapping[str, CapabilityDecision]
    findings: tuple[str, ...] = ()
```

新增私有函数并保持每个函数单一职责。合同读取函数采用以下确定实现：

```python
def _capability_contract(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    value = policy.get("capability_ownership", {})
    return value if isinstance(value, dict) else {}

def _selection_for_target(contract: Mapping[str, Any], target: str) -> str:
    targets = contract.get("targets", {})
    target_policy = targets.get(target, {}) if isinstance(targets, dict) else {}
    if isinstance(target_policy, dict) and isinstance(target_policy.get("selection"), str):
        return target_policy["selection"]
    return "auto"
```

`_resolve_capability()` 接收能力名、目标策略、Hub 组件集合和活动插件集合，严格
按本任务测试表中的五种选择模式返回 `CapabilityDecision`；不得读取目标配置或
执行文件写入。

`auto` 只有在 `official_plugins` 中的 ID 当前活动时才选择插件；`hub` 不屏蔽
Hub；`plugin:*` 缺失时返回 `owner-missing`；`disabled` 屏蔽映射中的 Hub
组件。所有 Hub 名称必须先与 manifest/shared MCP 对照验证。

- [ ] **步骤 6：运行解析器测试并确认全部通过**

运行：

```bash
python3 -m unittest tests.test_capability_resolver -v
```

预期：全部通过，无警告和异常堆栈。

- [ ] **步骤 7：提交核心裁决**

```bash
git add scripts/capability_resolver.py tests/test_capability_resolver.py
git commit -m "feat: resolve single capability ownership"
```

### 任务 2：增加所有权查看命令

**文件：**

- 新建：`scripts/ownership.py`
- 新建：`tests/test_ownership_cli.py`
- 修改：`bin/agent-sync`

**接口：**

- 命令：`agent-sync ownership [CAPABILITY] [--target TARGET] [--json]`。
- 输出只包含能力名、目标、选择、期望/实际所有者、状态、原因、屏蔽项、插件证据类型和回退所有者。
- 不输出 MCP URL 查询参数、环境变量、请求头、账号 ID 或本机插件缓存绝对路径。

- [ ] **步骤 1：写入命令 JSON 输出的失败测试**

```python
def test_ownership_command_reports_one_target_without_secrets(self):
    result = self.run_cli("ownership", "cloudflare", "--target", "cursor", "--json")
    payload = json.loads(result.stdout)
    self.assertEqual(payload["capability"], "cloudflare")
    self.assertEqual(payload["target"], "cursor")
    self.assertEqual(payload["effective_owner"], "plugin:cloudflare")
    self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)
    self.assertNotIn(str(self.home), result.stdout)
```

- [ ] **步骤 2：运行测试并确认因命令不存在而失败**

运行：

```bash
python3 -m unittest tests.test_ownership_cli -v
```

预期：失败，`agent-sync` 报告未知命令 `ownership`。

- [ ] **步骤 3：实现最小渲染器和入口**

`ownership.py` 调用 `resolve_target_policy()`，文本模式逐个目标输出一行，JSON
模式使用 `dataclasses.asdict()` 后经过允许字段筛选。`bin/agent-sync` 的帮助和
`case` 分支注册：

```bash
ownership)
    require_hub
    exec python3 "$SCRIPTS/ownership.py" "$@"
    ;;
```

- [ ] **步骤 4：运行命令测试并确认全部通过**

```bash
python3 -m unittest tests.test_ownership_cli -v
```

- [ ] **步骤 5：提交公开查看命令**

```bash
git add bin/agent-sync scripts/ownership.py tests/test_ownership_cli.py
git commit -m "feat: explain capability ownership"
```

### 任务 3：统一同步覆盖与 Doctor 冲突诊断

**文件：**

- 修改：`scripts/config_inventory.py`
- 修改：`scripts/agent-doctor.py`
- 修改：`tests/test_config_inventory.py`
- 修改：`tests/test_agent_doctor.py`
- 修改：`tests/test_promote_mcp.py`

**接口：**

- `resolve_mcp_coverage()` 继续返回 `state` 与 `owner`，新增能力决策对应的
  `ownership-conflict`、`owner-missing`、`disabled-by-policy` 状态。
- Doctor 对 `ownership-conflict` 与 `owner-missing` 产生 `scope=managed`、
  `owner=hub` 的 finding；`unmapped-candidate` 和 `plugin-state-uncertain` 为
  可见但非破坏性诊断。

- [ ] **步骤 1：写入覆盖状态和 Doctor 的失败测试**

| 测试名 | 固定断言 |
|---|---|
| `test_hub_selection_with_active_plugin_is_ownership_conflict` | coverage 的 `state` 为 `ownership-conflict`、`owner` 为 `hub` |
| `test_required_plugin_missing_is_owner_missing` | coverage 的 `state` 为 `owner-missing`，且没有把 Hub MCP 误标为插件所有 |
| `test_disabled_capability_is_accepted_without_mcp_registration` | coverage 的 `state` 为 `disabled-by-policy`，Doctor 不报告 MCP 缺失 |
| `test_doctor_fails_managed_scope_for_duplicate_effective_owner` | finding 包含能力名，`scope=managed`、`owner=hub` |

Doctor 关键断言：

```python
finding = next(item for item in report["findings"] if "cloudflare" in item["message"])
self.assertEqual(finding["scope"], "managed")
self.assertEqual(finding["owner"], "hub")
```

- [ ] **步骤 2：运行聚焦测试并确认失败**

```bash
python3 -m unittest \
  tests.test_config_inventory \
  tests.test_agent_doctor.AgentDoctorTests.test_doctor_fails_managed_scope_for_duplicate_effective_owner \
  -v
```

- [ ] **步骤 3：实现共用状态转换和诊断**

在 `resolve_mcp_coverage()` 中先查能力决策，再处理按名称的 Hub MCP。Doctor
只读取 `TargetResolution.capabilities` 和 coverage，不再重新推断插件优先级。
把可接受状态集合扩展为 `hub-managed`、`plugin-owned`、`target-owned`、
`disabled-by-policy`；冲突和缺失保持不可接受。

- [ ] **步骤 4：增加同步删除与恢复的端到端失败测试**

| 测试名 | 固定设置与断言 |
|---|---|
| `test_sync_removes_full_hub_bundle_for_reviewed_cursor_plugin` | 写入活动 Cursor 插件和 `auto` 映射；同步后 Cursor 用户 MCP 不含 `cloudflare-local` |
| `test_sync_restores_full_hub_bundle_after_cursor_plugin_disappears` | 删除测试夹具中的活动插件证据后再同步；Cursor 用户 MCP 重新含有 `cloudflare-local` |

第一项必须断言 `cloudflare-local` 从 Cursor 用户 MCP 删除；第二项必须断言插件
证据移除后它重新出现。

- [ ] **步骤 5：运行端到端测试并确认失败，再完成最小同步接线**

运行：

```bash
python3 -m unittest tests.test_promote_mcp.PromoteMcpTests.test_sync_removes_full_hub_bundle_for_reviewed_cursor_plugin -v
```

如果现有 `sync-mcp.sh` 已经消费 `suppressed_mcp`，不得重复实现删除逻辑；只修正
解析结果和断言。

- [ ] **步骤 6：运行任务相关测试并确认全部通过**

```bash
python3 -m unittest tests.test_config_inventory tests.test_agent_doctor tests.test_promote_mcp -v
```

- [ ] **步骤 7：提交诊断与同步接线**

```bash
git add scripts/config_inventory.py scripts/agent-doctor.py \
  tests/test_config_inventory.py tests/test_agent_doctor.py tests/test_promote_mcp.py
git commit -m "fix: enforce one effective capability owner"
```

### 任务 4：迁移 Cloudflare、更新中文说明并完成本机验证

**文件：**

- 修改：`README.md`
- 修改：`docs/superpowers/specs/2026-07-30-plugin-marketplace-dedup-exclusions-design.md`
- 修改：私有 Hub `policies/tool-scopes.json`
- 修改：私有 Hub `tests/test_hub_governance.py`
- 修改：私有 Hub `TOOLING.zh-CN.md`

**接口：**

- 私有策略采用设计稿中的 `capability_ownership` 结构。
- Cursor 为 `auto`，Codex 为 `hub`；其他目标没有活动且已核验官方提供方时保留 Hub。
- 旧英文设计文档只增加醒目的中文“已被替代”说明，不重写历史内容。

- [ ] **步骤 1：先写 Hub 治理失败测试**

```python
def test_capability_ownership_references_known_hub_assets(self):
    policy = load_policy()
    cloudflare = policy["capability_ownership"]["capabilities"]["cloudflare"]
    self.assertEqual(cloudflare["hub"]["skills"], ["cloudflare"])
    self.assertEqual(cloudflare["hub"]["mcp"], ["cloudflare-local"])
    self.assertEqual(cloudflare["targets"]["cursor"]["selection"], "auto")
    self.assertEqual(cloudflare["targets"]["codex"]["selection"], "hub")
```

- [ ] **步骤 2：运行测试并确认因策略尚未迁移而失败**

```bash
python3 -m unittest tests.test_hub_governance.HubGovernanceTests.test_capability_ownership_references_known_hub_assets -v
```

- [ ] **步骤 3：使用 `apply_patch` 迁移私有策略和中文说明**

删除 Cursor 旧 `prefer_marketplace.cloudflare` 写入项，添加新能力合同；不改变
共享 MCP 定义、不改变凭据、不改变 Codex 禁止插件列表。

- [ ] **步骤 4：运行 Hub 治理、公共测试和隐私审计**

```bash
python3 -m unittest discover -s tests -v
agent-sync test
agent-sync audit
```

- [ ] **步骤 5：执行本机同步并检查唯一所有者**

```bash
agent-sync all
agent-sync ownership cloudflare --json
agent-sync trace mcp cloudflare-local --json
agent-sync verify --strict --scope managed
```

验收：Cursor 的 `cloudflare-local` 为 `plugin-owned` 且不在用户 MCP 文件中；
Codex、Claude、Gemini、OpenCode、AntiGravity 和 VS Code 在没有等价官方插件时
仍为 Hub 管理；Doctor 不报告 Cloudflare 双所有者。

- [ ] **步骤 6：重复同步验证幂等性**

保存受管配置的安全哈希，第二次运行 `agent-sync all` 后确认哈希不变；不得把
产品自己改写的 OAuth 状态纳入比较。

- [ ] **步骤 7：分别提交公共工具与私有 Hub 的精确改动**

公共工具：

```bash
git add README.md docs/superpowers/specs/2026-07-30-plugin-marketplace-dedup-exclusions-design.md
git commit -m "docs: explain single capability ownership"
```

私有 Hub：

```bash
git add policies/tool-scopes.json tests/test_hub_governance.py TOOLING.zh-CN.md
git commit -m "feat: select one provider per capability"
```

不得把其他未跟踪文件纳入任何提交。
