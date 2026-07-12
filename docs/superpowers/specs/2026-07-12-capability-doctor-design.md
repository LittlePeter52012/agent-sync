# Agent Sync Doctor Design

## Goal

Add one terminal-first command, `agent-sync doctor`, that gives a concise,
safe health report for installed agent tools, their available local
capabilities, and the health and coverage of agent-sync configuration.

## Scope

The report is local and read-only. It detects installed CLIs, desktop apps,
configuration roots, configured models/providers by name, agent-sync skills,
MCP entries, rules, and product-specific extension/plugin surfaces. It does
not access account pages, network services, cookies, API keys, tokens, or
conversation data. It must never print configuration values that can contain a
secret.

The report covers Codex/ChatGPT, Claude, Cursor, Gemini/Antigravity, OpenCode,
and Copilot/VS Code, plus any additional known agent configuration roots that
are found locally. It distinguishes an unsupported tool from an installed tool
that agent-sync does not yet manage.

## Commands

`agent-sync doctor` prints a color-aware, column-aligned terminal report.
It ends with findings and an overall status of `HEALTHY`, `ATTENTION`, or
`ERROR`.

`agent-sync doctor --json` emits the same observations as structured JSON,
with no ANSI escapes and no secret values.

`agent-sync fix --dry-run` prints only the repairs that would be made.
`agent-sync fix` applies the safe, deterministic repairs described below and
then runs `agent-sync doctor` to show the resulting state. It does not repair
external authentication or failed remote MCP connections.

## Capability Model

Each agent record contains independent observations rather than a subjective
subscription score:

- CLI availability and executable path
- Desktop application availability where known
- Configuration directory/file availability
- Configured model/provider names when present in public configuration fields
- Synced-skill count against the hub manifest
- Shared-MCP count against the canonical shared MCP file
- Rules status where that product has a global rule target
- Product capability labels inferred from local configuration, for example
  `plugins`, `extensions`, `MCP`, `browser`, and `computer-use`

The report may run local MCP health checks only through an already-installed
CLI command. Such checks are bounded by a timeout and classified separately
from configuration coverage so that an unauthenticated remote MCP does not
make configuration look absent.

## Findings And Repairs

Doctor reports these actionable finding types:

- duplicated managed rule blocks;
- missing shared skills, MCP entries, or rule files;
- a shared MCP present with a locally overridden configuration;
- unavailable or authentication-required MCPs when a local health check is
  available;
- installed Agent tools outside the currently supported sync targets.

`fix` only performs local, idempotent configuration repairs:

- deduplicate agent-sync managed rule blocks and restore exactly one canonical
  block per hub rule;
- run the existing skills, MCP, and rule synchronizers for missing coverage;
- update the legacy status/verify reporting so Claude MCP coverage is shown.

It never changes an existing locally overridden MCP server, removes arbitrary
MCP entries, installs products, signs in, changes a chosen model, or writes a
subscription setting. MCP configuration drift remains visible for a deliberate
user decision.

## Implementation Boundaries

Add a focused Python reporting module under `scripts/`, invoked by the shell
entrypoint. Reuse the current hub manifest and canonical MCP JSON; do not
introduce a database or a new dependency. Add fixture-based tests for
terminal/JSON output, rule deduplication, drift detection, and no-secret
output. Existing `test-suite.sh` remains the regression test for current sync
behavior.

## Success Criteria

On this Mac, `agent-sync doctor` identifies the six supported agent families,
shows their current skills/MCP/rules coverage, flags duplicate global rules,
shows Claude MCP coverage, and reports known MCP health warnings without
printing secret values. `agent-sync fix --dry-run` makes no writes, and
`agent-sync fix` reduces only the safe local findings before rerunning doctor.
