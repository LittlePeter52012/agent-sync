# MCP Dependency Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, secret-free discovery and exact pinning for npm/PyPI-launched shared MCP dependencies, with safe cached checks before synchronization.

**Architecture:** A focused Python module reads only canonical Hub MCP launch arguments, recognizes exact, `latest`, and unversioned `npx`/`uvx` requirements, queries primary registries through injectable standard-library functions, and performs surgical version-only edits. The shell entrypoint exposes check/pin/smoke operations; `all` checks through a cache but never silently applies an update or package rename.

**Tech Stack:** Python 3 standard library (`argparse`, `dataclasses`, `json`, `urllib`, `subprocess`), Bash 3.2-compatible shell, `unittest`, MCP JSON-RPC over stdio.

## Global Constraints

- Exact Hub pins are authoritative after migration; `deps check` must still
  diagnose the current `latest` and unversioned entries, and target copies are
  never edited by `deps pin`.
- Registry failures return `unknown` and never make an existing exact pin unhealthy.
- No environment values, headers, OAuth data, vault paths, tokens, or account identifiers may appear in output or cache.
- `deps pin` may change only one recognized package argument and preserves the rest of the Hub file byte-for-byte.
- Prerelease, yanked, missing, or non-exact versions cannot be applied.
- `agent-sync all` reports cached candidates but does not apply them.
- Every new production function follows a witnessed RED → GREEN test cycle.
- After every task: inspect the diff, run focused tests, and obtain one bounded read-only review.

## Execution Order

Execute this plan in the same public worktree immediately after
`2026-08-01-target-identity-and-verification-plan.md`. All commands use the
worktree's `./bin/agent-sync`; do not merge the first plan separately and do not
create a second public branch that also edits `bin/agent-sync` or `README.md`.
Merge the combined public branch once after Task 6 passes.

---

## File Map

- `scripts/mcp-deps.py`: parse pins, registry checks, surgical pinning, cache, MCP smoke probe.
- `tests/test_mcp_deps.py`: parser, registry, safety, cache, smoke, and entrypoint tests.
- `bin/agent-sync`: `deps` command and non-mutating pre-sync check.
- `README.md`: command and Agent-reviewed workflow.
- `CHANGELOG.md`, `VERSION`: public release metadata after all public code passes.

### Task 1: Parse npm and PyPI MCP dependency specifications

**Files:**
- Create: `tests/test_mcp_deps.py`
- Create: `scripts/mcp-deps.py`

**Interfaces:**
- `DependencySpec(server, ecosystem, package, version, pin_state, argument_index)` dataclass.
- `discover_dependencies(hub: Path) -> list[DependencySpec]`.
- Pin states: `exact`, `latest`, `unversioned`.
- `DependencyError(ValueError)` for malformed recognized launch specifications.

- [ ] **Step 1: Write parser tests**

Create an isolated Hub fixture containing:

```json
{
  "mcpServers": {
    "mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]},
    "obsidian": {"command": "npx", "args": ["@mauricio.wolff/mcp-obsidian@0.8.2", "${OBSIDIAN_VAULT}"]},
    "local": {"command": "/bin/local", "args": []}
  }
}
```

Assert:

```python
specs = discover_dependencies(self.hub)
self.assertEqual(
    [
        (item.server, item.ecosystem, item.package, item.version, item.pin_state)
        for item in specs
    ],
    [
        ("mineru", "pypi", "mineru-open-mcp", "1.0.21", "exact"),
        ("obsidian", "npm", "@mauricio.wolff/mcp-obsidian", "0.8.2", "exact"),
    ],
)
```

Add separate tests proving `uvx mineru-open-mcp` returns
`version=None, pin_state="unversioned"` and
`npx @mauricio.wolff/mcp-obsidian@latest` returns
`version=None, pin_state="latest"`, while malformed arguments raise
`DependencyError` and local executables are ignored.

- [ ] **Step 2: Run parser tests and witness RED**

Run:

```bash
python3 -m unittest tests.test_mcp_deps.McpDependencyTests.test_discovers_exact_npm_and_pypi_pins -v
```

Expected: import/file failure because `mcp-deps.py` does not exist.

- [ ] **Step 3: Implement the minimal parser**

Use these exact types:

```python
@dataclass(frozen=True)
class DependencySpec:
    server: str
    ecosystem: str
    package: str
    version: str | None
    pin_state: str
    argument_index: int


class DependencyError(ValueError):
    pass
```

Recognize only:

