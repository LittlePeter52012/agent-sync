# Target Identity and Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Copilot/VS Code policy-key and plugin-ownership misjudgments, then distinguish managed synchronization health from full local runtime health.

**Architecture:** Keep the existing resolver and Doctor structure. Tighten automatic plugin MCP ownership to exact normalized transport identity, give Doctor records independent Skill and MCP target keys, and add explicit `scope`/`owner` metadata so a new managed verification gate can filter findings without hiding full-machine problems.

**Tech Stack:** Python 3 standard library, Bash 3.2-compatible shell, `unittest`, existing Agent Sync inventory/resolver modules.

## Global Constraints

- Do not read, merge, copy, delete, or reauthenticate GitHub tokens, Copilot subscriptions, credential helpers, Git remotes, or VS Code account sessions.
- Skill policy uses `copilot`; VS Code user/profile MCP and secret policy use `vscode` or `vscode:<profile-id>`.
- Same-name/different-transport plugin MCPs are collisions, not automatic replacements.
- `agent-sync verify --strict` retains current all-surface behavior.
- `agent-sync verify --strict --scope managed` filters the exit gate, not the report.
- Every production behavior change follows a witnessed RED → GREEN test cycle.
- After every task: inspect the diff, run its focused tests, and obtain one bounded read-only review before moving on.

## Execution Order

After the design and all three plans are committed on public `main`, create one
public Agent Sync worktree. Execute this plan first and
`2026-08-01-mcp-dependency-maintenance-plan.md` second in that same worktree so
their shared `bin/agent-sync` and `README.md` changes remain linear. During
both plans call `./bin/agent-sync` from that worktree, not the installed
`~/.local/bin` symlink. Merge the completed public branch once; only then start
the separate Hub worktree plan.

---

## File Map

- `scripts/capability_resolver.py`: automatic plugin ownership decision.
- `scripts/agent-doctor.py`: target records, finding metadata, scoped strict exit.
- `scripts/verify-all.sh`: structural verification plus scoped Doctor dispatch.
- `bin/agent-sync`: public `verify` option parsing and `all` managed gate.
- `tests/test_capability_resolver.py`: plugin identity regressions.
- `tests/test_agent_doctor.py`: Copilot/VS Code separation and finding scopes.
- `tests/test_promote_mcp.py`: end-to-end same-name collision coverage.
- `tests/test_verify_scopes.py`: CLI exit-code behavior for managed versus local findings.
- `README.md`: user-facing verification semantics.

### Task 1: Require transport identity for automatic plugin MCP ownership

**Files:**
- Modify: `tests/test_capability_resolver.py`
- Modify: `tests/test_config_inventory.py`
- Modify: `scripts/capability_resolver.py`

**Interfaces:**
- Consumes: `mcp_identity_matches(actual: dict, canonical: dict) -> bool`.
- Produces: `resolve_target_policy(...).suppressed_mcp` containing only transport-identical or explicitly preferred MCPs.

- [ ] **Step 1: Write the same-name/different-transport failing unit test**

Add to `CapabilityResolverTests`:

```python
def test_same_name_different_transport_is_not_automatic_plugin_ownership(self):
    plugin = self.home / ".cursor/plugins/local/conflict"
    plugin.mkdir(parents=True)
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared-mcp": {
                        "type": "http",
                        "url": "https://different.example/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    resolution = resolve_target_policy("cursor", self.home, self.hub)

    self.assertNotIn("shared-mcp", resolution.suppressed_mcp)
    self.assertNotIn("shared-mcp", resolution.mcp_owners)
```

- [ ] **Step 2: Run the focused test and witness RED**

Run:

```bash
python3 -m unittest tests.test_capability_resolver.CapabilityResolverTests.test_same_name_different_transport_is_not_automatic_plugin_ownership -v
```

Expected: FAIL because the current `name in plugin_names or transport_match`
branch suppresses `shared-mcp`.

- [ ] **Step 3: Add the failing coverage-level regression before implementation**

In `tests/test_config_inventory.py`, import and call the existing loaded module:

