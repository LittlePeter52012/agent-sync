# Agent Sync Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent Sync plugin-aware, target-excludable, security-conscious, and truthful about capability health.

**Architecture:** Add one shared capability resolver used by synchronization and diagnostics. Product adapters provide active plugin capabilities, policy controls suppression and exclusions, and every health surface uses enabled-state plus normalized transport checks instead of independent name counts.

**Tech Stack:** Python 3 standard library, Bash 3.2-compatible scripts, JSON/TOML configuration, `unittest`, native product CLIs.

## Global Constraints

- Product-managed plugin files and enabled state are read-only.
- Plugin cache presence alone is not active evidence.
- Exact duplicate suppression is automatic; semantic aliases require policy.
- Concrete secrets and private paths are never printed.
- Hub templates contain placeholders, not raw credentials.
- Every production behavior is introduced through a failing regression test.
- Existing tool-only MCP entries and unrelated local Skills remain untouched.

---

### Task 1: Unified plugin capability and policy resolver

**Files:**
- Create: `scripts/capability_resolver.py`
- Create: `tests/test_capability_resolver.py`

**Interfaces:**
- Produces: `PluginSource`, `McpIdentity`, `TargetPolicy`,
  `active_plugin_sources(target, home, runner)`,
  `resolve_target_policy(target, home, hub, runner)`.
- Consumes: Hub `manifest.yaml`, `mcp/shared-servers.json`, and
  `policies/tool-scopes.json`.

- [ ] **Step 1: Write failing tests**

Cover cache-only Cursor plugins, active log-backed Cursor plugins, explicit
local plugins, Codex CLI JSON, Claude CLI JSON, exact Skill overlap, exact MCP
name/transport overlap, conditional precedence, unconditional exclusions, and
plugin removal restoring an empty suppression set.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_capability_resolver -v
```

Expected: import failure because `capability_resolver.py` does not exist.

- [ ] **Step 3: Implement the resolver**

Use immutable dataclasses for public records, normalized casefolded names,
secret-free URL normalization, bounded subprocess calls, and fail-open uncertain
state. Parse only enabled plugin records.

- [ ] **Step 4: Verify GREEN**

Run the command from Step 2 and expect all resolver tests to pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/capability_resolver.py tests/test_capability_resolver.py
git commit -m "feat: resolve product plugin capability ownership"
```

### Task 2: Plugin-aware Skill synchronization

**Files:**
- Modify: `scripts/lib.sh`
- Modify: `scripts/sync-skills.sh`
- Modify: `scripts/list-sync.sh`
- Modify: `scripts/verify-all.sh`
- Modify: `tests/test_antigravity_surfaces.py`
- Modify: `tests/test_agent_doctor.py`

**Interfaces:**
- Consumes: `resolve_target_policy()` from Task 1.
- Produces: target-keyed Skill reconciliation and ownership rows.

- [ ] **Step 1: Write failing tests**

Add a Cursor marketplace Cloudflare fixture proving the Hub symlink is skipped
and a pre-existing Hub symlink is removed, while Codex and every other target
retain the Hub link. Add a second fixture with no active plugin proving Cursor
restores the link.

- [ ] **Step 2: Verify RED**

Run the two new focused tests and confirm Cursor still receives the duplicate
Hub symlink.

- [ ] **Step 3: Implement target-keyed Skill reconciliation**

Add `skill_target_specs` to `lib.sh`. Query the resolver once per target. Remove
only symlinks whose real path points into the Hub and whose names are suppressed.
Never delete a product directory or unrelated symlink.

- [ ] **Step 4: Verify GREEN**

Run the focused tests plus:

```bash
python3 -m unittest tests.test_antigravity_surfaces tests.test_agent_doctor -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/lib.sh scripts/sync-skills.sh scripts/list-sync.sh scripts/verify-all.sh tests/test_antigravity_surfaces.py tests/test_agent_doctor.py
git commit -m "feat: honor product plugin ownership for shared skills"
```

