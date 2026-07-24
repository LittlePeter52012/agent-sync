# Antigravity Surface Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize Hub Skills and shared MCP servers to Antigravity App, the standalone `agy` CLI, and Antigravity IDE, with separate status and health reporting.

**Architecture:** Extend the repository’s existing explicit target lists instead of introducing a new registry. Skills remain Hub-owned symlinks; MCP files use the existing merge/prune pipeline so product-only servers and local secret values survive. The global Gemini rule file remains the sole rule target.

**Tech Stack:** Bash 3.2-compatible shell scripts, Python 3 standard library, `unittest`, JSON configuration, Git.

## Global Constraints

- Preserve all non-whitelisted Skills and product-only MCP servers.
- Keep `--from antigravity` mapped to the Gemini global source.
- Never print MCP environment values or credentials.
- Do not synchronize conversations, knowledge stores, browser profiles, or plugins.
- Use the existing global `~/.gemini/GEMINI.md`; do not create root-specific rule copies.
- Keep changes surgical and dependency-free.

---

### Task 1: Add failing Antigravity surface synchronization tests

**Files:**
- Create: `tests/test_antigravity_surfaces.py`

**Interfaces:**
- Consumes: `scripts/sync-skills.sh`, `scripts/sync-mcp.sh`, `scripts/list-sync.sh`, and `scripts/verify-all.sh`.
- Produces: regression tests for App, CLI, and IDE Skills/MCP coverage, product-only preservation, cache readiness, list output, and verification failure.

- [ ] **Step 1: Create the isolated test fixture**

Create a `unittest.TestCase` that builds a temporary `HOME` and Hub with one
shared Skill and one shared MCP server. Add a fake `claude` executable so the
existing Claude merge step remains isolated:

```python
class AntigravitySurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        skill = self.hub / "skills" / "shared-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Shared\n", encoding="utf-8")
        (self.hub / "manifest.yaml").write_text(
            "skills:\n  - shared-skill\n",
            encoding="utf-8",
        )
        (self.hub / "mcp").mkdir()
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        (self.hub / "mcp" / "retired-servers.json").write_text(
            json.dumps({"retiredServers": []}),
            encoding="utf-8",
        )
        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_claude = fake_bin / "claude"
        fake_claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_claude.chmod(0o755)
        self.env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
            "CLAUDE_BIN": str(fake_claude),
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        }
```

- [ ] **Step 2: Add tests for the required behavior**

Add focused tests that:

```python
def test_skills_sync_reaches_all_antigravity_roots(self):
    subprocess.run(["bash", str(SYNC_SKILLS)], env=self.env, check=True)
    for relative in (
        ".gemini/config/skills",
        ".gemini/antigravity/skills",
        ".gemini/antigravity-cli/skills",
        ".gemini/antigravity-ide/skills",
    ):
        target = self.home / relative / "shared-skill"
        self.assertTrue(target.is_symlink(), relative)
        self.assertEqual(target.resolve(), self.hub / "skills" / "shared-skill")

def test_mcp_sync_preserves_local_servers_and_prepares_caches(self):
    roots = (
        ".gemini/config",
        ".gemini/antigravity",
        ".gemini/antigravity-cli",
        ".gemini/antigravity-ide",
    )
    for relative in roots:
        root = self.home / relative
        root.mkdir(parents=True)
        (root / "mcp_config.json").write_text(
            json.dumps({"mcpServers": {"local-only": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
    subprocess.run(["bash", str(SYNC_MCP)], env=self.env, check=True)
    for relative in roots:
        root = self.home / relative
        names = json.loads((root / "mcp_config.json").read_text())["mcpServers"]
        self.assertEqual(set(names), {"shared", "local-only"})
    for relative in roots[1:]:
        self.assertTrue((self.home / relative / "mcp" / "shared").is_dir())
        self.assertTrue((self.home / relative / "mcp" / "local-only").is_dir())

def test_list_and_verify_name_each_antigravity_surface(self):
    subprocess.run(["bash", str(SYNC_SKILLS)], env=self.env, check=True)
    subprocess.run(["bash", str(SYNC_MCP)], env=self.env, check=True)
    listed = subprocess.run(
        ["bash", str(LIST_SYNC)],
        env=self.env,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    for label in ("Gemini global", "Antigravity App", "Antigravity CLI", "Antigravity IDE"):
        self.assertIn(label, listed)
    verified = subprocess.run(
        ["bash", str(VERIFY)],
        env=self.env,
        text=True,
        capture_output=True,
    )
    self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
    (self.home / ".gemini/antigravity-cli/mcp_config.json").write_text(
        '{"mcpServers": {}}\n',
        encoding="utf-8",
    )
    failed = subprocess.run(
        ["bash", str(VERIFY)],
        env=self.env,
        text=True,
        capture_output=True,
    )
    self.assertNotEqual(failed.returncode, 0)
    self.assertIn("Antigravity CLI shared MCP 0/1", failed.stdout)
```

