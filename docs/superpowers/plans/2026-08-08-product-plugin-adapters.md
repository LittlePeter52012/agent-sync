# 多产品插件发现适配器实施计划

> **面向智能体执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项执行本计划。所有步骤使用复选框跟踪。

**目标：** 为当前受管 Agent 产品增加只读、可测试、失败安全的插件启用状态适配器，使未来安装已映射官方插件后能够自动去重。

**架构：** 每个产品适配器只负责把权威启用状态和插件清单转换成统一的 `PluginSource`，不负责语义判断。能力映射仍由核心解析器审核；适配器证据不确定时返回结构化不确定状态并保留 Hub。目标 Profile 和 AntiGravity 三个表面分别处理。

**技术栈：** Python 3 标准库、`subprocess`、`json`、`sqlite3`、`unittest`、现有本地产品 CLI。

## 全局约束

- 用户文档和实施说明使用中文。
- 只读插件状态，不安装、禁用、更新或删除插件。
- 缓存目录、扩展目录或商城位置单独存在时不能证明插件已启用。
- 只扫描策略中声明的官方插件 ID，避免把普通编辑器扩展误判为 Agent 能力包。
- 任何解析错误、CLI 超时或 Profile 歧义都保留 Hub，并报告不确定状态。
- 不读取或输出插件凭据、全局存储中的秘密项、账号状态或插件绝对安装路径。
- 每个适配器先测试失败，再写最小实现；一个适配器失败不得拖垮其他目标。

---

## 文件职责

- `scripts/plugin_adapters.py`：产品适配器及统一安全解析辅助函数。
- `scripts/capability_resolver.py`：调用适配器并把不确定证据并入目标解析结果。
- `tests/test_plugin_adapters.py`：使用临时目录和假 CLI 输出测试每个产品。
- `tests/test_capability_resolver.py`：验证 Profile 和目标键传递正确。
- `scripts/agent-doctor.py`：显示适配器不确定状态，不重复解析产品文件。
- `README.md`：中文说明支持范围、证据来源和失败安全行为。

### 任务 1：建立统一适配器边界

**文件：**

- 新建：`scripts/plugin_adapters.py`
- 新建：`tests/test_plugin_adapters.py`
- 修改：`scripts/capability_resolver.py`

**接口：**

```python
@dataclass(frozen=True)
class PluginDiscovery:
    sources: tuple[PluginSource, ...]
    uncertain: tuple[str, ...] = ()
```

公开函数签名固定为
`discover_plugins(target: str, home: Path, declared_ids: frozenset[str], *, runner=subprocess.run) -> PluginDiscovery`。

`declared_ids` 来自已经审核的能力映射。适配器不得遍历并报告所有无关插件。

- [ ] **步骤 1：写入未知目标和异常隔离的失败测试**

| 测试名 | 固定断言 |
|---|---|
| `test_unknown_target_returns_uncertain_without_sources` | `sources=()`，`uncertain` 含目标名 |
| `test_adapter_exception_becomes_uncertain_and_keeps_other_targets_usable` | 异常目标返回不确定；随后已支持目标仍能返回自己的结果 |
| `test_empty_declared_ids_skips_product_probe` | 假 runner 调用次数为 0，结果为空且无不确定项 |

- [ ] **步骤 2：运行测试并确认模块尚不存在**

```bash
python3 -m unittest tests.test_plugin_adapters -v
```

- [ ] **步骤 3：实现统一分派和清单读取辅助函数**

只实现以下最小辅助函数：

```python
def _safe_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None

def _declared(value: str, declared_ids: frozenset[str]) -> bool:
    normalized = value.casefold()
    short = normalized.split("@", 1)[0]
    return normalized in declared_ids or short in declared_ids
```

`_normalized_plugin_id()` 必须正确保留 `@scope/package`，只去除末尾版本；
`_manifest_source()` 只读取指定插件根中的 Skills 和 MCP 清单，不递归扫描邻接包。

把现有 Cursor、Codex 实现迁移到适配器模块，但保持公开
`active_plugin_sources()` 兼容包装器。

- [ ] **步骤 4：运行现有与新增测试并确认通过**

```bash
python3 -m unittest tests.test_plugin_adapters tests.test_capability_resolver -v
```

- [ ] **步骤 5：提交适配器边界**

```bash
git add scripts/plugin_adapters.py scripts/capability_resolver.py \
  tests/test_plugin_adapters.py tests/test_capability_resolver.py
git commit -m "refactor: isolate product plugin discovery"
```

### 任务 2：接入 Claude 与 Gemini CLI

**文件：**

- 修改：`scripts/plugin_adapters.py`
- 修改：`tests/test_plugin_adapters.py`

**接口：**

- Claude 命令：`claude plugin list --json`，只接受 `enabled: true` 且 ID 已声明的记录；安装根来自 `installPath`。
- Gemini 命令：`gemini extensions list --output-format json`，只接受明确启用且名称已声明的记录；缺失启用字段时返回不确定，不猜测。

- [ ] **步骤 1：写入 Claude 活动、禁用和损坏输出的失败测试**