```python
def test_same_name_different_transport_plugin_does_not_cover_shared_mcp(self):
    plugin = self.home / ".cursor/plugins/local/conflict"
    plugin.mkdir(parents=True)
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"shared": {"command": "/bin/echo"}}}
        ),
        encoding="utf-8",
    )

    module = load_inventory_module()
    coverage = module.resolve_mcp_coverage("cursor", self.home, self.hub)

    self.assertEqual(coverage["shared"]["state"], "missing")
    self.assertEqual(coverage["shared"]["owner"], "hub-managed")
```

Run it alone. Expected: FAIL with `plugin-owned` under the current resolver.

- [ ] **Step 4: Implement transport-only automatic ownership**

In `resolve_target_policy`, remove the plugin-name shortcut and retain exact
identity matching:

```python
for name in shared_identities:
    canonical = shared_configs.get(name, {})
    transport_match = any(
        isinstance(config, dict)
        and isinstance(canonical, dict)
        and mcp_identity_matches(config, canonical)
        for config in source.mcp_servers.values()
    )
    if transport_match:
        suppressed_mcp.add(name)
        mcp_owners[name] = owner
```

Do not change explicit `prefer_marketplace` handling.

- [ ] **Step 5: Run both RED tests GREEN and the resolver/inventory suites**

Run:

```bash
python3 -m unittest tests.test_capability_resolver tests.test_config_inventory -v
```

Expected: all resolver tests PASS, including the existing alias-with-identical-
transport test.

- [ ] **Step 6: Review and commit Task 1**

Run:

```bash
git diff --check
python3 -m unittest tests.test_capability_resolver tests.test_config_inventory -v
git add scripts/capability_resolver.py tests/test_capability_resolver.py tests/test_config_inventory.py
git commit -m "fix: require plugin MCP transport identity"
```

### Task 2: Split Copilot Skill policy from VS Code MCP policy

**Files:**
- Modify: `tests/test_agent_doctor.py`
- Modify: `scripts/agent-doctor.py`

**Interfaces:**
- Consumes: `effective_skill_counts(target, path, skills)` and `resolve_mcp_coverage(target, home, hub)`.
- Produces: each target tuple with separate `skill_target_key` and `mcp_target_key` positions.

- [ ] **Step 1: Write a failing policy-separation test**

Add to `AgentDoctorTests`:

```python
def test_copilot_skill_policy_is_independent_from_vscode_mcp_policy(self):
    (self.hub / "policies").mkdir(exist_ok=True)
    (self.hub / "policies" / "tool-scopes.json").write_text(
        json.dumps(
            {
                "distribution": {
                    "copilot": {
                        "exclude_skills": ["example"],
                        "exclude_mcp": [],
                    },
                    "vscode": {
                        "exclude_skills": [],
                        "exclude_mcp": ["shared"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    vscode = (
        self.home
        / "Library/Application Support/Code/User/mcp.json"
    )
    vscode.parent.mkdir(parents=True)
    vscode.write_text(json.dumps({"servers": {}}), encoding="utf-8")

    report = json.loads(self.run_doctor().stdout)
    record = next(
        item for item in report["agents"]
        if item["name"] == "Copilot / VS Code"
    )

    self.assertEqual(record["skills"], {"configured": 1, "expected": 1})
    self.assertEqual(record["managed_skills"], {"configured": 0, "expected": 0})
    self.assertEqual(record["mcp"], {"configured": 1, "expected": 1})
```

- [ ] **Step 2: Run the test and witness RED**

Run:

```bash
python3 -m unittest tests.test_agent_doctor.AgentDoctorTests.test_copilot_skill_policy_is_independent_from_vscode_mcp_policy -v
```

Expected: FAIL because the combined record sends `vscode` to
`effective_skill_counts`.

- [ ] **Step 3: Change the target tuple shape minimally**

Change target tuples from:

```python
(name, command, target_key, config, skill_dir, kind, apps)
```

to:

```python
(name, command, skill_target_key, mcp_target_key, config, skill_dir, kind, apps)
```

For ordinary targets the two keys are equal. Use this exact Copilot/VS Code
record:

```python
(
    "Copilot / VS Code",
    "copilot",
    "copilot",
    "vscode",
    HOME / "Library" / "Application Support" / "Code" / "User" / "mcp.json",
    HOME / ".copilot" / "skills",
    "json",
    ("Visual Studio Code.app",),
)
```

