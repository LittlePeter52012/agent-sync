# Agent Sync CLI-Backed Skill Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit local executable dependencies for shared CLI-backed Skills.

**Architecture:** The existing Doctor reads an optional generic Skill policy
from the private Hub, validates it, and resolves required commands without
executing them. Structural synchronization and MCP behavior remain unchanged.

**Tech Stack:** Python 3 standard library, JSON, `unittest`, Bash.

## Global Constraints

- No new runtime dependency.
- Public code and examples contain no personal Skill names or paths.
- Dependency checks are read-only and secret-free.
- Agent Sync never installs or updates third-party CLIs.

---

### Task 1: Skill dependency doctor checks

**Files:**
- Modify: `scripts/agent-doctor.py`
- Modify: `tests/test_agent_doctor.py`

**Interfaces:**
- Produces: `skill_dependency_findings() -> list[dict[str, str]]`

- [x] Add failing tests for a missing command, an available command, an
  unlisted Skill, and malformed `required_commands`.
- [x] Run `python3 -m unittest tests.test_agent_doctor -v` and confirm the new
  assertions fail because Skill dependency findings do not exist.
- [x] Implement the minimal policy reader and command availability checks.
- [x] Re-run the focused tests and confirm they pass.

### Task 2: Public documentation and release metadata

**Files:**
- Modify: `README.md`
- Modify: `examples/policies/tool-scopes.json`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

**Interfaces:**
- Policy: `skills.<skill>.required_commands: string[]`

- [x] Document the policy, CLI/MCP ownership boundary, and Doctor behavior.
- [x] Add a generic example without personal data.
- [x] Bump the minor version to `1.6.0`.

### Task 3: Verification

**Files:**
- No production file changes.

- [ ] Run `python3 -m unittest discover -s tests -v`.
- [ ] Run `bash scripts/test-suite.sh` with permission to exercise real local
  sync paths.
- [ ] Run `agent-sync audit`.
- [ ] Verify missing and present command fixtures produce the expected Doctor
  findings.