```text
command basename npx + args[0] = package@numeric-version
command basename uvx + args[0] = package==numeric-version
```

For scoped npm packages, split on the final `@`; classify `latest` rather than
rejecting it. For uvx, classify a bare package as unversioned. Reject empty
package names, flags in the package position, malformed exact versions, and
any exact version containing `-`.

- [ ] **Step 4: Run the parser module tests GREEN**

Run:

```bash
python3 -m unittest tests.test_mcp_deps -v
```

Expected: parser tests PASS.

- [ ] **Step 5: Review and commit Task 1**

```bash
git diff --check
git add scripts/mcp-deps.py tests/test_mcp_deps.py
git commit -m "feat: parse pinned MCP dependencies"
```

### Task 2: Query primary registries with safe status classification

**Files:**
- Modify: `tests/test_mcp_deps.py`
- Modify: `scripts/mcp-deps.py`

**Interfaces:**
- `check_dependencies(specs, fetch_json) -> list[dict[str, object]]`.
- Status values: `current`, `ahead`, `update-available`, `unpinned`, `deprecated`, `unknown`.

- [ ] **Step 1: Write fake-registry tests**

Use a `fetch_json(url)` fake returning:

```python
PYPI = {
    "info": {"version": "1.0.22", "project_urls": {"Source": "https://github.com/example/mineru"}},
    "releases": {"1.0.22": [{"yanked": False}]},
}
NPM = {
    "dist-tags": {"latest": "0.8.2"},
    "repository": {"url": "https://github.com/example/obsidian.git"},
    "versions": {
        "0.8.2": {"deprecated": "Renamed to @bitbonsai/mcpvault"}
    },
}
```

Assert MinerU is `update-available`, Obsidian is `deprecated`, URLs contain
only registry package endpoints, and returned records contain exactly:

```python
{
    "server", "ecosystem", "package", "current", "latest",
    "pin_state", "status", "deprecated", "metadata_url"
}
```

Add tests for `URLError`, missing latest, prerelease latest, and a release where
all PyPI files are yanked; each must return `unknown` without raising. Add
current-Hub fixtures proving latest/unversioned entries return `unpinned` (or
`deprecated` when the selected npm version record is deprecated) instead of
aborting. Add numeric comparison fixtures for `1.0`, `1.0.0`, and a local pin
newer than registry latest; the last returns `ahead`, never update-available.

- [ ] **Step 2: Run the new tests and witness RED**

Expected: FAIL because `check_dependencies` is absent.

- [ ] **Step 3: Implement deterministic registry adapters**

Use primary endpoints:

```text
https://pypi.org/pypi/<percent-encoded-package>/json
https://registry.npmjs.org/<percent-encoded-package>
```

Select `info.version` for PyPI and `dist-tags.latest` for npm. Accept only a
stable numeric dotted version that exists in the registry payload and is not
yanked. For npm, inspect `versions[selected_version].deprecated` and return only
the boolean `deprecated`; do not cache or print the registry's arbitrary free
text. Treat all registry text as untrusted, strip control characters from
package/version fields, and never execute or automatically apply a replacement
name. Normalize repository links only after confirming they use `https`. Never
include raw response text in exceptions or output.

The default fetcher uses an explicit 8-second timeout, rejects responses larger
than 2 MiB before JSON parsing, and closes the response. Tests cover timeout,
oversize, malformed JSON, and HTTP error paths.

- [ ] **Step 4: Run registry tests GREEN**

```bash
python3 -m unittest tests.test_mcp_deps -v
```

- [ ] **Step 5: Review and commit Task 2**

```bash
git diff --check
git add scripts/mcp-deps.py tests/test_mcp_deps.py
git commit -m "feat: check MCP package registries"
```

### Task 3: Add surgical, registry-validated pin updates

**Files:**
- Modify: `tests/test_mcp_deps.py`
- Modify: `scripts/mcp-deps.py`

**Interfaces:**
- `pin_dependency(hub, server, version, *, apply, fetch_json) -> str`.
- Return value is the proposed full file text for dry-run or the written text for apply.

- [ ] **Step 1: Write mutation-safety tests**

Read the fixture bytes before pinning and assert:

```python
proposed = pin_dependency(
    self.hub,
    "mineru",
    "1.0.22",
    apply=False,
    fetch_json=self.fetch_json,
)
self.assertIn('"mineru-open-mcp==1.0.22"', proposed)
self.assertEqual(self.shared_path.read_bytes(), before)
```

