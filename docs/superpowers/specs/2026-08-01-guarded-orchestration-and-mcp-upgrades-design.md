# Guarded Orchestration and MCP Dependency Upgrades Design

**Date:** 2026-08-01

## Goal

Improve Agent Hub and `agent-sync` in four related areas without weakening
account isolation or turning synchronization into an uncontrolled updater:

1. replace the local absolute ban on recursive delegation with a small,
   auditable second-level delegation allowance;
2. pin package-launched shared MCP servers while allowing Agents to discover,
   assess, test, and adopt safe updates before synchronization;
3. separate Copilot Skill policy from VS Code MCP policy so two GitHub account
   roles are not conflated by internal target names;
4. remove false success and false failure states in plugin ownership and
   synchronization verification.

The design keeps the private Agent Hub as the desired-state source and the
public `agent-sync` repository free of personal credentials and private policy.

## Evidence and Constraints

- Current Codex releases enable multi-agent workflows by default. The product
  documentation recommends bounded delegation for independent work and warns
  that every helper consumes additional tokens. It does not define a default
  `max_depth` setting or require an absolute recursion prohibition.
- The local `Never delegate recursively` rule came from a previous runaway
  task with 27 spawned agents, 229 waits, 948 tool calls, nine context
  compactions, and two rate-limit windows. That evidence justifies budgets and
  stop conditions, but not a permanent ban on every second-level decomposition.
- The Hub currently launches MinerU through unversioned
  `uvx mineru-open-mcp` and Obsidian through
  `npx @mauricio.wolff/mcp-obsidian@latest`. A passing sync can therefore run
  different upstream code on different days.
- As checked on 2026-08-01, `mineru-open-mcp==1.0.21` is the current PyPI
  stable release. The current Obsidian launcher
  `@mauricio.wolff/mcp-obsidian` is deprecated at `0.8.2`; upstream renamed it
  to `@bitbonsai/mcpvault`, whose current stable release was `0.12.5` at the
  2026-08-01 registry check. The new
  package preserves the local stdio launcher and vault-path argument while
  adding later security fixes. This is a reviewed package migration, not a
  reason to trust future latest versions automatically.
- Doctor currently represents the combined `Copilot / VS Code` surface with
  one `vscode` target key even though the Skill directory is managed under the
  `copilot` target and the MCP configuration is managed under `vscode`. This
  can ignore Copilot-specific Skill exclusions. It is a policy-routing defect,
  not evidence of an account or token collision.
- The user intentionally uses separate account roles: one for Copilot AI and
  one for personal GitHub repository/VS Code work. Their authentication stores,
  Git remotes, credential helpers, and product login state are out of scope.

## Considered Approaches

### 1. Minimal manual patch

Change the prose orchestration rule, manually pin the two MCP packages, and
fix the Doctor target key. This is small but will drift again and gives no safe
upgrade path.

### 2. Guarded maintenance pipeline

Use a conditionally bounded second level of delegation, exact MCP pins,
deterministic registry discovery, Agent-reviewed release assessment, focused
runtime smoke tests, and explicit synchronization. Split Copilot and VS Code
policy identities while leaving account state untouched. Add scoped
verification and stricter capability identity rules.

### 3. Fully autonomous latest-following and unrestricted recursion

Let `agent-sync all` install registry latest versions and let every helper
spawn freely. This is convenient when everything works, but it makes upstream
supply-chain changes, breaking releases, token fan-out, and root-cause
attribution unpredictable.

Use approach 2.

## Design

### 1. Conditionally bounded delegation

The Hub's synchronized orchestration rule will keep a single accountable main
Agent and permit one second-level decomposition only under an explicit task
budget:

- depth 0 is the main Agent and owns the final answer and all shared writes;
- a depth-1 helper may spawn at most one depth-2 leaf only when the original
  assignment or a later main-Agent message grants that allowance;
- depth 2 is terminal and may not delegate;
- the default task budget is two spawned Agents in total, independent of the
  product's own concurrency default;
- every assignment must name one bounded deliverable, allowed tools, write
  ownership, and the delegation/token budget;
- helpers may perform read-only exploration or isolated work, but must not
  mutate the same file or external object concurrently;