| 测试名 | 固定输入与断言 |
|---|---|
| `test_claude_uses_enabled_declared_plugin_with_install_path` | CLI 返回 `enabled=true`、已声明 ID 和临时安装目录；结果包含一个 `claude-cli` 来源 |
| `test_claude_ignores_disabled_plugin` | 同一记录 `enabled=false`；结果不含来源且不误报活动 |
| `test_claude_malformed_json_is_uncertain` | stdout 为 `{`；`uncertain` 含 `claude:plugin-state-uncertain` |
| `test_claude_does_not_probe_unmapped_plugins` | `declared_ids` 为空；runner 调用次数为 0 |

- [ ] **步骤 2：运行 Claude 测试并确认失败**

```bash
python3 -m unittest tests.test_plugin_adapters.PluginAdapterTests.test_claude_uses_enabled_declared_plugin_with_install_path -v
```

- [ ] **步骤 3：实现 Claude 最小适配器并运行测试至通过**

CLI 超时固定为 10 秒，返回码非零、JSON 非列表或安装路径不可读时添加
`claude:plugin-state-uncertain`。

- [ ] **步骤 4：写入 Gemini 活动、禁用、空输出和含糊输出的失败测试**

| 测试名 | 固定输入与断言 |
|---|---|
| `test_gemini_uses_explicitly_enabled_declared_extension` | JSON 记录包含已声明名称、`enabled=true` 和路径；结果包含一个 `gemini-cli` 来源 |
| `test_gemini_ignores_explicitly_disabled_extension` | `enabled=false`；结果不含来源 |
| `test_gemini_blank_output_is_uncertain_for_declared_plugin` | stdout 为空；返回 `gemini:plugin-state-uncertain` |
| `test_gemini_missing_enabled_field_is_uncertain` | 记录没有 enabled；不产生来源并返回不确定项 |

- [ ] **步骤 5：实现 Gemini 最小适配器并运行聚焦测试**

```bash
python3 -m unittest tests.test_plugin_adapters -v
```

- [ ] **步骤 6：提交 Claude 与 Gemini 适配器**

```bash
git add scripts/plugin_adapters.py tests/test_plugin_adapters.py
git commit -m "feat: discover Claude and Gemini plugins"
```

### 任务 3：接入 OpenCode 与 AntiGravity App/CLI

**文件：**

- 修改：`scripts/plugin_adapters.py`
- 修改：`tests/test_plugin_adapters.py`

**接口：**

- OpenCode：读取 `~/.config/opencode/opencode.json` 的活动 `plugin` 数组；只对
  已声明 ID 解析名称和可选安装清单，不读取 `provider`、认证或 MCP 环境变量。
- AntiGravity App/CLI：分别读取
  `~/.gemini/antigravity/plugins/*/plugin.json` 与
  `~/.gemini/antigravity-cli/plugins/*/plugin.json`；只有 `disabled` 不为 true
  且名称已声明时视为活动。

- [ ] **步骤 1：写入 OpenCode 版本后缀、作用域包和无关插件失败测试**

| 测试名 | 固定输入与断言 |
|---|---|
| `test_opencode_normalizes_versioned_declared_plugin` | `plugin=["sample@latest"]`、声明 `sample`；活动 ID 为 `sample` |
| `test_opencode_preserves_scoped_package_identity` | `plugin=["@scope/sample@1.2.3"]`；活动 ID 为 `@scope/sample` |
| `test_opencode_ignores_provider_and_secret_bearing_mcp_fields` | provider/MCP 中放置哨兵秘密值；结果和日志不含哨兵值 |
| `test_opencode_malformed_config_is_uncertain` | 配置为 `{`；返回 `opencode:plugin-state-uncertain` |

- [ ] **步骤 2：运行测试确认失败，再实现只读 OpenCode 适配器**

只读取顶层 `plugin`；插件根无法确定时仍可返回空清单的活动 `PluginSource`，
供已审核语义映射匹配，不扫描 `node_modules` 中的其他包。

- [ ] **步骤 3：写入 AntiGravity 目标隔离失败测试**

| 测试名 | 固定输入与断言 |
|---|---|
| `test_antigravity_app_and_cli_use_separate_plugin_roots` | 两个根写入不同插件；各目标只能发现自己的插件 |
| `test_antigravity_disabled_plugin_is_not_active` | `plugin.json` 含 `disabled=true`；不产生来源 |
| `test_antigravity_cache_without_plugin_manifest_is_not_active` | 只有缓存目录没有 `plugin.json`；不产生来源 |

- [ ] **步骤 4：实现最小 AntiGravity App/CLI 适配器并运行测试**

```bash
python3 -m unittest tests.test_plugin_adapters -v
```

- [ ] **步骤 5：提交 OpenCode 与 AntiGravity App/CLI 适配器**

```bash
git add scripts/plugin_adapters.py tests/test_plugin_adapters.py
git commit -m "feat: discover OpenCode and Antigravity plugins"
```

### 任务 4：接入 VS Code Profile 与 AntiGravity IDE

**文件：**

- 修改：`scripts/plugin_adapters.py`
- 修改：`tests/test_plugin_adapters.py`
- 修改：`tests/test_capability_resolver.py`

