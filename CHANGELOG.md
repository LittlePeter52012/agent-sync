# Changelog

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