- status is collected through completion-aware waits rather than rapid polling;
- no task is sent through both Codex native delegation and Multica;
- new delegation stops when surfaced use jumps by ten percentage points in one
  task, remaining capacity reaches twenty percent, or repeated context
  compaction indicates a polluted workflow.

This is controlled two-level decomposition, not unrestricted recursive
fan-out. Simple work remains single-Agent.

### 2. Exact pins with Agent-reviewed upgrades

The canonical Hub launch specifications will use exact versions. The public
tool gains a generic dependency workflow for recognized package launchers:

```text
agent-sync deps check [--json]
agent-sync deps pin SERVER VERSION [--dry-run|--apply]
```

`deps check` is read-only. It recognizes exact, `latest`, and unversioned
package requirements in Hub MCP entries, queries the corresponding primary
registry, and returns only package, current pin state, available stable
version, deprecation state, ecosystem, and upstream metadata links. This lets
the first run diagnose the Hub's existing unpinned entries instead of refusing
to inspect them. It never prints environment values, headers, OAuth data,
vault paths, or account identifiers. Network failure produces an `unknown`
result rather than a broken-Hub verdict.

`deps pin` changes only the version component of an already recognized Hub
package requirement, including conversion from `latest` or unversioned to an
exact pin. It cannot rename the package, change the executable, modify
environment fields, or edit target copies directly. A deprecated-package
rename is a separate, explicit Hub migration supported by primary upstream
evidence and a candidate smoke test. `--dry-run` is the default; `--apply`
requires an exact version and a clean, reviewable Hub diff.

When an Agent is asked to maintain or synchronize configuration, the
synchronized rule requires this sequence:

1. run `deps check --json` before distribution;
2. inspect primary release notes, compatibility metadata, and the version
   delta with the active model;
3. leave major, breaking, yanked, prerelease, or ambiguous updates unapplied
   and report the reason;
4. for a safe candidate, pin it in an isolated Hub branch or worktree;
5. smoke-test MCP initialization and tool discovery without exposing secrets;
6. run focused tests, the complete `agent-sync test`, Hub governance checks,
   synchronization, and final runtime/privacy verification;
7. push Hub and public-tool changes only after all required checks pass.

An ordinary terminal invocation of `agent-sync all` performs a cached update
check and reports candidates, but never silently rewrites pins. The "LLM
judgment" is supplied by the active Agent that is already conducting the
maintenance task, not by embedding a second opaque model/API dependency inside
the shell tool.

### 3. Copilot and VS Code target separation

Doctor's combined presentation record will carry two policy keys:

```text
label: Copilot / VS Code
skill_target_key: copilot
mcp_target_key: vscode
```

The first key controls `~/.copilot/skills` and
`distribution.copilot.exclude_skills`. The second controls the VS Code user MCP
file, profile replicas, `distribution.vscode.exclude_mcp`, and
`secrets.targets.vscode`.

No compatibility alias will translate one key into the other. A test fixture
will give the two targets intentionally different policies and prove that a
Copilot Skill exclusion cannot suppress a VS Code MCP, and vice versa. VS Code
profile identities remain `vscode:<profile-id>` where already supported.

This change touches only Agent Sync's coverage model. It must not inspect,
merge, copy, delete, or reauthenticate GitHub tokens, Copilot subscriptions,
credential helpers, Git remotes, or VS Code account sessions.

### 4. Capability ownership identity

For MCP capabilities supplied by a product plugin:

- an exact normalized transport identity may automatically satisfy or replace
  a Hub capability;
- equal names with different transports are a visible collision, not automatic
  ownership;
- semantic aliases require an explicit
  `plugins.<target>.prefer_marketplace.<plugin>.mcp` policy entry;
- Skill name equality remains sufficient for exact Skill duplication because
  Skills do not have an executable transport identity.

This prevents a same-name but unrelated plugin server from making Hub coverage
look complete.

### 5. Verification scopes

Structural synchronization and all-product runtime health answer different
questions and must not be collapsed into one exit code.

- `agent-sync verify --strict` retains its current all-surface compatibility
  behavior.
