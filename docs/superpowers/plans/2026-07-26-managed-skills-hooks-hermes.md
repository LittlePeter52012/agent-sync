# Managed Skills, Hook Health, and Hermes Implementation Plan

> Execute test-first and keep public synchronizer code free of personal paths,
> credentials, and private Hub content.

**Goal:** Make Hub Skill ownership truthful, connect Hermes to shared Skills,
audit required lifecycle hooks, and promote Qwen transcription into the
private Hub.

**Architecture:** Agent Sync continues to distribute ordinary Skills as Hub
symlinks. Hermes consumes the already-synchronized `~/.agents/skills`
directory through its native `skills.external_dirs`. Product hook files remain
native and are checked against a private desired-state policy.

**Tech stack:** Bash, Python standard library, unittest, YAML-preserving narrow
text editing for Hermes config.

---

## Task 1: Define failing synchronization tests

**Files:**

- Modify: `tests/test_agent_doctor.py`
- Create: `tests/test_sync_hermes.py`

1. Add a Doctor test where `SKILL.md` exists in a real target directory but is
   not linked to the Hub.
2. Add hook-policy tests for missing event wiring, matching wiring, and an
   invalid policy.
3. Add Hermes tests for an empty list, preservation of existing external
   directories, and idempotency.
4. Run the focused tests and confirm they fail for the missing behavior.

## Task 2: Implement truthful Skill ownership and hook audit

**Files:**

- Modify: `scripts/agent-doctor.py`
- Modify: `scripts/verify-all.sh`

1. Count configured and Hub-managed Skills separately.
2. Report stale copies or wrong symlinks as attention findings.
3. Parse the private hook policy and product-native hook commands without
   emitting command contents or private paths.
4. Verify required event wiring and leading executable availability.
5. Run focused tests until green.

## Task 3: Add Hermes native shared-Skill integration

**Files:**

- Create: `scripts/sync_hermes.py`
- Modify: `bin/agent-sync`
- Modify: `scripts/verify-all.sh`
- Modify: `scripts/test-suite.sh`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

1. Add a narrow, preserving updater for `skills.external_dirs`.
2. Add `agent-sync hermes` and include it in full sync and fix workflows.
3. Add Hermes to Doctor as a Skills consumer with no shared-MCP claim.
4. Document the Skills-only boundary and bump the minor version.
5. Run all Agent Sync tests.

## Task 4: Promote and repair Qwen transcription

**Files:**

- Create: Hub `skills/qwen3-transcribe/`
- Modify: Hub `manifest.yaml`
- Modify: Hub `policies/tool-scopes.json`
- Modify: relevant Hub usage documentation

1. Establish the current wrapper's missing `--check` behavior as the failing
   baseline.
2. Add the Hub Skill and scripts with cross-Agent paths, canonical credential
   lookup, pinned-Python support, accurate model documentation, and `--check`.
3. Validate the Skill structure and run shell/Python syntax checks.
4. Replace stale Agent directories through `agent-sync all --force`.

## Task 5: Pin lifecycle hook interpreters and declare desired state

**Files:**

- Modify: `~/.gemini/settings.json`
- Modify: `~/.cursor/hooks.json`
- Modify: Hub `policies/tool-scopes.json`

1. Prefix MemPalace shell hooks with its installed Python interpreter.
2. Declare only the required MemPalace lifecycle hooks in Hub policy.
3. Preserve every CMUX and product-specific hook.
4. Run Doctor and hook scripts with safe synthetic payloads.

## Task 6: Verify and publish

1. Run `agent-sync test`.
2. Run `agent-sync all --force`.
3. Run `agent-sync verify --strict`.
4. Run Qwen `--check` and transcribe synthetic audio.
5. Review diffs and run both privacy audits.
6. Commit and push the public Agent Sync repository.
7. Commit and push the private Agent Hub repository.

## Task 7: Correct Gemini CLI configuration coverage

**Files:**

- Modify: `scripts/config_inventory.py`
- Modify: `scripts/agent-doctor.py`
- Modify: `scripts/sync-mcp.sh`
- Create: `scripts/prune-mcp-aliases.py`
- Modify: `scripts/list-sync.sh`
- Modify: `scripts/verify-all.sh`
- Modify: `scripts/test-suite.sh`

1. Add failing tests proving that `~/.gemini/settings.json` was not synchronized
   or inventoried.
2. Add Gemini CLI as a distinct consumer that uses `~/.agents/skills`.
3. Inventory all Gemini and Antigravity MCP roots independently.
4. Remove retired entries and case-only aliases while preserving hooks,
   credentials, and intentional Gemini-only servers.
5. Repeat the full verification before publishing.
