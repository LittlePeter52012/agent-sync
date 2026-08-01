# Hub Orchestration and Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt exact MCP pins and controlled two-level delegation in the private Agent Hub, validate every invariant, synchronize all managed Agents, and publish both repositories safely.

**Architecture:** Add one standard-library Hub governance test before changing policy, use the newly tested public dependency command for version changes, encode Agent-reviewed maintenance and bounded delegation as separate synchronized rules, then perform idempotent local rollout and remote Git publication. The Hub remains desired state; product plugins and account stores remain outside it.

**Tech Stack:** Python 3 standard library, JSON/Markdown Hub policy, `unittest`, `agent-sync` 1.11.0, Git/GitHub remotes, local MCP runtimes.

## Global Constraints

- Hub main and public tool main must begin clean; implementation occurs in isolated worktrees and merges only after review.
- Exact 2026-08-01 registry evidence is `mineru-open-mcp==1.0.21` and
  `@bitbonsai/mcpvault@0.12.5`, subject to a fresh primary-registry check at
  execution time. The current `@mauricio.wolff/mcp-obsidian` package is
  deprecated and may be retained only if the successor migration fails the
  candidate smoke/compatibility gates.
- Depth 0 owns final output and shared writes; an authorized depth-1 helper may spawn at most one terminal depth-2 leaf.
- Default task budget is two spawned Agents total; product concurrency defaults remain untouched.
- Codex native delegation and Multica never run the same task.
- No GitHub/Copilot authentication, credential helper, Git remote, VS Code session, product plugin, or tool-only MCP is changed.
- Every configuration change is preceded by a failing governance test or an explicit configuration-test exception documented in the task.
- After every task: inspect the diff, run focused tests, and obtain one bounded read-only review.

## Execution Order

Start this plan only after the combined public Agent Sync worktree has completed
both public plans, passed review, and been merged into local public `main`.
Create one separate Hub worktree and execute Tasks 1–4 entirely there. Use the
merged public binary at
`$HOME/.local/share/agent-sync/bin/agent-sync` with
`AGENT_HUB_ROOT="$PWD"`. Merge only the Hub worktree in Task 5. Neither
repository is pushed before the final joint gate.

---

## File Map

- `tests/test_hub_governance.py`: cross-invariant Hub policy tests.
- `mcp/shared-servers.json`: exact MinerU and Obsidian package pins.
- `rules/bounded-orchestration.md`: conditional depth-2 allowance and budgets.
- `rules/mcp-dependency-maintenance.md`: Agent-driven check/review/pin/smoke/sync route.
- `README.md`, `TOOLING.zh-CN.md`, `USAGE.zh-CN.md`: ownership and usage guidance.
- Agent target rule/config files: generated only by `agent-sync all`, never edited manually.

### Task 1: Establish Hub governance tests before policy changes

**Files:**
- Create: `$HOME/.config/agent-hub/tests/test_hub_governance.py`

**Interfaces:**
- `load_json(relative: str) -> dict`.
- `manifest_skills() -> list[str]`.
- Pure local `unittest`; no registry/network access.

- [ ] **Step 1: Write the governance test module**

Implement tests that assert:

```python
def test_shared_retired_and_tool_only_names_are_disjoint(self):
    self.assertFalse(self.shared & self.retired)
    for policy in self.tool_only_policies:
        self.assertFalse(self.shared & policy)

def test_package_launchers_use_exact_stable_pins(self):
    for name, config in self.shared_config.items():
        command = Path(str(config.get("command", ""))).name
        args = config.get("args", [])
        if command == "uvx":
            self.assertRegex(
                args[0], r"^[A-Za-z0-9_.-]+==[0-9]+(?:\.[0-9]+)+$", name
            )
        elif command == "npx":
            self.assertRegex(
                args[0], r"^(?:@[^/]+/)?[^@]+@[0-9]+(?:\.[0-9]+)+$", name
            )

def test_manifest_and_skill_frontmatter_are_unique(self):
    self.assertEqual(len(self.skills), len(set(self.skills)))
    for name in self.skills:
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        frontmatter = text.split("---", 2)[1]
        declared = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        self.assertEqual(declared.group(1).strip(), name)

def test_rule_names_are_unique_and_nonempty(self):
    names = [path.stem for path in (ROOT / "rules").glob("*.md")]
    self.assertEqual(len(names), len(set(names)))
    self.assertTrue(all(path.read_text().strip() for path in (ROOT / "rules").glob("*.md")))

def test_multica_declared_paths_exist(self):
    desired = load_json("multica/desired-state.json")
    self.assertEqual(desired["schema_version"], 1)
    self.assertRegex(desired["minimum_multica_version"], r"^[0-9]+(?:\.[0-9]+){2}$")
    skill_names = [item["name"] for item in desired["skills"]]
    squad_names = [item["name"] for item in desired["squads"]]
    agent_names = [
        item["agent"] for item in desired["agent_skill_assignments"]
    ]
    self.assertEqual(len(skill_names), len(set(skill_names)))
    self.assertEqual(len(squad_names), len(set(squad_names)))
    self.assertTrue(all(name.strip() for name in agent_names))
    self.assertEqual(len(agent_names), len(set(agent_names)))
    known = set(skill_names)
    for assignment in desired["agent_skill_assignments"]:
        self.assertLessEqual(set(assignment["skills"]), known)
        self.assertEqual(
            len(assignment["skills"]), len(set(assignment["skills"]))
        )
    for squad in desired["squads"]:
        self.assertIn(squad["leader"], set(agent_names))
    paths = [
        item["source"]
        for item in desired["skills"]
        if item.get("managed", True)
    ]
    paths.extend(item["instructions_file"] for item in desired["squads"])
    for relative in paths:
        resolved = (ROOT / relative).resolve()
        self.assertTrue(resolved.is_relative_to(ROOT.resolve()), relative)
        self.assertTrue(resolved.exists(), relative)
```

Read tool-only names from `policies/tool-scopes.json` keys
`allowed_tool_only` and `required_tool_only`. Do not scan arbitrary Skill tests
or documentation for words such as `token`. Instead, recursively validate only
structured `env`, `headers`, and `http_headers` values in
`mcp/shared-servers.json`: every nonempty string must match `^\$\{[A-Z0-9_]+\}$`.
Assert `secrets.targets.*.allow_secret_donors` values are booleans. The existing
public privacy audit remains responsible for high-entropy token detection
across tracked/untracked repository text.

- [ ] **Step 2: Run the tests and witness the intended RED**

```bash
python3 -m unittest tests.test_hub_governance -v
```

Expected: the exact-pin test fails on bare MinerU and Obsidian `@latest`; other
invariants either pass or expose a real pre-existing governance defect that is
reported before proceeding.

- [ ] **Step 3: Review the test itself**

Confirm it reads only tracked Hub policy, cannot print secret values, and does
not encode the current package versions as expected values. Commit the test
while RED so the next task has a proven gate:

```bash
git add tests/test_hub_governance.py
git commit -m "test: add Agent Hub governance invariants"
```

### Task 2: Check MinerU and migrate the deprecated Obsidian package safely

**Files:**
- Modify: `$HOME/.config/agent-hub/mcp/shared-servers.json`

**Interfaces:**
- Consumes `agent-sync deps check|pin|smoke` from public version 1.11.0.
- Produces exact canonical package arguments in the isolated Hub worktree; no
  target copies yet.

Execute Steps 1–4 in one persistent PTY session rooted at the Hub worktree so
the three reviewed version variables remain in scope. File edits still use
`apply_patch`; the PTY is only for checks, pinning, and smoke commands.

- [ ] **Step 1: Run a fresh registry check and inspect primary metadata**

```bash
AGENT_HUB_ROOT="$PWD" "$HOME/.local/share/agent-sync/bin/agent-sync" deps check --json --refresh
```

For each candidate, open only the returned primary registry/source metadata.
Reject prerelease, yanked, major, breaking, abandoned, or ambiguous releases.
Confirm that npm marks `@mauricio.wolff/mcp-obsidian` deprecated and that the
official successor keeps the local stdio invocation plus the same vault-path
argument. If current stable versions differ from 1.0.21/0.12.5, record the
evidence and use the newly verified safe exact versions instead of mechanically
copying the design-date candidates.

After the Agent approves the registry evidence, record the exact reviewed
values in the current shell and use them in every later command:

```bash
MINERU_VERSION="1.0.21"
LEGACY_OBSIDIAN_VERSION="0.8.2"
MCPVAULT_VERSION="0.12.5"
export MINERU_VERSION LEGACY_OBSIDIAN_VERSION MCPVAULT_VERSION
```