### Task 3: MCP enabled-state, transport, exclusions, and ownership

**Files:**
- Modify: `scripts/config_inventory.py`
- Modify: `scripts/merge-mcp.py`
- Modify: `scripts/merge-mcp-codex.py`
- Modify: `scripts/sync-mcp.sh`
- Modify: `scripts/sync-mcp-claude.py`
- Replace compatibility use of: `scripts/cursor_plugin_mcp.py`
- Modify: `scripts/trace-config.py`
- Modify: `tests/test_config_inventory.py`
- Modify: `tests/test_promote_mcp.py`

**Interfaces:**
- Consumes: `resolve_target_policy()` and canonical transport normalization.
- Produces: `resolve_mcp_coverage(target, home, hub)` with states
  `hub-managed`, `plugin-owned`, `excluded`, `disabled`, `drifted`, `missing`.

- [ ] **Step 1: Write failing tests**

Add fixtures for disabled shared MCP, wrong HTTP endpoint, wrong stdio command,
same transport under a plugin alias, conditional policy alias, target exclusion,
plugin disappearance restoration, and trace output that includes plugin
ownership.

- [ ] **Step 2: Verify RED**

Run the focused inventory and promotion tests. Confirm the current code accepts
disabled/wrong transports and omits plugin ownership from trace.

- [ ] **Step 3: Implement normalized coverage and reconciliation**

Filter disabled entries, compare normalized transport, skip suppressed
canonical entries during merge, prune previously Hub-managed suppressed
entries, and restore them when suppression disappears. Keep target-only entries.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_config_inventory tests.test_promote_mcp -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/config_inventory.py scripts/merge-mcp.py scripts/merge-mcp-codex.py scripts/sync-mcp.sh scripts/sync-mcp-claude.py scripts/cursor_plugin_mcp.py scripts/trace-config.py tests/test_config_inventory.py tests/test_promote_mcp.py
git commit -m "fix: verify and reconcile effective MCP ownership"
```

### Task 4: Truthful Verify, Status, Doctor, and rule checks

**Files:**
- Modify: `scripts/agent-doctor.py`
- Modify: `scripts/verify-all.sh`
- Modify: `scripts/list-sync.sh`
- Modify: `scripts/sync-rules.sh`
- Modify: `tests/test_agent_doctor.py`

**Interfaces:**
- Consumes: Skill and MCP ownership rows from Tasks 2 and 3.
- Produces: consistent human/JSON health reports and exact managed-rule checks.

- [ ] **Step 1: Write failing tests**

Add tests proving disabled/drifted MCPs, active plugin duplicates, missing
managed rules, duplicate managed rules, unsupported policy targets, and
plugin-state uncertainty all produce findings and strict nonzero status.

- [ ] **Step 2: Verify RED**

Run the focused Doctor tests and confirm at least the missing-rule and
marketplace-plugin cases incorrectly pass.

- [ ] **Step 3: Replace independent name counting**

Make list, verify, trace, and Doctor render the shared resolver's ownership
states. Verify exact normalized managed-rule content once per declared target.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_agent_doctor -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/agent-doctor.py scripts/verify-all.sh scripts/list-sync.sh scripts/sync-rules.sh tests/test_agent_doctor.py
git commit -m "fix: make health reports reflect effective capability state"
```

### Task 5: Privacy coverage, secret routing, and file permissions

**Files:**
- Modify: `scripts/privacy-audit.sh`
- Modify: `scripts/merge-mcp.py`
- Modify: `scripts/sync-mcp-claude.py`
- Modify: `scripts/agent-doctor.py`
- Modify: `tests/test_privacy_boundary.py`
- Modify: `tests/test_agent_doctor.py`
- Modify: `tests/test_promote_mcp.py`

**Interfaces:**
- Consumes: `secrets.targets.<target>.allow_secret_donors`.
- Produces: secret-free findings and mode `0600` for sensitive target configs.

- [ ] **Step 1: Write failing tests**