**接口：**

- VS Code 默认目标使用 `code --list-extensions --show-versions` 的已安装清单，
  并从只读 `state.vscdb` 的 `extensionsIdentifiers/disabled` 键排除明确禁用项。
- `vscode:<profile-id>` 必须读取该 Profile 对应的扩展与禁用状态；无法确定
  Profile 归属时报告不确定，不能回退到默认 Profile 推测。
- AntiGravity IDE 使用产品自带 `antigravity-ide --list-extensions
  --show-versions` 与自己的 `state.vscdb`，不得借用 VS Code 状态。

- [ ] **步骤 1：写入已安装但禁用、Profile 隔离和数据库损坏的失败测试**

| 测试名 | 固定输入与断言 |
|---|---|
| `test_vscode_excludes_declared_disabled_extension` | CLI 列出插件，SQLite 禁用键包含同一 ID；不产生活动来源 |
| `test_vscode_profile_does_not_inherit_default_plugin_state` | 默认 Profile 活动、指定 Profile 禁用；指定 Profile 不产生来源 |
| `test_vscode_missing_profile_state_is_uncertain` | 指定不存在的 Profile；返回带 Profile ID 的不确定项 |
| `test_antigravity_ide_uses_its_own_cli_and_state_database` | VS Code 与 AntiGravity IDE 返回不同清单；IDE 结果只含 IDE 插件 |
| `test_extension_state_database_error_is_uncertain` | SQLite 文件不是数据库；保留 Hub 并返回不确定项 |

- [ ] **步骤 2：运行聚焦测试并确认失败**

```bash
python3 -m unittest tests.test_plugin_adapters.PluginAdapterTests.test_vscode_excludes_declared_disabled_extension -v
```

- [ ] **步骤 3：实现只读扩展清单与禁用状态解析**

只执行参数数组形式的子进程，不经 shell。SQLite 查询固定为：

```sql
SELECT value FROM ItemTable WHERE key = 'extensionsIdentifiers/disabled'
```

解析后只保留小写扩展 ID；不得枚举或输出数据库中的其他键和值。

- [ ] **步骤 4：修正目标键传递并运行全部适配器测试**

`resolve_target_policy()` 必须把完整目标键传给适配器，使
`vscode:<profile-id>` 不被提前截断为 `vscode`。

```bash
python3 -m unittest tests.test_plugin_adapters tests.test_capability_resolver -v
```

- [ ] **步骤 5：提交 VS Code 与 AntiGravity IDE 适配器**

```bash
git add scripts/plugin_adapters.py scripts/capability_resolver.py \
  tests/test_plugin_adapters.py tests/test_capability_resolver.py
git commit -m "feat: discover profile-scoped editor plugins"
```

### 任务 5：接入诊断、文档并完成全产品审计

**文件：**

- 修改：`scripts/agent-doctor.py`
- 修改：`tests/test_agent_doctor.py`
- 修改：`README.md`
- 修改：私有 Hub `TOOLING.zh-CN.md`

**接口：**

- Doctor 把适配器 `uncertain` 转成 `plugin-state-uncertain`，作用域为目标本地；
  不确定状态只有在能力映射声明了该插件时才出现。
- `agent-sync ownership --json` 必须列出每个受管目标的证据类别，不列绝对路径。

- [ ] **步骤 1：写入诊断失败测试**

| 测试名 | 固定断言 |
|---|---|
| `test_doctor_reports_declared_plugin_state_uncertain_without_suppressing_hub` | finding 为本地目标所有；共享 MCP coverage 仍为 `hub-managed` |
| `test_doctor_ignores_unrelated_unmapped_editor_extensions` | 未在能力合同声明的扩展不产生插件所有权 finding |

- [ ] **步骤 2：运行测试确认失败，再实现最小 Doctor 接线**

```bash
python3 -m unittest tests.test_agent_doctor -v
```

- [ ] **步骤 3：更新中文支持矩阵和安全边界说明**

文档明确列出每个产品的证据源、Profile 边界、无法确定时保留 Hub，以及
“本机执行不等于数据留在本机”。

- [ ] **步骤 4：运行公共完整测试、语法、隐私和 Hub 治理测试**

```bash
agent-sync test
agent-sync audit
python3 -m unittest discover -s "$AGENT_HUB_ROOT/tests" -v
```

- [ ] **步骤 5：运行全产品只读所有权审计**

```bash
agent-sync ownership --json
agent-sync doctor --json --scope all
agent-sync verify --strict --scope managed
```

验收：当前没有已映射官方插件的目标继续由 Hub 管理；Cursor Cloudflare 由
官方插件管理；已禁用 Claude 插件不拥有任何 Hub 能力；OpenCode、Gemini、
VS Code 和 AntiGravity 的无关插件不产生误判。

- [ ] **步骤 6：提交诊断与文档**

```bash
git add scripts/agent-doctor.py tests/test_agent_doctor.py README.md
git commit -m "docs: audit plugin ownership across products"
```

私有 Hub 只提交本任务明确修改的中文说明，不纳入其他未跟踪文件。