- `agent-sync verify --strict --scope managed` checks Hub-managed Skills,
  shared MCPs, rules, their required runtime coverage, and all malformed Hub
  policy. Product-owned and tool-only runtime findings remain visible but do
  not falsely fail the managed synchronization result. A same-name runtime is
  managed only when target-specific coverage says its owner is `hub-managed`;
  plugin-owned or excluded capabilities remain local findings. A retired Hub
  MCP left in any product configuration is a managed retirement failure.
- `agent-sync doctor --runtime --strict` remains the full machine-health gate
  and may fail for a broken product-owned or tool-only capability.
- `agent-sync all` runs one full runtime Doctor report and uses only its managed
  findings for the synchronization exit gate. Product-owned and tool-only
  findings stay visible in that same report, avoiding a second expensive probe.

Every finding includes `scope` and `owner` in JSON output so callers do not
infer ownership from a label.

### 6. Hub governance checks

The Hub gains one cross-invariant test that verifies:

- manifest names and Skill frontmatter are unique and consistent;
- shared, retired, and tool-only MCP names are disjoint;
- package-launched shared MCP entries use exact stable pins rather than
  `@latest`, bare npm package names, or unversioned `uvx` requirements;
- synchronized rule block names are unique;
- Multica adapter paths and declared ownership remain valid;
- no credential-shaped values occur in tracked policy or documentation.

The test validates policy structure; it does not contact registries. Registry
availability belongs to the separate dependency check.

## Failure and Rollback Behavior

- Registry outage: keep the existing exact pin and continue synchronization
  with an update-status warning.
- Candidate fails initialization or tool discovery: restore the prior pin,
  retain test evidence, and do not distribute the candidate.
- A target-specific runtime is broken: managed sync may complete, while the
  full Doctor still reports the affected target and owner.
- Plugin evidence is stale or ambiguous: keep the Hub capability and report
  uncertainty; never delete a product plugin.
- Copilot/VS Code policy migration: no auth files are donors or destinations.
  Temporary-HOME fixtures, command review, and repository diffs prove the
  boundary without reading or hashing account stores.
- Orchestration begins to fan out: the main Agent stops new delegation,
  collects existing results once, and resumes with a compact evidence summary.

## Implementation Boundaries

The first implementation changes only:

- the private Hub orchestration rule and shared MCP package pins;
- a small Hub governance test;
- public `agent-sync` dependency discovery/pinning commands;
- Doctor target-key representation;
- capability ownership comparison;
- verification scopes, documentation, and focused tests.

It does not clean unrelated repositories, change GitHub/Copilot login state,
install remote plugins, promote tool-only MCPs, add another orchestration
system, or refactor unrelated synchronization code. Already-merged stale
worktrees may be removed only as a separate, explicitly reported housekeeping
step after implementation succeeds.

## Acceptance Criteria

1. A depth-1 helper with an explicit allowance can delegate one bounded leaf;
   a depth-2 helper is instructed and tested as terminal.
2. No local Codex concurrency/model defaults are reintroduced; product
   defaults remain authoritative.
3. MinerU is pinned to an exact stable version, the deprecated Obsidian package
   is migrated to an exact stable `@bitbonsai/mcpvault` release, and both MCP
   initialization/tool-list smoke tests pass.
4. `deps check --json` reports current and available versions deterministically
   for pinned npm/PyPI entries and degrades safely offline.
5. `deps pin` refuses package renames, prerelease ambiguity, unrecognized
   launchers, missing exact versions, and direct edits to target copies.
6. Copilot Skill and VS Code MCP policies can differ without a coverage
   misjudgment, and no auth/account file changes.
7. Same-name/different-transport plugin fixtures report a collision instead of
   suppressing the Hub entry; exact transport and explicit-policy fixtures
   resolve correctly.
8. Managed verification fails for a broken shared MCP but not for an unrelated
   tool-only runtime; full runtime Doctor still reports and fails the latter.
9. Hub governance rejects `@latest` and unversioned package launch specs.
10. The focused test suite, complete public suite, Hub test, privacy audit,
    two consecutive idempotent syncs, final status, and runtime Doctor pass
    before either repository is pushed.