If the registry evidence changed, replace the literal assigned value before
exporting; do not change later commands independently.

- [ ] **Step 2: Dry-run both pins**

```bash
AGENT_HUB_ROOT="$PWD" "$HOME/.local/share/agent-sync/bin/agent-sync" deps pin mineru "$MINERU_VERSION" --dry-run
AGENT_HUB_ROOT="$PWD" "$HOME/.local/share/agent-sync/bin/agent-sync" deps pin obsidian "$LEGACY_OBSIDIAN_VERSION" --dry-run
git diff -- mcp/shared-servers.json
```

Expected: no live change during dry-run. MinerU shows an exact version-only
change; the old Obsidian package can be pinned in place but cannot be silently
renamed by `deps pin`, proving the package-identity safety boundary.

- [ ] **Step 3: Apply and smoke-test the MinerU exact pin**

```bash
AGENT_HUB_ROOT="$PWD" "$HOME/.local/share/agent-sync/bin/agent-sync" deps pin mineru "$MINERU_VERSION" --apply
AGENT_HUB_ROOT="$PWD" "$HOME/.local/share/agent-sync/bin/agent-sync" deps smoke mineru --target cursor --timeout 60
```

Expected: initialize succeeds and returns a positive tool count.

Commit the tested MinerU pin so the worktree is clean before the explicit
Obsidian package migration:

```bash
git add mcp/shared-servers.json
git commit -m "chore: pin MinerU MCP version"
```

- [ ] **Step 4: Migrate Obsidian in the isolated Hub worktree and smoke-test it**

Use `apply_patch` to change only the first Obsidian argument from
`@mauricio.wolff/mcp-obsidian@latest` to the exact requirement formed by
`@bitbonsai/mcpvault@${MCPVAULT_VERSION}`. The patch must contain the resolved
numeric value, not the shell placeholder. Then run with a temporary empty vault:

```bash
OBSIDIAN_VAULT="$(mktemp -d)"
export OBSIDIAN_VAULT
AGENT_HUB_ROOT="$PWD" "$HOME/.local/share/agent-sync/bin/agent-sync" deps check --json --refresh
AGENT_HUB_ROOT="$PWD" "$HOME/.local/share/agent-sync/bin/agent-sync" deps smoke obsidian --timeout 60
rmdir "$OBSIDIAN_VAULT"
unset OBSIDIAN_VAULT
```

Expected: the new package is exact, non-deprecated, initializes, and returns at
least this legacy tool set:

```text
delete_note get_frontmatter get_notes_info get_vault_stats list_directory
manage_tags move_file move_note patch_note read_multiple_notes read_note
search_notes update_frontmatter write_note
```

Compare the returned sorted `tools` list to this baseline. Additional tools are
accepted only when they remain local to the same vault boundary.

If migration fails, restore the old package name, pin it to
`$LEGACY_OBSIDIAN_VERSION`, record the
deprecation risk, and do not claim the Obsidian upgrade complete.

- [ ] **Step 5: Run the governance test GREEN**

```bash
python3 -m unittest tests.test_hub_governance -v
git diff --check
```

- [ ] **Step 6: Review and commit the Obsidian migration**

```bash
git add mcp/shared-servers.json
git commit -m "chore: migrate Obsidian MCP package"
```

### Task 3: Replace the absolute recursion ban with guarded two-level delegation

**Files:**
- Modify: `$HOME/.config/agent-hub/tests/test_hub_governance.py`
- Modify: `$HOME/.config/agent-hub/rules/bounded-orchestration.md`

**Interfaces:**
- Synchronized rule text only; no Codex concurrency/model setting changes.

- [ ] **Step 1: Add a failing orchestration-policy test**

Assert the rule:

```python
def test_orchestration_allows_only_authorized_terminal_second_level(self):
    text = (ROOT / "rules/bounded-orchestration.md").read_text()
    self.assertNotIn("Never delegate recursively", text)
    for phrase in (
        "depth-1 helper may spawn at most one depth-2 leaf",
        "depth 2 is terminal",
        "default task budget is two spawned Agents",
        "one accountable writer",
        "Do not run the same task through both control planes",
    ):
        self.assertIn(phrase, text)
```

- [ ] **Step 2: Run the test and witness RED**

