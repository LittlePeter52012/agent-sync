# Exa Fallback and Plugin Scan Performance Design

## Goal

Add Exa as a Hub-declared fallback capability without duplicating product-native
Exa integrations, and remove repeated product-plugin discovery from one Skill
sync run.

## Scope

This change covers two related ownership operations:

1. resolve which product or Hub owns Exa for each Agent target;
2. resolve product-plugin ownership once per target during Skill sync and reuse
   that result for every Skill in the same run.

It does not copy product-plugin commands, prompts, Skills, credentials, or
account state into the Hub. It does not change unrelated MCP ownership.

## Current State and Root Cause

Codex/ChatGPT exposes Exa through its product plugin, and Cursor has an Exa
marketplace plugin declaring `https://mcp.exa.ai/mcp`. The Hub currently has no
Exa MCP definition.

`sync-skills.sh` correctly asks `capability_resolver.py` for suppressed Skills
before distribution. Its final verification loop, however, invokes the resolver
again for every Skill and every target. Cursor resolution recursively locates
the newest `Cursor Plugins.log`; on the current machine this traverses more than
two thousand log files. Repeating that work once per Skill makes the focused
plugin-ownership test take about twenty seconds and makes the complete test gate
look stalled under short-lived callers.

## Capability Ownership Design

The Hub remains the desired-state catalog, while the active product integration
remains the execution owner.

- Codex/ChatGPT uses its native Exa plugin. Hub Exa is excluded from Codex MCP
  distribution.
- Cursor uses its active marketplace Exa plugin. Hub Exa is suppressed by exact
  transport identity when the plugin is active; an explicit marketplace policy
  also documents the intended owner.
- MCP-capable targets without a native Exa integration receive the Hub fallback
  at `https://mcp.exa.ai/mcp`.
- Product credentials and authorization remain product-owned and are never
  committed to the Hub.
- Local configuration does not imply local data processing: the fallback is a
  remote HTTP MCP, so search queries and fetched URLs are sent to Exa.

The conditional Cursor behavior is important: if its Exa plugin is disabled or
removed, a later sync restores the Hub fallback rather than leaving the target
without Exa. Codex uses an explicit target exclusion because its app/plugin
capability is product-managed outside user MCP configuration.

## Search Routing Design

No new top-level Exa Skill is added. `academic-search-router` receives a concise
provider-selection rule:

- Exa: semantic web discovery and clean page extraction;
- Perplexity: multi-source synthesis after quota checks;
- Scholar, DOI/publisher pages, Zotero, and primary sources: exact academic
  metadata, citation chains, theorem claims, and novelty verification.

This keeps natural-language routing available without making Exa the default
for every search or duplicating Cursor's plugin commands and Skills.

## Performance Design

`sync-skills.sh` creates one suppressed-Skill snapshot per target at the start
of the run. Distribution and final verification both read those snapshots.
Each snapshot is deleted on normal exit and on interruption.

The resolver remains the single ownership authority. No persistent cache,
timestamp invalidation, background service, or new configuration format is
introduced. A new `agent-sync` invocation always observes current plugin state.

This changes discovery frequency from approximately
`targets × (1 + skills)` to `targets` per run while preserving the same resolved
ownership values.

## Failure Handling

- Failure to create or populate a target snapshot aborts the sync before
  distribution.
- Empty resolver output is valid and means no suppression for that target.
- Snapshot cleanup must not delete paths outside the temporary directory
  created by the current process.
- A same-name MCP with a different transport remains a collision; it is not
  silently treated as the Hub Exa fallback.
- Product plugin cache presence alone is not active-plugin evidence.

## Test Design

The implementation must be test-driven.

1. Add a focused Skill-sync test that wraps the resolver and asserts exactly one
   resolver invocation per target for a multi-Skill manifest. It must fail on
   the current repeated-verification behavior.
2. Preserve the existing test showing that Cursor removes a Hub Skill while an
   active plugin owns it and restores the Hub link when the plugin disappears.
3. Add capability-resolution tests showing:
   - an active Cursor Exa plugin suppresses the same Hub MCP;
   - an inactive/cache-only plugin does not suppress it;
   - Codex policy exclusion suppresses Hub Exa without requiring a duplicate
     user MCP entry;
   - an unrelated target receives the Hub Exa definition.
4. Add Hub policy/coverage assertions so `status`, `verify`, and Doctor report
   Exa as `plugin-owned`, `excluded`, or `hub-managed` according to target.
5. Run the focused tests, full `agent-sync test`, strict managed verification,
   dependency check, and privacy audit.

## Files and Ownership

Public `agent-sync` repository:

- modify `scripts/sync-skills.sh` only for per-run snapshot reuse;
- extend existing tests under `tests/` for invocation count and ownership
  behavior;
- update public documentation only where the effective-provider rule needs to
  be explained.

Private Agent Hub repository:

- add `exa` to `mcp/shared-servers.json` as a remote HTTP MCP;
- update `policies/tool-scopes.json` with Codex exclusion and Cursor marketplace
  preference;
- update `academic-search-router/SKILL.md` with the provider-selection rule;
- update the Hub changelog or README describing the data destination and
  fallback behavior.

No credential files or product plugin state are committed in either repository.

## Acceptance Criteria

- A Skill sync resolves plugin ownership once per target per invocation.
- Cursor's focused plugin-ownership test completes without repeated log-tree
  scans and retains restore-on-plugin-removal behavior.
- Codex/ChatGPT and Cursor expose one effective Exa provider, never two.
- Targets without native Exa receive the Hub fallback.
- Disabling the Cursor Exa plugin allows Hub Exa to return on the next sync.
- Search routing distinguishes discovery, synthesis, and authoritative academic
  verification.
- `agent-sync test`, strict managed verification, dependency checks, and privacy
  audit complete successfully.
- Both repositories contain no raw Exa credential or user account state.
