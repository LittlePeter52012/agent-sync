# Agent Doctor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one safe terminal command that reports local Agent capabilities and agent-sync health, and one `fix` command that repairs only deterministic sync issues.

**Architecture:** A dependency-free Python script gathers local, name-only observations and renders either a table or JSON. The existing shell entrypoint dispatches `doctor` and `fix`; existing synchronizers remain the source of truth for actual skills, MCP, and rule writes.

**Tech Stack:** Bash 3.2, Python 3 standard library, JSON, TOML text inspection, `unittest`.

## Global Constraints

- Never read or print configuration values that can contain secrets.
- Never access network or account/subscription state.
- `doctor` is read-only; `fix --dry-run` is read-only.
- `fix` must preserve existing local MCP overrides.
- Support macOS default Python without third-party dependencies.

---

### Task 1: Add tested local capability and health reporting

**Files:**
- Create: `scripts/agent-doctor.py`
- Create: `tests/test_agent_doctor.py`
- Modify: `bin/agent-sync`

**Interfaces:**
- Produces: `python3 scripts/agent-doctor.py [--json]`
- Produces: `agent-sync doctor [--json]`

- [ ] **Step 1: Write failing tests for JSON detection and redaction**

Create temporary `HOME` and hub trees. Invoke the script with `--json`; assert the JSON includes records for Codex and Claude, accurate skill/MCP counts, and findings for missing coverage. Put a value such as `TOKEN_SHOULD_NOT_APPEAR` in a config file and assert it is absent from stdout.

- [ ] **Step 2: Run the new tests and verify failure**

Run: `python3 -m unittest tests/test_agent_doctor.py -v`

Expected: FAIL because `scripts/agent-doctor.py` does not exist.

- [ ] **Step 3: Implement the minimum reporter**

Implement a standard-library script with target descriptors for Codex, Claude, Cursor, Gemini/Antigravity, OpenCode, Copilot/VS Code, and Agents. Read only file names, object keys, and model/provider field names. Render status counts, capability labels, actionable findings, and a JSON equivalent. Use `Path.home()` and `AGENT_HUB_ROOT`; never serialize file contents or environment values.

- [ ] **Step 4: Add command dispatch and verify green**

Add `doctor)` to `bin/agent-sync`, dispatching to the script with passed arguments. Run: `python3 -m unittest tests/test_agent_doctor.py -v` and `agent-sync doctor --json`.

- [ ] **Step 5: Commit**

```bash
git add scripts/agent-doctor.py tests/test_agent_doctor.py bin/agent-sync
git commit -m "feat: add agent health doctor"
```

### Task 2: Repair idempotent rule synchronization and legacy reporting

**Files:**
- Modify: `scripts/sync-rules.sh`
- Modify: `scripts/list-sync.sh`
- Modify: `scripts/verify-all.sh`
- Modify: `scripts/test-suite.sh`

**Interfaces:**
- Produces one managed rule block per hub rule after any number of syncs.
- Shows Claude MCP coverage in list and verification output.

- [ ] **Step 1: Write failing regression tests**

Extend `test-suite.sh` with a temporary rule target containing three copies of the current Agent Sync rule. Run `sync-rules.sh` with temporary `HOME` and assert exactly one canonical managed block remains. Add an assertion that status/verify includes Claude MCP coverage.

- [ ] **Step 2: Run the suite and verify failure**

Run: `AGENT_HUB_ROOT="$HOME/.config/agent-hub" agent-sync test`

Expected: FAIL on duplicate-rule cleanup and missing Claude MCP coverage.

- [ ] **Step 3: Implement minimal managed markers and coverage checks**

Wrap injected rules in deterministic HTML begin/end markers based on the rule filename. Before writing, remove every prior marked block and every exact legacy copy of the rule. Update the MCP coverage maps to include Claude through `claude mcp list` only where available, without making this a network or authentication requirement.

- [ ] **Step 4: Run focused then full validation**

Run: `agent-sync test`.

Expected: all existing and new checks pass; a repeated `agent-sync all` leaves exactly one managed rule block.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync-rules.sh scripts/list-sync.sh scripts/verify-all.sh scripts/test-suite.sh
git commit -m "fix: normalize synced rules and Claude MCP status"
```

### Task 3: Add safe fix preview and execution

**Files:**
- Modify: `bin/agent-sync`
- Modify: `tests/test_agent_doctor.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `agent-sync fix --dry-run`
- Produces: `agent-sync fix`

- [ ] **Step 1: Write failing command tests**

Assert `fix --dry-run` reports the three synchronizers it would run and leaves a fixture target byte-for-byte unchanged. Assert unknown `fix` options fail with usage.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests/test_agent_doctor.py -v`

Expected: FAIL because the `fix` command is absent.

- [ ] **Step 3: Implement minimum safe fix command**

Accept only `--dry-run`. Print the exact operations in dry-run mode. Otherwise run `sync-skills.sh --method=symlink`, `sync-mcp.sh`, and `sync-rules.sh`, then invoke `agent-sync doctor`. Do not remove or overwrite existing named MCP configurations.

- [ ] **Step 4: Verify and document**

Run `agent-sync fix --dry-run`, `agent-sync doctor`, `agent-sync test`, and `agent-sync audit`. Add concise README usage for `doctor` and `fix`.

- [ ] **Step 5: Commit**

```bash
git add bin/agent-sync tests/test_agent_doctor.py README.md
git commit -m "feat: add safe agent sync fix command"
```
