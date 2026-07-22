# Changelog

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