Expected: FAIL on the current absolute prohibition and missing depth/budget
language.

- [ ] **Step 3: Make the smallest rule change**

Replace only item 2 and add the task-budget/assignment details needed by the
test. Preserve product-default concurrency, smallest-useful-helper guidance,
wait discipline, context handoff, rate-limit stops, and Multica separation.

- [ ] **Step 4: Run governance tests GREEN and review semantics**

```bash
python3 -m unittest tests.test_hub_governance -v
git diff -- rules/bounded-orchestration.md tests/test_hub_governance.py
```

Confirm depth 2 cannot spawn, a depth-1 helper needs explicit allowance, and
the main Agent remains the only shared writer.

- [ ] **Step 5: Commit Task 3**

```bash
git add rules/bounded-orchestration.md tests/test_hub_governance.py
git commit -m "policy: allow guarded second-level delegation"
```

### Task 4: Add Agent-reviewed dependency maintenance guidance

**Files:**
- Modify: `$HOME/.config/agent-hub/tests/test_hub_governance.py`
- Create: `$HOME/.config/agent-hub/rules/mcp-dependency-maintenance.md`
- Modify: `$HOME/.config/agent-hub/TOOLING.zh-CN.md`
- Modify: `$HOME/.config/agent-hub/USAGE.zh-CN.md`

**Interfaces:**
- New synchronized rule owns only maintenance ordering, not MCP ownership.

- [ ] **Step 1: Add a failing rule-content test**

Require these ordered concepts in the new rule:

```python
required = (
    "agent-sync deps check --json",
    "primary release notes",
    "agent-sync deps pin",
    "agent-sync deps smoke",
    "agent-sync test",
    "agent-sync all",
)
positions = [text.index(value) for value in required]
self.assertEqual(positions, sorted(positions))
self.assertIn("never silently apply", text)
```

- [ ] **Step 2: Run the test and witness RED**

Expected: FAIL because the rule file does not exist.

- [ ] **Step 3: Create the concise maintenance rule**

State that an Agent handling configuration maintenance must check first, use
the active model to read primary compatibility evidence, skip ambiguous or
breaking updates, dry-run/apply an exact pin, smoke-test, run public/Hub tests,
then synchronize. A manual `all` may report candidates but must never silently
apply them. Registry outage retains the existing pin.

- [ ] **Step 4: Update user documentation without duplicating policy**

In Chinese docs, add the three commands and one short decision table:

```text
无更新 -> 直接同步
安全的小版本更新 -> 精确 pin、smoke、测试、同步
大版本/破坏性/证据不足 -> 保留当前 pin 并报告
注册表不可用 -> 保留当前 pin，允许既有配置同步
```

- [ ] **Step 5: Run governance tests and review**

```bash
python3 -m unittest tests.test_hub_governance -v
git diff --check
```

- [ ] **Step 6: Commit Task 4**

```bash
git add rules/mcp-dependency-maintenance.md tests/test_hub_governance.py TOOLING.zh-CN.md USAGE.zh-CN.md
git commit -m "docs: add guarded MCP maintenance route"
```

### Task 5: Merge the reviewed Hub worktree and perform local synchronization

**Files:**
- Generated managed targets under the user's home directory.
- No manual edits to generated target blocks.

- [ ] **Step 1: Re-run both repositories' tests before merge**

```bash
git -C "$HOME/.local/share/agent-sync" status --short --branch
"$HOME/.local/share/agent-sync/bin/agent-sync" test
"$HOME/.local/share/agent-sync/bin/agent-sync" audit
git status --short --branch
python3 -m unittest discover -s tests -v
```

- [ ] **Step 2: Independent pre-merge review**

Give one read-only reviewer the public-main and Hub-worktree commit ranges, the design, all three plans,
and fresh test outputs. Verify each reported issue locally; accepted issues get
a failing regression test and a focused fix before merge.

- [ ] **Step 3: Merge the Hub worktree into local Hub main**

The public branch was already merged before this plan began. Use
`git merge --ff-only` for the Hub when history permits. Never reset, checkout
over user changes, or merge with a dirty destination. Confirm both public and
Hub main worktrees are clean after the Hub merge.

- [ ] **Step 4: Run the update decision before synchronization**

```bash
"$HOME/.local/bin/agent-sync" deps check --json --refresh
```