Pass `skill_target_key` only to `effective_skill_counts` and
`mcp_target_key` only to `resolve_mcp_coverage`.

- [ ] **Step 4: Run the focused and complete Doctor tests**

Run:

```bash
python3 -m unittest tests.test_agent_doctor.AgentDoctorTests.test_copilot_skill_policy_is_independent_from_vscode_mcp_policy -v
python3 -m unittest tests.test_agent_doctor -v
```

Expected: PASS with no access to any real account file because the test uses an
isolated temporary HOME.

- [ ] **Step 5: Review and commit Task 2**

Run:

```bash
git diff --check
git diff -- scripts/agent-doctor.py tests/test_agent_doctor.py
git add scripts/agent-doctor.py tests/test_agent_doctor.py
git commit -m "fix: separate Copilot and VS Code policy keys"
```

### Task 3: Add explicit finding scope and owner metadata

**Files:**
- Modify: `tests/test_agent_doctor.py`
- Modify: `scripts/agent-doctor.py`

**Interfaces:**
- Produces: `make_finding(message, *, scope, owner, severity="attention") -> dict[str, str]`.
- Produces: `policy_file_findings() -> list[dict[str, str]]`, one Hub-owned policy finding for unreadable or malformed JSON.
- Produces: `classify_runtime_failure(target, name, coverage) -> tuple[str, str]` returning `(scope, owner)` without performing I/O.
- Produces: every JSON finding with `severity`, `message`, `scope`, and `owner`.

- [ ] **Step 1: Write failing metadata tests**

Add one managed and one local finding fixture:

```python
def test_findings_expose_scope_and_owner(self):
    (self.home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "broken-tool-only": {
                        "command": "definitely-missing-tool-only-command"
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    findings = json.loads(self.run_doctor().stdout)["findings"]

    self.assertTrue(findings)
    self.assertTrue(
        all({"severity", "message", "scope", "owner"} <= set(item)
            for item in findings)
    )
    tool_only = next(
        item for item in findings
        if "broken-tool-only" in item["message"]
    )
    self.assertEqual(tool_only["scope"], "local")
    self.assertEqual(tool_only["owner"], "claude")
```

- [ ] **Step 2: Run the test and witness RED**

Run the new test alone. Expected: FAIL because current findings lack `scope`
and `owner`.

- [ ] **Step 3: Write classification regressions and witness RED before implementation**

Add tests proving:

```python
self.assertEqual(malformed_policy["scope"], "policy")
self.assertEqual(malformed_policy["owner"], "hub")
self.assertEqual(
    module.classify_runtime_failure(
        "cursor",
        "shared",
        {"shared": {"state": "plugin-owned", "owner": "plugin:example"}},
    ),
    ("local", "cursor"),
)
self.assertEqual(
    module.classify_runtime_failure(
        "cursor",
        "shared",
        {"shared": {"state": "excluded", "owner": "excluded"}},
    ),
    ("local", "cursor"),
)
self.assertEqual(
    module.classify_runtime_failure(
        "cursor",
        "shared",
        {"shared": {"state": "drifted", "owner": "hub-managed"}},
    ),
    ("managed", "hub"),
)
self.assertEqual(retired_residue["scope"], "managed")
self.assertEqual(retired_residue["owner"], "hub")
```

The malformed-policy and retired-residue fixtures run Doctor in a temporary
HOME. Test the pure classifier directly, so no local HTTP port or
`subprocess.run` mock affects the result. Run each new test now. Expected: FAIL
because metadata, policy parsing, and the classifier are absent.

- [ ] **Step 4: Add one constructor, classifier, and source tagging**

Add:

```python
def make_finding(
    message: str,
    *,
    scope: str,
    owner: str,
    severity: str = "attention",
) -> dict[str, str]:
    return {
        "severity": severity,
        "message": message,
        "scope": scope,
        "owner": owner,
    }
```

Add `policy_file_findings` as the first policy check in `build_report`; it
parses `policies/tool-scopes.json` once for validity without printing file
contents. Existing section-specific policy functions may continue returning
their detailed schema findings only when the file parsed successfully.