Add tests for token-shaped values under `docs/` and `tests/`, a token plus a
placeholder on the same line, group-readable secret-bearing config, denied
donor propagation, and permission hardening after sync.

- [ ] **Step 2: Verify RED**

Run the focused privacy, Doctor, and promotion tests and confirm the current
privacy scan and permission checks miss the fixtures.

- [ ] **Step 3: Implement full-tree scanning and permissions**

Scan tracked and untracked working-tree files while excluding `.git`, caches,
and generated build artifacts. Expand known token patterns without printing
matches. Enforce `0600` after writing concrete MCP environment/header fields.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_privacy_boundary tests.test_agent_doctor tests.test_promote_mcp -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/privacy-audit.sh scripts/merge-mcp.py scripts/sync-mcp-claude.py scripts/agent-doctor.py tests/test_privacy_boundary.py tests/test_agent_doctor.py tests/test_promote_mcp.py
git commit -m "fix: harden local secret and privacy boundaries"
```

### Task 6: Complete test entry point and Codex idempotency

**Files:**
- Modify: `bin/agent-sync`
- Modify: `scripts/reconcile-codex-orchestration.py`
- Modify: `tests/test_reconcile_codex_orchestration.py`
- Modify: `tests/test_agent_doctor.py`

**Interfaces:**
- Produces: `agent-sync test` running Python plus Bash suites and stable Codex
  semantic reconciliation.

- [ ] **Step 1: Write failing tests**

Assert the test command dispatches `unittest discover` before the Bash suite.
Add a Codex fixture with product-formatted blank lines and assert two reconcile
passes produce no second change.

- [ ] **Step 2: Verify RED**

Run the focused tests and confirm current dispatch omits Python tests and Codex
dry-run reports formatting drift.

- [ ] **Step 3: Implement minimal dispatch and normalization**

Run unittest, Python compile, Bash syntax, then the Bash suite. Normalize only
managed Codex section whitespace before comparison.

- [ ] **Step 4: Verify GREEN**

Run the focused tests followed by `agent-sync test`.

- [ ] **Step 5: Commit**

```bash
git add bin/agent-sync scripts/reconcile-codex-orchestration.py tests/test_reconcile_codex_orchestration.py tests/test_agent_doctor.py
git commit -m "fix: complete evaluation and Codex idempotency"
```

### Task 7: Private Hub policy, live convergence, and release verification

**Files:**
- Modify: `$HOME/.config/agent-hub/policies/tool-scopes.json`
- Modify: `$HOME/.config/agent-hub/TOOLING.zh-CN.md`
- Modify: `$HOME/.config/agent-hub/USAGE.zh-CN.md`
- Modify: `$HOME/.config/agent-hub/GUIDE-总说明.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: live synchronized Agent state and documented ownership policy.

- [ ] **Step 1: Add the approved Hub policy**

Add Cursor Cloudflare conditional Skill precedence, Codex Cloudflare forbidden
plugin, target exclusions, and explicit secret-donor compatibility entries.

- [ ] **Step 2: Run focused policy and privacy checks**

Run Doctor policy tests and both repository privacy audits before live sync.

- [ ] **Step 3: Run live convergence**

Run:

```bash
agent-sync all
agent-sync all
```

Confirm the second run introduces no semantic changes.

- [ ] **Step 4: Run final verification**

Run:

```bash
python3 -m unittest discover -s tests -v
agent-sync test
agent-sync verify --strict
agent-sync doctor --json --runtime --strict
agent-sync audit
```

Confirm Cursor reports the Cloudflare Skill as plugin-owned, Codex reports the
marketplace Cloudflare plugin absent, `cloudflare-local` remains enabled, and
all secret-bearing config files are mode `0600`.

- [ ] **Step 5: Commit the private Hub**

Stage only the Cloudflare and policy/documentation changes, inspect the staged
diff, run the privacy audit again, and commit without pushing.

- [ ] **Step 6: Update public documentation and commit**

Document plugin precedence, exclusions, ownership states, complete test
behavior, and permission checks in `README.md`, then commit.