For `apply=True`, assert the only textual change is
`mineru-open-mcp==1.0.21` → `mineru-open-mcp==1.0.22`. Add conversion tests for
bare `mineru-open-mcp` and `@mauricio.wolff/mcp-obsidian@latest` to exact
versions. Add refusal tests for an unknown server, package rename syntax,
target version `latest`, `1.1.0rc1`, `1.1.0-beta.1`, a missing registry
version, and a yanked PyPI version.

- [ ] **Step 2: Run the tests and witness RED**

Expected: FAIL because `pin_dependency` is absent.

- [ ] **Step 3: Implement a one-argument surgical replacement**

Parse JSON to identify the exact old argument, whether exact, latest, or
unversioned, then replace exactly one `json.dumps(old_argument)` token in the original text with
`json.dumps(new_argument)`. Refuse zero or multiple textual occurrences. Write
through a sibling temporary file followed by `os.replace` only for
`apply=True`.

- [ ] **Step 4: Run pinning tests GREEN and inspect a real dry-run**

```bash
python3 -m unittest tests.test_mcp_deps -v
```

Expected: tests PASS. The real CLI dry-run is intentionally deferred until
Task 5, after its argparse and clean-worktree gates exist.

- [ ] **Step 5: Review and commit Task 3**

```bash
git diff --check
git add scripts/mcp-deps.py tests/test_mcp_deps.py
git commit -m "feat: pin MCP dependencies safely"
```

### Task 4: Add bounded MCP initialization and tool-list smoke probing

**Files:**
- Modify: `tests/test_mcp_deps.py`
- Modify: `scripts/mcp-deps.py`

**Interfaces:**
- `smoke_dependency(hub, server, *, target=None, timeout=30, runner=Popen) -> dict[str, object]`.
- Result fields: `server`, `initialized`, `protocol_version`, `tool_count`, `tools`, `status`.

- [ ] **Step 1: Write protocol tests with a fake stdio process**

The fake process must record two newline-delimited JSON-RPC requests and return
responses for `initialize` and `tools/list`. Assert the first methods are:

```python
["initialize", "notifications/initialized", "tools/list"]
```

and the result is:

```python
{
    "server": "mineru",
    "initialized": True,
    "protocol_version": "2025-06-18",
    "tool_count": 2,
    "tools": ["extract", "status"],
    "status": "healthy",
}
```

Add tests for timeout, malformed JSON, wrong response ID, a JSON-RPC `error`,
protocol mismatch, non-list `tools`, server notifications interleaved before a
response, nonzero exit, and unrecognized non-stdio launchers. Add command,
args, and env placeholder fixtures; any unresolved placeholder must fail before
`Popen` is called. Output assertions must not contain the Hub environment
dictionary or resolved values.

- [ ] **Step 2: Run smoke tests and witness RED**

Expected: FAIL because `smoke_dependency` is absent.

- [ ] **Step 3: Implement the minimal MCP stdio handshake**

Launch only the canonical command and args for the named server. Resolve full
`${NAME}` placeholders from the current process environment. When `target` is
provided and its Hub policy permits secret donors, reuse the existing
`collect_donors`, `donor_lookup_map`, and placeholder resolution helpers from
`merge-mcp.py` without logging their values. If any command, arg, or env
placeholder remains unresolved, do not start the child.

Send MCP protocol version `2025-06-18`, client name `agent-sync`, and client
version read from `VERSION`. Match responses by request ID, ignore valid server
notifications, reject JSON-RPC error objects, require the negotiated version to
be supported, and require `tools` to be a list of objects with nonempty string
names. Return only a sorted unique tool-name list and normalized fields.

After `tools/list`, close stdin and wait for bounded graceful exit; only then
terminate and finally kill if necessary. Never call a tool during smoke.

- [ ] **Step 4: Run smoke tests GREEN**

```bash
python3 -m unittest tests.test_mcp_deps -v
```

- [ ] **Step 5: Review and commit Task 4**

```bash
git diff --check
git add scripts/mcp-deps.py tests/test_mcp_deps.py
git commit -m "feat: smoke test pinned MCP launchers"
```

### Task 5: Expose `deps` and add a cached non-mutating pre-sync check

**Files:**
- Modify: `tests/test_mcp_deps.py`
- Modify: `bin/agent-sync`
- Modify: `scripts/mcp-deps.py`
- Modify: `README.md`

**Interfaces:**
- `agent-sync deps check [--json] [--refresh]`.
- `agent-sync deps pin SERVER VERSION [--dry-run|--apply]`.
- `agent-sync deps smoke SERVER [--target TARGET] [--timeout SECONDS]`.
- Cache: `${XDG_CACHE_HOME:-$HOME/.cache}/agent-sync/mcp-deps.json`, maximum age 86400 seconds.