Use `scope="managed", owner="hub"` for synchronized rules, Hub placeholders,
shared coverage, and Hub-managed runtime failures. Use `scope="local"` with
the actual product key for tool-only MCPs, product plugins/hooks, local file
modes, unsupported local products, and product-only runtime failures. Use
`scope="policy", owner="hub"` for malformed Hub policy.

Update `runtime_mcp_findings` to map each probe label to its target key and call
`resolve_mcp_coverage(target_key, HOME, HUB)`. A named runtime failure is
passed to `classify_runtime_failure` and is managed only when that target's
coverage record for the name has `owner == "hub-managed"` and state is not
`plugin-owned` or `excluded`.
Same-name plugin-owned/excluded failures stay local. A probe-level timeout
without a server name is `scope="local"` and owned by the probed product.

A configured MCP whose name appears in `retired-servers.json` is a failed Hub
retirement obligation, so tag it `scope="managed", owner="hub"`, even though
the residue physically lives in a product config.

- [ ] **Step 5: Run Doctor tests and inspect JSON privacy**

Run:

```bash
python3 -m unittest tests.test_agent_doctor -v
AGENT_HUB_ROOT=/Users/slavatang/.config/agent-hub python3 scripts/agent-doctor.py --json | python3 -c '
import json, sys
report = json.load(sys.stdin)
assert all({'severity', 'message', 'scope', 'owner'} <= set(item) for item in report['findings'])
print(len(report['findings']))
'
```

Expected: tests PASS; the JSON contains metadata but no credential values.

- [ ] **Step 6: Review and commit Task 3**

Run `git diff --check`, review only Doctor/test changes, then:

```bash
git add scripts/agent-doctor.py tests/test_agent_doctor.py
git commit -m "feat: classify Doctor findings by scope"
```

### Task 4: Add managed verification without weakening full strict health

**Files:**
- Create: `tests/test_verify_scopes.py`
- Modify: `scripts/agent-doctor.py`
- Modify: `scripts/verify-all.sh`
- Modify: `bin/agent-sync`
- Modify: `README.md`

**Interfaces:**
- `agent-doctor.py --strict --scope all|managed`, default `all`.
- `agent-sync verify --strict [--scope managed|all]`.
- `agent-sync all` runs structural verification and one full runtime Doctor report whose strict exit gate is managed-only.

- [ ] **Step 1: Write CLI exit-code tests first**

Create `tests/test_verify_scopes.py` with an isolated HOME/Hub fixture. Define
these two helpers before the tests:

```python
REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "bin" / "agent-sync"
DOCTOR = REPO / "scripts" / "agent-doctor.py"

def run_entrypoint(self, *args):
    return subprocess.run(
        [str(ENTRYPOINT), *args],
        cwd=REPO,
        env=self.env,
        text=True,
        capture_output=True,
    )

def run_doctor(self, *args):
    return subprocess.run(
        ["python3", str(DOCTOR), "--json", *args],
        cwd=REPO,
        env=self.env,
        text=True,
        capture_output=True,
    )
```

Then add these assertions:

```python
def test_managed_scope_ignores_tool_only_failure(self):
    result = self.run_entrypoint("verify", "--strict", "--scope", "managed")
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("broken-tool-only", result.stdout)

def test_all_scope_fails_tool_only_failure(self):
    result = self.run_entrypoint("verify", "--strict")
    self.assertEqual(result.returncode, 1)

def test_managed_scope_fails_shared_coverage_drift(self):
    (self.hub / "mcp" / "shared-servers.json").write_text(
        json.dumps(
            {"mcpServers": {"shared": {"command": "/bin/echo"}}}
        ),
        encoding="utf-8",
    )
    result = self.run_doctor("--strict", "--scope", "managed")
    self.assertEqual(result.returncode, 1)
    self.assertIn("shared", result.stdout)

def test_managed_scope_fails_malformed_hub_policy(self):
    (self.hub / "policies").mkdir(exist_ok=True)
    (self.hub / "policies" / "tool-scopes.json").write_text(
        '{"distribution":',
        encoding="utf-8",
    )
    result = self.run_doctor("--strict", "--scope", "managed")
    self.assertEqual(result.returncode, 1)

def test_managed_scope_fails_retired_mcp_residue(self):
    (self.hub / "mcp" / "retired-servers.json").write_text(
        json.dumps({"retiredServers": ["retired"]}),
        encoding="utf-8",
    )
    (self.home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"retired": {"command": "/bin/sh"}}}),
        encoding="utf-8",
    )
    result = self.run_doctor("--strict", "--scope", "managed")
    self.assertEqual(result.returncode, 1)
```

