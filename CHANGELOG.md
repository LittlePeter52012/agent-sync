# Changelog

## 1.9.3 — 2026-07-26

- Recheck a failed OpenCode or Claude runtime MCP probe once before reporting
  attention, filtering transient startup failures without hiding persistent
  connection errors.

## 1.9.2 — 2026-07-26

- Run the post-deployment Doctor through the remote login shell so NVM- and
  profile-managed CLIs are not falsely reported as missing.

## 1.9.1 — 2026-07-26

- Add `agent-sync deploy HOST [--apply]` for credential-free, push-based
  deployment of committed tool and private Hub snapshots over SSH.
- Keep remote deployment read-only by default, refuse dirty local repositories,
  preserve the previous remote snapshots, and roll back when structural sync
  fails.

## 1.9.0 — 2026-07-26

- Treat a shared Skill as healthy only when the target is a symlink that
  resolves to the matching private Hub Skill, eliminating false 100% coverage
  from stale directories and cross-Agent links.
- Register `~/.agents/skills` through Hermes' native
  `skills.external_dirs`, preserving existing external directories and keeping
  Hermes-local Skills separate.
- Add read-only lifecycle-hook desired-state checks for Codex, Gemini, and
  Cursor without rewriting native hook files or product-specific hooks.
- Report configured and Hub-managed Skill coverage separately in Doctor.
- Synchronize the actual Gemini CLI user configuration at
  `~/.gemini/settings.json` and report it separately from Gemini global and
  Antigravity product roots.
- Inventory every Gemini/Antigravity MCP surface, remove retired MCP entries
  from Gemini CLI, and prune case-only aliases of canonical shared servers.

## 1.8.0 — 2026-07-24

- Synchronize Hub Skills and shared MCP servers independently to Gemini global,
  Antigravity App, the standalone `agy` CLI, and Antigravity IDE.
- Preserve product-only MCP servers while preparing missing per-server cache
  directories for the three Antigravity product roots.
- Split `list`, `verify`, and `doctor` reporting so drift in one Antigravity
  surface is visible without being masked by another.
- Keep `--from antigravity` mapped to the Gemini global configuration and keep
  shared rules in the single global `~/.gemini/GEMINI.md`.

## 1.7.0 — 2026-07-23

- Add an optional `agent-sync multica` adapter that checks a private Hub
  allowlist without writing by default and returns exit code 2 for drift.
- Require explicit `agent-sync multica --apply` before publishing allowlisted
  workspace Skills, replacing named Agent Skill assignments, or updating named
  Squad policy fields.
- Keep Multica runtimes, credentials, MCP servers, plugins, Issues, comments,
  task history, and Git repositories outside the adapter boundary.
- Keep `sync`, `all`, `fix`, and `update --sync` local-only with respect to
  Multica.

## 1.6.0 — 2026-07-22

- Add optional `skills.<name>.required_commands` checks in
  `policies/tool-scopes.json` so Doctor can distinguish a synchronized Skill
  from a usable CLI-backed Skill.
- Report missing executables, malformed command lists, and policies that name
  Skills outside the Hub manifest without executing third-party commands.
- Document CLI-backed Skills as a local dependency boundary rather than a
  reason to duplicate the same capability as shared MCP.

## 1.5.2 — 2026-07-19

- Resolve Claude MCP environment placeholders from the concrete value already
  stored under the target environment-variable name when the portable Hub
  placeholder uses a different name.
- This lets shared definitions such as `OUTPUT_DIR=${SERVICE_OUTPUT_DIR}`
  converge without service-specific mapping code.

## 1.5.1 — 2026-07-19

- Replace case-variant Claude MCP entries using the name actually registered
  with the Claude CLI, so a legacy `MinerU` entry can converge to canonical
  `mineru`.
- Give OpenCode and Claude runtime probes independent bounded defaults to avoid
  false timeouts from Claude's longer MCP health-check cycle.
- Support `AGENT_SYNC_RUNTIME_TIMEOUT_OPENCODE` and
  `AGENT_SYNC_RUNTIME_TIMEOUT_CLAUDE` overrides in addition to the shared
  `AGENT_SYNC_RUNTIME_TIMEOUT`.

## 1.5.0 — 2026-07-19

- Add optional per-Agent allowlists and required lists for intentional
  tool-only MCP servers in `policies/tool-scopes.json`.
- Extend Doctor with secret-free findings for unexpected, missing-required, and
  invalidly scoped tool-only MCP definitions.
- Document the ownership boundary between shared Skills, shared MCP servers,
  tool-only MCP servers, and native plugins/extensions.
- Keep the new audit read-only: `agent-sync fix` never removes native
  tool-owned MCP configuration.

## 1.4.0 — 2026-07-19

- Add `agent-sync sources` to rank MCP promotion candidates by static health,
  shared coverage, low tool-only drift, top-level source preference, and
  modification time as a final tie-breaker.
- Add secret-free `agent-sync trace mcp NAME`.
- Extend Doctor to audit tool-only and retired MCP entries, optional runtime
  connectivity, and required/forbidden plugin scope.
- Add `agent-sync verify --strict` for structural plus runtime verification.
- Preserve local absolute executable paths only while the canonical command
  identity is unchanged, allowing safe migrations such as `npx` to a dedicated
  local executable.
- Exclude disabled MCP entries when promoting from JSON or Codex sources.
- Keep promotion explicit: no configuration is promoted solely because it is
  the most recently modified.