- [ ] **Step 3: Run the focused test and confirm RED**

Run:

```bash
python3 -m unittest tests.test_antigravity_surfaces -v
```

Expected: failures show that CLI/IDE Skills and MCP targets, cache directories,
and separate list/verify labels do not exist yet.

- [ ] **Step 4: Commit the RED test**

```bash
git add tests/test_antigravity_surfaces.py
git commit -m "test: define Antigravity surface sync"
```

### Task 2: Synchronize Skills, MCP, caches, and structural reporting

**Files:**
- Modify: `scripts/lib.sh`
- Modify: `scripts/sync-mcp.sh`
- Modify: `scripts/list-sync.sh`
- Modify: `scripts/verify-all.sh`
- Modify: `scripts/test-suite.sh`

**Interfaces:**
- Consumes: `read_skill_list()`, `skill_targets()`, `merge-mcp.py`, and `prune-retired-mcp.py`.
- Produces: four explicit Gemini/Antigravity Skills/MCP targets and `prepare_antigravity_cache(config_path)`.

- [ ] **Step 1: Extend `skill_targets()`**

Keep the existing targets and add:

```bash
$HOME/.gemini/antigravity-cli/skills
$HOME/.gemini/antigravity-ide/skills
```

- [ ] **Step 2: Extend MCP targets and add cache preparation**

Replace the aggregated Antigravity entry in `TARGETS` with:

```bash
"Gemini global|$HOME/.gemini/config/mcp_config.json"
"Antigravity App|$HOME/.gemini/antigravity/mcp_config.json"
"Antigravity CLI|$HOME/.gemini/antigravity-cli/mcp_config.json"
"Antigravity IDE|$HOME/.gemini/antigravity-ide/mcp_config.json"
```

Add a helper that creates cache directories for enabled servers without
deleting existing contents:

```bash
prepare_antigravity_cache() {
    local config="$1"
    local root="${config%/mcp_config.json}"
    while IFS= read -r server; do
        [ -n "$server" ] || continue
        case "$server" in
            */*|*..*) echo "Unsafe MCP server name: $server" >&2; return 1 ;;
        esac
        mkdir -p "$root/mcp/$server"
    done < <(python3 - "$config" <<'PY'
import json, sys
from pathlib import Path

servers = json.loads(Path(sys.argv[1]).read_text()).get("mcpServers", {})
for name, config in sorted(servers.items()):
    if isinstance(config, dict) and config.get("disabled") is not True:
        print(name)
PY
)
}
```

Call it after a successful merge for the App, CLI, and IDE labels only.

- [ ] **Step 3: Split list and verification labels**

Add the four Target Map entries to both `list-sync.sh` dictionaries and the
MCP target list in `verify-all.sh`. Keep the shared-count algorithms unchanged.

- [ ] **Step 4: Extend the full evaluation suite**

Add App, CLI, and IDE checks to the Skills coverage, symlink, and MCP sections
in `test-suite.sh`. Update the fixed MCP PASS/FAIL increment from six to nine.

- [ ] **Step 5: Run the focused test and confirm GREEN**

Run:

```bash
python3 -m unittest tests.test_antigravity_surfaces -v
```

Expected: all tests pass.

- [ ] **Step 6: Run existing synchronization tests**

Run:

```bash
python3 -m unittest tests.test_promote_mcp tests.test_config_inventory -v
```

Expected: all tests pass; `--from antigravity` still reads the Gemini global
configuration.

- [ ] **Step 7: Commit synchronization behavior**

```bash
git add scripts/lib.sh scripts/sync-mcp.sh scripts/list-sync.sh scripts/verify-all.sh scripts/test-suite.sh tests/test_antigravity_surfaces.py
git commit -m "feat: sync all Antigravity surfaces"
```