For the entrypoint tests, use `skills: []`, an empty shared MCP object, and no
rule files so `verify-all.sh` is structurally neutral; place only the
tool-owned failure under temporary HOME. For the managed shared-drift test,
invoke Doctor directly after adding one shared MCP. Use a temporary
`AGENT_SYNC_RUNTIME_TIMEOUT=1`; no real product executable or account config is
needed.

- [ ] **Step 2: Run the new module and witness RED**

Run:

```bash
python3 -m unittest tests.test_verify_scopes -v
```

Expected: option parsing fails because `--scope` is not implemented.

- [ ] **Step 3: Add Doctor scope filtering**

Extend argparse:

```python
parser.add_argument("--scope", choices=("all", "managed"), default="all")
```

Keep the full report unchanged. Calculate the exit gate only:

```python
blocking = report["findings"]
if args.scope == "managed":
    blocking = [
        item for item in blocking
        if item["scope"] in {"managed", "policy"}
        or item["owner"] == "hub"
    ]
return 1 if args.strict and blocking else 0
```

- [ ] **Step 4: Add exact entrypoint parsing**

Accept only these strict forms:

```text
verify --strict
verify --strict --scope all
verify --strict --scope managed
```

For managed verification, run `verify-all.sh`, then:

```bash
python3 "$SCRIPTS/agent-doctor.py" --runtime --strict --scope managed
```

Keep `verify --strict` mapped to `--scope all` for backward compatibility.

- [ ] **Step 5: Make `all` use two explicit health views**

Keep `run_full_sync` structural-only because it is also used by ordinary
`sync` and `sync --from`. In the `all)` branch only, run the existing full sync
and then one full report with a managed exit gate:

```bash
python3 "$SCRIPTS/agent-doctor.py" --runtime --strict --scope managed
```

Because scope filtering affects only the exit code, product-owned and tool-only
findings remain visible in the same output. Do not suppress or discard them.

Add an entrypoint test with a pre-populated dependency cache and a fake product
CLI proving ordinary `sync` does not invoke a registry check or runtime probe,
while `all` does.

- [ ] **Step 6: Run RED/GREEN scope tests and full related tests**

Run:

```bash
python3 -m unittest tests.test_verify_scopes -v
python3 -m unittest tests.test_agent_doctor tests.test_capability_resolver tests.test_config_inventory tests.test_promote_mcp -v
bash -n bin/agent-sync scripts/verify-all.sh
```

Expected: all PASS.

- [ ] **Step 7: Document the two gates**

In `README.md`, document that managed scope proves Hub distribution while full
strict Doctor proves all configured local/plugin/tool-only runtimes. State that
neither mode mutates accounts or product plugins.

- [ ] **Step 8: Review and commit Task 4**

Run:

```bash
git diff --check
git add bin/agent-sync scripts/agent-doctor.py scripts/verify-all.sh tests/test_verify_scopes.py README.md
git commit -m "feat: add managed verification scope"
```

### Task 5: Phase verification gate

**Files:**
- No production changes unless a failing regression test identifies a defect.

- [ ] **Step 1: Run the complete public suite**

```bash
./bin/agent-sync test
```

Expected: Python tests, Python syntax, Bash syntax, and Bash evaluation suite
all PASS.

- [ ] **Step 2: Run privacy and live read-only checks**

```bash
./bin/agent-sync audit
./bin/agent-sync verify --strict --scope managed
./bin/agent-sync doctor --runtime --strict
```

Record managed and full-health outcomes separately. A full Doctor failure does
not erase a passing managed result; it must be diagnosed before final release.

- [ ] **Step 3: Independent review gate**

Give one read-only reviewer the design, this plan, commit range, and test
output. Accept only findings tied to the approved scope. Reproduce every
accepted bug with a failing test before changing production code.

- [ ] **Step 4: Mark the phase complete only with fresh evidence**

Confirm `git status --short`, commit hashes, and the complete test output. Do
not push yet; the dependency and Hub phases still follow.
