# Tool-Only MCP Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secret-free policy auditing for intentional tool-only MCP servers and document the shared-versus-native ownership model.

**Architecture:** Reuse `config_inventory.collect_inventory()` as the only MCP metadata source. Extend the private Hub's existing `tool-scopes.json` schema with optional per-tool `allowed_tool_only` and `required_tool_only` lists, then have Agent Doctor report drift without mutating native configurations.

**Tech Stack:** Python 3 standard library, `unittest`, Bash 3.2-compatible CLI wrapper, Markdown documentation.

## Global Constraints

- The public Agent Sync repository must contain no personal paths, credentials, account data, or private Hub configuration.
- Tool-only MCP servers remain owned by their native Agent and are never removed by `agent-sync fix`.
- Shared MCP merge, retired-server cleanup, source ranking, and source promotion behavior must not change.
- Policy matching is case-insensitive and findings may contain only Agent/MCP names.

---

### Task 1: Add MCP scope-policy regression tests

**Files:**
- Modify: `tests/test_agent_doctor.py`

**Interfaces:**
- Consumes: `agent-doctor.py` JSON output through the existing `run_doctor()` helper.
- Produces: executable examples of the `mcp.<tool>.allowed_tool_only` and `required_tool_only` schema.

- [ ] **Step 1: Write a failing test for allowed and unexpected tool-only MCP names**

Create Cursor and Codex fixture configs with shared, allowed, and unexpected
MCP names. Assert that the allowed name is absent from findings and the
unexpected name is reported.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest tests.test_agent_doctor.AgentDoctorTests.test_doctor_audits_tool_only_mcp_scope -v
```

Expected: FAIL because Agent Doctor does not yet read the MCP policy section.

- [ ] **Step 3: Write failing tests for missing required names, invalid policy, and secret-free output**

Assert that a missing required name and a required name outside the allowlist
produce attention findings, malformed list values are rejected, and local MCP
configuration values never appear in JSON output.

- [ ] **Step 4: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_agent_doctor.AgentDoctorTests.test_doctor_audits_tool_only_mcp_scope tests.test_agent_doctor.AgentDoctorTests.test_doctor_reports_invalid_tool_only_mcp_policy -v
```

Expected: FAIL for the missing implementation, not for fixture setup errors.

### Task 2: Implement the minimal scope-policy audit

**Files:**
- Modify: `scripts/agent-doctor.py`
- Test: `tests/test_agent_doctor.py`

**Interfaces:**
- Consumes: `collect_inventory(HOME, HUB)`, `read_shared_mcp()`, and optional `policies/tool-scopes.json`.
- Produces: `mcp_scope_findings() -> list[dict[str, str]]`.

- [ ] **Step 1: Add schema validation and normalization helpers**

Read `mcp` as an object keyed by supported top-level tool identifiers.
Normalize configured and policy MCP names with `casefold()`. Return one safe
finding when an entry or list has an invalid type.

- [ ] **Step 2: Add allowed and required comparisons**

For each configured policy tool, find the corresponding top-level inventory
record, subtract shared names, and report unexpected or missing-required names.
Report a policy finding when required names are not in the allowlist.

- [ ] **Step 3: Add findings to `build_report()`**

