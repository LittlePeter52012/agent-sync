# Bounded Orchestration Implementation Plan

> **For Codex:** Execute inline without subagents. This repair exists to prevent
> runaway delegation and must be verified before any new long-running task.

**Goal:** Enforce quota-aware Codex and Multica orchestration while preserving
bounded high-quality review.

**Architecture:** Agent Hub stores desired values and routing rules.
`agent-doctor.py` reports drift. A small reconciler performs the narrow Codex
configuration repair. Existing rule and Multica adapters distribute the
behavioral policy.

**Tech Stack:** Python standard library, Bash, TOML, JSON, Markdown, unittest.

---

### Task 1: Reproduce Missing Codex Orchestration Diagnostics

**Files:**
- Modify: `tests/test_agent_doctor.py`

Add fixtures with stale forbidden plugin hook state and unsafe/missing
`[agents]` defaults. Assert that doctor reports both without printing a secret
sentinel. Run the focused tests and confirm RED.

### Task 2: Add Doctor Support

**Files:**
- Modify: `scripts/agent-doctor.py`

Parse the private Hub orchestration policy and the Codex TOML. Report unsafe
agent defaults, forbidden plugin configuration, and stale forbidden hook
state. Run the focused tests and confirm GREEN.

### Task 3: Add a Narrow Reconciler

**Files:**
- Create: `scripts/reconcile-codex-orchestration.py`
- Create: `tests/test_reconcile_codex_orchestration.py`
- Modify: `bin/agent-sync`

Write failing tests for dry-run, minimal apply, preservation, and idempotence.
Implement the reconciler and connect it to `agent-sync fix`.

### Task 4: Publish Desired Policy

**Files:**
- Modify in private Hub: `policies/tool-scopes.json`
- Create in private Hub: `rules/bounded-orchestration.md`
- Modify in private Hub: `multica/protocols/shared-execution.md`
- Modify in private Hub: `multica/squads/*.md`

Store the hard Codex defaults, synchronized operating rules, and bounded
Multica delegation contract.

### Task 5: Apply and Verify

Run focused tests, the full agent-sync test suite, `agent-sync fix`,
`agent-sync rules`, `agent-sync multica --apply`, strict verification, privacy
audit, and final doctor checks. Confirm that OMO remains available only to
OpenCode and Codex retains native bounded multi-agent capability.