### Task 3: Split Doctor records and document the new coverage

**Files:**
- Modify: `scripts/agent-doctor.py`
- Modify: `tests/test_agent_doctor.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

**Interfaces:**
- Consumes: `target_records(skills, shared)` and `capability_labels(name, config)`.
- Produces: independent Doctor records named `Gemini global`, `Antigravity App`, `Antigravity CLI`, and `Antigravity IDE`.

- [ ] **Step 1: Add a failing Doctor test**

In the Doctor test fixture, create all four Gemini/Antigravity configurations
with one Skill and one shared MCP. Assert:

```python
names = {agent["name"] for agent in json.loads(self.run_doctor().stdout)["agents"]}
self.assertIn("Gemini global", names)
self.assertIn("Antigravity App", names)
self.assertIn("Antigravity CLI", names)
self.assertIn("Antigravity IDE", names)
self.assertNotIn("Gemini / Antigravity", names)
```

Also remove the CLI Skill symlink and assert the only new coverage finding
names `Antigravity CLI`.

- [ ] **Step 2: Run the Doctor test and confirm RED**

Run:

```bash
python3 -m unittest tests.test_agent_doctor.AgentDoctorTests.test_doctor_splits_antigravity_surfaces -v
```

Expected: FAIL because Doctor still returns the aggregated record.

- [ ] **Step 3: Replace the aggregated target**

Use four target tuples:

```python
("Gemini global", "gemini", HOME / ".gemini/config/mcp_config.json", HOME / ".gemini/config/skills", "json", mcp_reader, ("Gemini.app",)),
("Antigravity App", "", HOME / ".gemini/antigravity/mcp_config.json", HOME / ".gemini/antigravity/skills", "json", mcp_reader, ("Antigravity.app",)),
("Antigravity CLI", "agy", HOME / ".gemini/antigravity-cli/mcp_config.json", HOME / ".gemini/antigravity-cli/skills", "json", mcp_reader, ()),
("Antigravity IDE", "", HOME / ".gemini/antigravity-ide/mcp_config.json", HOME / ".gemini/antigravity-ide/skills", "json", mcp_reader, ("Antigravity IDE.app",)),
```

Update `capability_labels()` so App/CLI expose MCP and plugins when their
product plugin directory exists, while IDE exposes MCP and extensions when its
extension directory exists. Do not change MCP scope-policy source labels.

- [ ] **Step 4: Run Doctor tests and confirm GREEN**

Run:

```bash
python3 -m unittest tests.test_agent_doctor -v
```

Expected: all tests pass.

- [ ] **Step 5: Update public documentation and version**

Document the four surfaces, their paths, and the difference between
`antigravity` (App launcher) and `agy` (standalone CLI). Add a changelog entry
and increment the minor version from `1.7.0` to `1.8.0`.

- [ ] **Step 6: Commit Doctor and documentation**

```bash
git add scripts/agent-doctor.py tests/test_agent_doctor.py README.md CHANGELOG.md VERSION
git commit -m "feat: report Antigravity surfaces separately"
```

### Task 4: Install, synchronize, verify, and publish

**Files:**
- Modify through normal execution: local Agent Skills and MCP configuration targets.

**Interfaces:**
- Consumes: the completed `agent-sync` implementation and private Hub.
- Produces: synchronized local targets and a published public `agent-sync` release commit.

- [ ] **Step 1: Run the complete automated suite**

Run:

```bash
python3 -m unittest discover -s tests -v
agent-sync test
```

Expected: all unit tests and evaluation checks pass.

- [ ] **Step 2: Synchronize the real machine**

Run:

```bash
agent-sync all
```

Expected: App, CLI, and IDE each show 15/15 Skills and 7/7 shared MCP servers.

- [ ] **Step 3: Run strict health and privacy checks**

Run:

```bash
agent-sync verify --strict
agent-sync doctor --json
agent-sync audit
```

Expected: verification succeeds, Doctor reports no synchronization findings,
and privacy audit reports zero failures and zero warnings.

- [ ] **Step 4: Verify product-only ownership**

Inspect name-only MCP inventories and confirm `claude-mem` and `wolfbook`
remain present only where previously configured. Do not print environment
values.

- [ ] **Step 5: Push and verify the public repository**

```bash
git push origin main
git ls-remote origin refs/heads/main
```

Expected: local `HEAD` equals the remote `main` hash and the worktree is clean.