If a new candidate appeared since Task 2, do not silently apply it. Re-enter
Task 2 review/smoke gates or retain the tested pin.

- [ ] **Step 5: Synchronize twice and prove idempotency**

Hash only managed target configuration files, synchronized rule targets, and
managed Skill link destinations. The helper prints one digest and never file
contents:

```bash
managed_digest() {
python3 - <<'PY'
import hashlib
import os
import sys
from pathlib import Path

home = Path.home()
hub = Path.home() / ".config/agent-hub"
scripts = Path.home() / ".local/share/agent-sync/scripts"
sys.path.insert(0, str(scripts))
from config_inventory import _source_specs

digest = hashlib.sha256()
files = [path for _tool, _label, path, _bucket in _source_specs(home)]
files.extend((
    home / ".claude/CLAUDE.md",
    home / ".codex/AGENTS.md",
    home / ".gemini/GEMINI.md",
))
for path in sorted(set(files), key=str):
    digest.update(str(path).encode())
    if path.exists():
        digest.update(path.read_bytes())

skills = []
in_skills = False
for line in (hub / "manifest.yaml").read_text().splitlines():
    if line == "skills:":
        in_skills = True
    elif in_skills and line.startswith("  - "):
        skills.append(line[4:].strip())
    elif in_skills and line and not line.startswith(" "):
        break
skill_roots = (
    home / ".claude/skills", home / ".cursor/skills",
    home / ".codex/skills", home / ".agents/skills",
    home / ".gemini/config/skills", home / ".gemini/antigravity/skills",
    home / ".gemini/antigravity-cli/skills",
    home / ".gemini/antigravity-ide/skills",
    home / ".config/opencode/skills", home / ".copilot/skills",
)
for root in skill_roots:
    for skill in skills:
        path = root / skill
        digest.update(str(path).encode())
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        elif (path / "SKILL.md").exists():
            digest.update((path / "SKILL.md").read_bytes())
print(digest.hexdigest())
PY
}

"$HOME/.local/bin/agent-sync" all
AFTER_ONE="$(managed_digest)"
"$HOME/.local/bin/agent-sync" all
AFTER_TWO="$(managed_digest)"
test "$AFTER_ONE" = "$AFTER_TWO"
"$HOME/.local/bin/agent-sync" status
"$HOME/.local/bin/agent-sync" verify --strict --scope managed
```

The first run may intentionally change coverage from the pre-sync state. The
second run must leave the managed-target digest unchanged. Any drift is
diagnosed before publication.

- [ ] **Step 6: Run full machine health separately**

```bash
"$HOME/.local/bin/agent-sync" doctor --runtime --strict
```

If a tool-only/product-owned runtime fails, report its owner and repair only
when it falls within the user's existing configuration scope. Do not promote it
to the Hub.

### Task 6: Publish Agent Hub and public Agent Sync

**Files:**
- No new changes unless verification exposes a tested defect.

- [ ] **Step 1: Final privacy and repository checks**

```bash
"$HOME/.local/bin/agent-sync" audit
git -C "$HOME/.local/share/agent-sync" status --short --branch
git -C "$HOME/.config/agent-hub" status --short --branch
git -C "$HOME/.local/share/agent-sync" log --oneline --decorate -n 12
git -C "$HOME/.config/agent-hub" log --oneline --decorate -n 12
```

Confirm no secret-bearing or unrelated file is staged and each local main is
only ahead of its intended origin.

- [ ] **Step 2: Push the public tool**

```bash
git -C "$HOME/.local/share/agent-sync" push origin main
```

Confirm the remote-tracking branch reaches the same commit.

- [ ] **Step 3: Push the already-reviewed private Hub commit directly**

```bash
git -C "$HOME/.config/agent-hub" push origin main
```

Do not call `agent-sync push` here because its `git add -A` behavior could stage
a late unrelated file. Confirm Hub origin and local main point at the same
commit.

- [ ] **Step 4: Final fresh verification after push**

```bash
"$HOME/.local/bin/agent-sync" status
"$HOME/.local/bin/agent-sync" verify --strict --scope managed
"$HOME/.local/bin/agent-sync" doctor --runtime --strict
```

Report exact test counts, runtime findings if any, public/Hub commit hashes, and
the narrower verified fact that no authentication, account-state mutation, or
credential migration command was executed. Health/audit commands may read or
use existing authenticated sessions without modifying them.
