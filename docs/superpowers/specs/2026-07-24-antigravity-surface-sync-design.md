# Antigravity Surface Sync Design

## Goal

Make `agent-sync` treat the installed Antigravity surfaces as distinct local
configuration targets instead of reporting one aggregated
“Gemini / Antigravity” target.

The synchronized state must cover:

- the Antigravity desktop app;
- the standalone `agy` CLI;
- Antigravity IDE;
- the existing Gemini global configuration.

Shared Hub Skills and MCP servers must reach every target. Product-specific
Skills, MCP servers, plugins, conversations, caches, and credentials must
remain owned by their product.

## Chosen Approach

Extend the existing explicit target lists. Do not introduce a new target
registry or make the products share an entire configuration directory.

This is the smallest change compatible with the repository’s current shell and
Python architecture. It also preserves the current merge behavior: only Hub
whitelist entries are synchronized, while unrelated local entries survive.

## Target Map

| Surface | Skills | MCP configuration |
|---|---|---|
| Gemini global | `~/.gemini/config/skills` | `~/.gemini/config/mcp_config.json` |
| Antigravity App | `~/.gemini/antigravity/skills` | `~/.gemini/antigravity/mcp_config.json` |
| Antigravity CLI | `~/.gemini/antigravity-cli/skills` | `~/.gemini/antigravity-cli/mcp_config.json` |
| Antigravity IDE | `~/.gemini/antigravity-ide/skills` | `~/.gemini/antigravity-ide/mcp_config.json` |

The existing global `~/.gemini/GEMINI.md` remains the single synchronized rule
target for the Gemini/Antigravity family. Root-specific copies are deliberately
not created because the same managed rule could otherwise be loaded more than
once.

## Synchronization Behavior

### Skills

`sync-skills.sh` adds the CLI and IDE Skills directories to the same whitelist
symlink flow already used by the App and other Agents.

- Existing Hub-owned symlinks are refreshed.
- Existing local directories are preserved unless the user passes `--force`.
- Non-whitelisted local Skills are not removed.

### MCP

`sync-mcp.sh` merges the canonical shared MCP map into all four
Gemini/Antigravity MCP files.

- Existing product-only servers remain present.
- Retired shared servers are pruned through the existing retired-server
  policy.
- Secret values are never printed.
- For the three Antigravity runtime roots, the synchronizer creates missing
  per-server cache directories under `mcp/` after a successful merge. It never
  deletes cache contents.

### Reporting

`status`, `list`, `verify`, and `doctor` report Gemini global, Antigravity App,
Antigravity CLI, and Antigravity IDE separately.

Doctor detects the executable or application appropriate to each surface:

- Gemini global: `gemini`;
- Antigravity App: the installed application bundle;
- Antigravity CLI: `agy`;
- Antigravity IDE: the installed IDE bundle.

The existing `--from antigravity` promotion source remains mapped to the Gemini
global configuration for backward compatibility. The new surfaces are
synchronization targets, not additional promotion sources.

## Failure Handling

- Missing target directories are created during synchronization.
- Invalid MCP JSON remains a hard failure through the existing merge tools.
- Structural verification fails independently for any surface missing a shared
  Skill or MCP server.
- Doctor findings name only the surface, Skill, MCP server, command, or
  placeholder; they do not include secret values.
- MCP cache preparation fails the synchronization command if a required
  directory cannot be created.

## Tests and Acceptance Criteria

Automated tests must prove:

1. Skills synchronization reaches App, CLI, and IDE roots.
2. MCP synchronization reaches all four Gemini/Antigravity MCP files.
3. Product-only MCP servers survive a shared merge.
4. Cache directories are created for configured Antigravity MCP servers.
5. Verification fails when one Antigravity surface is missing a shared entry.
6. Status and Doctor expose separate rows rather than one aggregated row.
7. Existing Agents and source-promotion behavior remain green.

Local acceptance requires:

- 15/15 Hub Skills on App, CLI, and IDE;
- 7/7 shared MCP servers on App, CLI, and IDE;
- `agent-sync verify --strict` succeeds;
- `agent-sync doctor --json` reports no synchronization findings;
- the full test suite and privacy audit pass.

## Out of Scope

- Sharing conversation databases, knowledge stores, or browser profiles across
  Antigravity products.
- Removing or promoting product-only MCP servers.
- Rotating credentials or rewriting Antigravity IDE application settings.
- Making Codex and Antigravity native plugins, browser controls, or UI features
  identical.