Call `mcp_scope_findings()` after existing configured-MCP and plugin-scope
checks. Do not add mutation behavior to `fix`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_agent_doctor.AgentDoctorTests.test_doctor_audits_tool_only_mcp_scope tests.test_agent_doctor.AgentDoctorTests.test_doctor_reports_invalid_tool_only_mcp_policy -v
```

Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

### Task 3: Document the public ownership model

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Create: `docs/superpowers/specs/2026-07-19-tool-only-mcp-governance-design.md`
- Create: `docs/superpowers/plans/2026-07-19-tool-only-mcp-governance.md`

**Interfaces:**
- Consumes: the policy schema implemented in Task 2.
- Produces: public, generic guidance for private Hub maintainers.

- [ ] **Step 1: Extend the optional tool-scope policy documentation**

Add the `mcp` schema example, state that the audit is non-mutating, and explain
shared Skill, shared MCP, tool-only MCP, and native plugin ownership.

- [ ] **Step 2: Add a changelog entry**

Record the new tool-only MCP scope audit and its privacy guarantees.

- [ ] **Step 3: Run privacy and placeholder scans**

Run:

```bash
bash scripts/privacy-audit.sh
rg -n 'TODO|TBD|/Users/|OneDrive|Bearer ' README.md CHANGELOG.md docs/superpowers
```

Expected: privacy audit passes; the scan contains no personal paths or raw
credential examples.

### Task 4: Configure and distribute the private Hub

**Files:**
- Modify in private Hub: `manifest.yaml`
- Modify in private Hub: `policies/tool-scopes.json`
- Create in private Hub: `skills/cli-anything-zotero/SKILL.md`
- Create in private Hub: `rules/agent-tool-routing.md`
- Create in private Hub: `TOOLING.zh-CN.md`
- Modify in private Hub: `README.md`
- Modify in private Hub: `USAGE.zh-CN.md`

**Interfaces:**
- Consumes: the public policy schema from Task 2.
- Produces: one shared Zotero CLI Skill, explicit native MCP ownership, and a Chinese operational runbook.

- [ ] **Step 1: Add the tested Zotero CLI Skill to the Hub whitelist**

Copy the existing local Skill content into the private Hub and add its exact
directory name to `manifest.yaml`.

- [ ] **Step 2: Declare every current top-level tool-only MCP**

Add per-tool allowlists and require only integrations that must remain present.
Keep the host application's loopback Zotero MCP out of
`mcp/shared-servers.json`.

- [ ] **Step 3: Add the routing rule**

State that host-embedded Zotero work uses the host-scoped MCP, external Agent
work uses the CLI Skill, and a single operation never calls both.

- [ ] **Step 4: Add the operational guide**

Document local process versus cloud backend, canonical commands, source
promotion, verification, Git backup, Outlook local-first cutover, and native
plugin boundaries.

### Task 5: Validate local integrations and retire redundant cloud connectors

**Files:**
- No repository file changes.

**Interfaces:**
- Consumes: local Microsoft 365 MCP, local Zotero CLI Bridge, host-scoped Zotero MCP.
- Produces: evidence that local-first routes are usable before removing redundant cloud connectors.

- [ ] **Step 1: Verify Microsoft 365 read surfaces**

Use the local MCP to resolve the signed-in profile, list a small mail sample,
and list a small calendar sample without exposing message bodies in logs.

- [ ] **Step 2: Verify Microsoft 365 draft support**

Create a harmless draft through the local MCP and confirm its ID. If a safe
delete-draft operation is available, remove the test draft; otherwise leave it
clearly titled as a test draft and report it.

- [ ] **Step 3: Re-run Zotero CLI and MCP smoke tests**

Run `zotero-cli app plugin-status`, a bounded library query, and a read-only
MCP initialize/tools-list handshake.

- [ ] **Step 4: Remove redundant cloud Outlook connectors**

Only after Steps 1–2 pass, uninstall the exact Codex Outlook Email and Outlook
Calendar plugins with the native plugin manager.

### Task 6: Install, synchronize, verify, and publish

**Files:**
- Public and private repository files from Tasks 1–4 only.

**Interfaces:**
- Consumes: completed public code and private policy changes.
- Produces: installed Agent Sync, synchronized local tools, and updated public/private remotes.

- [ ] **Step 1: Run complete public verification**

Run:

```bash
python3 -m unittest discover -s tests -v
bash scripts/test-suite.sh
bash scripts/privacy-audit.sh
```

Expected: every suite passes with zero failures.

- [ ] **Step 2: Commit and fast-forward public `main`**

Commit only public repository changes on the feature branch, fast-forward
`main`, and push `origin/main`.

- [ ] **Step 3: Run the installed synchronizer**

Run:

```bash
agent-sync all
agent-sync verify --strict
agent-sync doctor --runtime --strict
```

Expected: all shared Skills/MCP/Rules are covered and Doctor reports no
findings.

- [ ] **Step 4: Commit only the private Hub files owned by this task**

Run the Hub privacy audit, stage exact paths, verify the staged diff, commit,
and push `origin/main`. Leave unrelated pre-existing working-tree changes
unstaged.

- [ ] **Step 5: Verify both remotes and clean public worktree**

Confirm public `main == origin/main`, private `main == origin/main`, the public
worktree is clean, and unrelated private changes remain present but unstaged.

