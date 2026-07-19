# Agent Sync Source Health and Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe source recommendations, full static MCP health, optional runtime health, configuration tracing, and plugin scope auditing.

**Architecture:** A focused `config_inventory.py` module reads only names,
commands, and timestamps from supported configurations. `agent-doctor.py`,
`source-advisor.py`, and `trace-config.py` consume that sanitized inventory.
The shell entrypoint exposes the new commands without changing existing sync
semantics.

**Tech Stack:** Python 3 standard library, Bash, `unittest`, JSON/TOML text
parsing already used by Agent Sync.

## Global Constraints

- The Hub remains the only shared source of truth.
- Source recommendation is read-only and never auto-promotes.
- Runtime probes use bounded timeouts and never print raw output.
- Plugin policies are audit-only.
- Tool-only MCP entries remain untouched unless retired.
- No new runtime dependency.

---

### Task 1: Sanitized configuration inventory

**Files:**
- Create: `scripts/config_inventory.py`
- Test: `tests/test_config_inventory.py`

**Interfaces:**
- Produces: `collect_inventory(home: Path, hub: Path) -> list[dict[str, Any]]`
- Produces: `rank_sources(records: list[dict[str, Any]]) -> list[dict[str, Any]]`
- Produces: `trace_mcp(name: str, home: Path, hub: Path) -> dict[str, Any]`

- [x] Write failing tests for missing tool-only commands, retired residue,
  healthy-source ranking, and redacted trace output.
- [x] Run `python3 -m unittest tests.test_config_inventory -v` and confirm the
  import fails because `config_inventory.py` does not exist.
- [x] Implement readers for Cursor, Antigravity, Claude, OpenCode, VS Code,
  profiles, and Codex; expose only names, command availability, and mtime.
- [x] Re-run the focused test and confirm all cases pass.

### Task 2: Doctor runtime and scope policy

**Files:**
- Modify: `scripts/agent-doctor.py`
- Modify: `tests/test_agent_doctor.py`

**Interfaces:**
- Consumes: `collect_inventory`
- Produces: `runtime_mcp_findings() -> tuple[list[dict[str, str]], list[str]]`
- Produces: `plugin_scope_findings() -> list[dict[str, str]]`

- [x] Write failing tests proving doctor reports a broken tool-only MCP,
  retired residue, failed runtime MCP, and required/forbidden plugin drift.
- [x] Run the focused tests and confirm they fail on missing behavior.
- [x] Add `--runtime`, `--strict`, and bounded sanitized runtime parsers.
- [x] Re-run the focused tests and confirm they pass without secret strings.

### Task 3: Source advisor and trace commands

**Files:**
- Create: `scripts/source-advisor.py`
- Create: `scripts/trace-config.py`
- Modify: `bin/agent-sync`
- Test: `tests/test_config_inventory.py`

**Interfaces:**
- Command: `agent-sync sources [--json]`
- Command: `agent-sync trace mcp NAME [--json]`
- Command: `agent-sync verify --strict`

- [x] Write failing entrypoint tests for all three command surfaces.
- [x] Run the focused tests and confirm unknown-command failures.
- [x] Implement concise terminal and JSON renderers using sanitized inventory.
- [x] Re-run focused tests and verify exit status and output contracts.

### Task 4: Documentation and release metadata

**Files:**
- Modify: `README.md`
- Modify: `examples/manifest.yaml`
- Add: `examples/policies/tool-scopes.json`
- Modify: `VERSION`

- [x] Document explicit source promotion, read-only recommendation, runtime
  doctor, strict verify, trace, and audit-only plugin policy.
- [x] Add a generic policy example with no personal names or paths.
- [x] Bump the release from `1.3.0` to `1.4.0`.

### Task 5: Verification and manual QA

**Files:**
- No production file changes.

- [x] Run `python3 -m unittest discover -s tests -v`.
- [x] Run `bash scripts/test-suite.sh`.
- [x] Run `agent-sync audit`.
- [x] Run `agent-sync sources`, `agent-sync trace mcp notebooklm`,
  `agent-sync doctor --runtime`, and `agent-sync verify --strict`.
- [x] Confirm normal `agent-sync sync --from TOOL --dry-run` behavior remains
  unchanged.