- [ ] **Step 1: Write entrypoint and cache tests**

Call `main(argv, fetch_json=fake_fetch)` directly for registry/cache behavior so
the injection crosses no process boundary. Assert:

```python
stdout = io.StringIO()
with redirect_stdout(stdout):
    rc = module.main(["check", "--json"], fetch_json=self.fetch_json)
self.assertEqual(rc, 0)
self.assertIn("dependencies", json.loads(stdout.getvalue()))

bad = self.run_entrypoint("deps", "pin", "mineru", "latest", "--apply")
self.assertNotEqual(bad.returncode, 0)

all_run = self.run_entrypoint("all", cache_payload=self.safe_cached_update)
self.assertIn("MCP dependency update available", all_run.stderr)
self.assertEqual(self.shared_path.read_bytes(), before)
```

Test cache reuse through `main` and count fake fetch calls. Test shell dispatch
separately with a pre-populated safe cache so no subprocess test needs a hidden
registry injection mechanism. The test helper's `cache_payload` argument writes
that payload to
`${HOME}/.cache/agent-sync/mcp-deps.json` before invoking the subprocess and
does not alter production environment variables.

- [ ] **Step 2: Run tests and witness RED**

Expected: entrypoint reports unknown command `deps`.

- [ ] **Step 3: Add argparse subcommands and cache validation**

Expose `main(argv: list[str] | None = None, *, fetch_json=default_fetch_json)`
so unit tests inject registry results in process. The cache stores only the
safe records returned by `check_dependencies` plus
`checked_at`. Reject a cache that is malformed, has unknown fields, is older
than 86400 seconds, or contains any key matching
`token|secret|password|header|oauth|vault|path` case-insensitively.
Write cache updates through a sibling temporary file and `os.replace`.

For `deps pin --apply`, require `git -C "$HUB" status --porcelain` to be empty
before mutation. Dry-run remains available in a dirty worktree. Add a dirty-Hub
refusal test and assert CLI output contains only
`SERVER: OLD_REQUIREMENT -> NEW_REQUIREMENT`, never the full Hub JSON.

- [ ] **Step 4: Add shell dispatch and pre-sync advisory**

Update usage and route `deps` to `mcp-deps.py`. Keep `run_full_sync` unchanged.
In the `all)` branch only, before `run_full_sync`, call:

```bash
python3 "$SCRIPTS/mcp-deps.py" check --hub "$HUB" --cached --quiet || true
```

The quiet form prints only one line per available package/version pair to
stderr. It does not prevent an offline sync and never calls `pin`.

Add a regression proving ordinary `sync` never invokes dependency discovery;
only `all` owns the advisory.

- [ ] **Step 5: Run focused tests and shell syntax**

```bash
python3 -m unittest tests.test_mcp_deps -v
bash -n bin/agent-sync
```

- [ ] **Step 6: Document the Agent-reviewed workflow**

Document check → primary-release review → dry-run pin → smoke → test → sync.
State explicitly that `all` only discovers candidates and that an active Agent,
not a hidden secondary model call, makes the compatibility decision.

- [ ] **Step 7: Review and commit Task 5**

```bash
git diff --check
git add bin/agent-sync scripts/mcp-deps.py tests/test_mcp_deps.py README.md
git commit -m "feat: add guarded MCP dependency maintenance"
```

### Task 6: Public release metadata and phase verification

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

- [ ] **Step 1: Add release notes and bump one minor version**

Record dependency check/pin/smoke, Copilot/VS Code target separation, plugin
transport identity, and managed verification. Set `VERSION` from `1.10.1` to
`1.11.0` because public commands and JSON fields are added without removing
existing commands.

- [ ] **Step 2: Run complete verification before committing**

```bash
./bin/agent-sync test
./bin/agent-sync audit
python3 -m unittest tests.test_mcp_deps -v
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Independent review gate**

Give one reviewer the design, both completed public implementation plans,
commit range, registry-output schema, and full test output. Reproduce any
accepted defect with a failing test before changing code.

- [ ] **Step 4: Commit release metadata**

```bash
git add CHANGELOG.md VERSION
git commit -m "chore: release agent-sync 1.11.0"
```

- [ ] **Step 5: Do not push yet**

Record the clean worktree and commit range. The Hub phase must consume and
validate this public tool before the repositories are pushed.
