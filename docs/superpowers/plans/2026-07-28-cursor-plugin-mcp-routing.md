# Cursor Plugin MCP Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent Sync avoid duplicate Cursor MCP registrations and diagnose Cursor's loopback Streamable HTTP compatibility failures.

**Architecture:** A focused Cursor plugin MCP helper discovers local plugin server names and prunes only shadowed Hub entries after normal merging. Verification and Doctor use the same discovered names as a capability source. Doctor's optional runtime path adds a loopback-only HTTP GET probe that mirrors Cursor's SSE discovery behavior.

**Tech Stack:** Python 3 standard library, Bash, `unittest`, JSON MCP configurations.

## Global Constraints

- Do not modify Cursor plugin files.
- Preserve tool-only MCP entries and local secrets.
- Runtime probes must remain loopback-only, bounded, read-only, and secret-free.
- Follow the existing Agent Sync JSON shapes and test style.

---

### Task 1: Cursor plugin MCP discovery and pruning

**Files:**
- Create: `scripts/cursor_plugin_mcp.py`
- Modify: `scripts/sync-mcp.sh`
- Modify: `tests/test_promote_mcp.py`

**Interfaces:**
- Produces: `plugin_server_names(home: Path) -> set[str]`
- CLI: `cursor_plugin_mcp.py --home PATH list`
- CLI: `cursor_plugin_mcp.py --home PATH prune --canonical PATH --target PATH`

- [x] **Step 1: Write failing sync tests**

Add tests that install a temporary local Cursor plugin declaring `shared`,
execute MCP sync, and assert that `shared` is absent from the user
`mcp.json` while `cursor-native` remains. Add a second test without the plugin
and assert that `shared` is present.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_promote_mcp.PromoteMcpTests.test_sync_uses_cursor_plugin_for_shared_mcp tests.test_promote_mcp.PromoteMcpTests.test_sync_restores_shared_mcp_when_cursor_plugin_is_absent -v
```

Expected: FAIL because plugin discovery/pruning is not implemented.

- [x] **Step 3: Implement the minimal helper and sync hook**

Parse `~/.cursor/plugins/local/*/mcp.json`, collect enabled `mcpServers`
names, and remove case-insensitive intersections with the canonical shared
names from the user target. Invoke the helper only after Cursor's normal merge.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: both tests pass.

### Task 2: Coverage recognizes plugin-provided MCP

**Files:**
- Modify: `scripts/verify-all.sh`
- Modify: `scripts/list-sync.sh`
- Modify: `scripts/agent-doctor.py`
- Modify: `tests/test_agent_doctor.py`
- Modify: `tests/test_promote_mcp.py`

**Interfaces:**
- Consumes: Cursor plugin server names emitted by `cursor-plugin-mcp.py list`.
- Produces: Cursor shared MCP coverage based on the union of user and plugin
  server names.

- [x] **Step 1: Write failing Verify and Doctor tests**

Create temporary Cursor plugin metadata that provides `shared`, omit `shared`
from the user MCP file, and assert Verify succeeds and Doctor reports `1/1`
coverage without a missing-coverage finding.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_promote_mcp.PromoteMcpTests.test_verify_counts_cursor_plugin_shared_mcp tests.test_agent_doctor.AgentDoctorTests.test_doctor_counts_cursor_plugin_shared_mcp -v
```

Expected: FAIL because current coverage reads only `~/.cursor/mcp.json`.

- [x] **Step 3: Implement union coverage**

Import or invoke the helper without reading plugin secrets. Apply the union only
to Cursor; leave every other target unchanged.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: both tests pass.

### Task 3: Cursor loopback HTTP runtime diagnostic

**Files:**
- Modify: `scripts/agent-doctor.py`
- Modify: `tests/test_agent_doctor.py`

**Interfaces:**
- Produces: `cursor_loopback_http_findings() -> tuple[list[dict[str, str]], bool]`

- [x] **Step 1: Write failing boundary tests**

Inject openers at the standard-library network boundary whose `/mcp` GET
results are 404 and 405. Assert 404 produces a sanitized Cursor incompatibility
finding and 405 does not.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_agent_doctor.AgentDoctorTests.test_doctor_runtime_reports_cursor_streamable_http_404 tests.test_agent_doctor.AgentDoctorTests.test_doctor_runtime_accepts_cursor_streamable_http_405 -v
```

Expected: FAIL because Cursor runtime probing is absent.

- [x] **Step 3: Implement the bounded loopback probe**

Use `urllib.parse`, `urllib.request`, and a short timeout. Probe only HTTP URLs
whose hostname is `127.0.0.1`, `localhost`, or `::1`. Never include configured
headers and never expose the URL or response body.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: both tests pass.

### Task 4: Documentation, version, and full verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

**Interfaces:**
- Produces: documented Cursor plugin ownership and runtime behavior.

- [x] **Step 1: Document the behavior**

Explain that local Cursor plugins can satisfy shared MCP coverage, shadowed user
entries are removed automatically, and `doctor --runtime` probes only loopback
Cursor HTTP MCP endpoints.

- [x] **Step 2: Bump the minor version and changelog**

Record the two focused fixes without unrelated release notes.

- [x] **Step 3: Run the complete unit suite**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 4: Run repository evaluation and privacy checks**

Run:

```bash
bash scripts/privacy-audit.sh
bash scripts/verify-all.sh
```

Expected: both exit successfully after the live Hub update and sync.
