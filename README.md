# agent-sync

**Cross-tool synchronizer for Agent Skills, MCP servers, and agent rules.**

This repository is the **tool only**. It does **not** contain anyone’s personal skills, MCP secrets, or private rules.

Your personal configuration belongs in a **separate private hub** (default `~/.config/agent-hub`).

Supported tools:

- Cursor
- Antigravity (Gemini)
- Claude Code
- Codex
- OpenCode
- VS Code GitHub Copilot

## Install

```bash
git clone https://github.com/LittlePeter52012/agent-sync.git ~/.local/share/agent-sync
ln -sf ~/.local/share/agent-sync/bin/agent-sync ~/.local/bin/agent-sync
```

## Quick start

If one Agent tool is already configured the way you want, adopt it and sync
everything in one command:

```bash
agent-sync sync --from vscode
```

Supported sources are `vscode`, `cursor`, `antigravity`, `opencode`, `codex`,
and `claude`. The command shows added, changed, removed, and unchanged MCP names
before asking for confirmation. It creates a minimal private Hub automatically
on first use, then distributes Skills, MCP servers, and Rules to all supported
tools.

If you prefer to build the private Hub directly:

```bash
agent-sync init
# edit ~/.config/agent-hub/ ...
agent-sync sync
```

## How synchronization works

```mermaid
flowchart TB
    SOURCE["1 · Choose the Agent that already works<br/>VS Code · Cursor · Codex · OpenCode · Claude · Antigravity"]
    PROMOTE["2 · agent-sync sync --from TOOL<br/>Preview changes → Confirm once"]
    HUB[("3 · Private Agent Hub<br/>Skills · MCP · Rules<br/>Single source of truth")]
    SYNC["4 · agent-sync sync<br/>Distribute and convert for every tool"]
    TARGETS["Claude · Codex / ChatGPT · Cursor<br/>Gemini / Antigravity · OpenCode · VS Code / Copilot"]
    CHECK{"5 · agent-sync doctor<br/>Is everything healthy?"}
    READY["Ready to use<br/>The same shared capabilities everywhere"]
    FIX["agent-sync fix<br/>Repair → Re-sync → Verify"]
    GH[("Optional private GitHub backup<br/>agent-sync push / pull")]

    SOURCE --> PROMOTE --> HUB --> SYNC --> TARGETS --> CHECK
    CHECK -->|"Yes"| READY
    CHECK -->|"Issue found"| FIX
    FIX --> SYNC
    HUB -. "private backup" .-> GH

    classDef source fill:#eef2ff,stroke:#6366f1,color:#1e1b4b,stroke-width:1.5px;
    classDef action fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2px;
    classDef hub fill:#fff7ed,stroke:#f97316,color:#7c2d12,stroke-width:2.5px;
    classDef target fill:#f0fdf4,stroke:#22c55e,color:#14532d,stroke-width:1.5px;
    classDef check fill:#fdf4ff,stroke:#c026d3,color:#701a75,stroke-width:2px;

    class SOURCE source;
    class PROMOTE,SYNC,FIX action;
    class HUB,GH hub;
    class TARGETS,READY target;
    class CHECK check;
```

There are only three normal workflows:

```bash
agent-sync sync                         # Hub → every Agent
agent-sync sync --from vscode           # VS Code → Hub → every Agent
agent-sync fix                          # repair deterministic issues and verify
```

Use `--dry-run` to preview a source promotion without writing, or `--yes` for
intentional non-interactive use. `agent-sync all` remains an alias for the
original full Hub-to-tools workflow.

## Two-layer model

| Layer | What | Where | Visibility |
|-------|------|--------|------------|
| **agent-sync** (this repo) | Sync CLI + merge logic | `~/.local/share/agent-sync` | Public |
| **Personal hub** | Your skills / MCP / rules | `~/.config/agent-hub` | **Private** (your choice) |

```
~/.local/share/agent-sync/     ← tool (public)
~/.config/agent-hub/           ← YOUR configs (keep private)
    manifest.yaml
    skills/
    mcp/shared-servers.json
    rules/
```

## Commands

```bash
agent-sync init          # create hub from examples/
agent-sync sync          # Hub → skills + MCP + rules + verify
agent-sync sync --from vscode --dry-run
agent-sync sync --from vscode # VS Code → Hub → all tools
agent-sync all           # backward-compatible alias for full Hub sync
agent-sync skills        # symlink whitelist skills
agent-sync mcp           # merge shared MCP (keeps tool-only servers)
agent-sync rules         # inject rules/*.md
agent-sync list          # coverage matrix
agent-sync verify
agent-sync test
agent-sync status
agent-sync doctor        # local agent capabilities and sync-health report
agent-sync doctor --json # same report as safe, machine-readable JSON
agent-sync fix --dry-run # preview safe local repairs without writing
agent-sync fix           # sync missing coverage and normalize synced rules
agent-sync update        # pull latest agent-sync tool from GitHub
agent-sync update --sync # update tool + re-sync hub to all AI tools
agent-sync update --hub  # also pull personal hub
agent-sync pull          # pull personal hub only
agent-sync audit         # privacy audit (tokens, PII, repo visibility)
agent-sync push -m "msg" # commit/push the personal hub (if it is a git repo)
```

`doctor` is local and read-only. It reports installed agent surfaces, configured
model/provider names, skill and shared-MCP coverage, and duplicate synced rules.
It never prints MCP values, tokens, paths from private configuration, cookies,
or account/subscription information. `fix` is intentionally narrow: it repairs
missing sync coverage, removes retired Hub-managed MCP names, and normalizes
managed rule blocks. Tool-only MCP servers remain untouched. For shared MCP
names, Hub command, URL, transport, and normal arguments are authoritative;
existing local secrets and machine-specific absolute paths are preserved.

For VS Code, `agent-sync mcp` merges shared MCP servers into the default user
configuration and every existing VS Code Profile. Profile-specific MCP files
are separate in current VS Code releases, so `agent-sync doctor` reports each
Profile's coverage individually.

### Adopting a configuration from an Agent

`sync --from` promotes only MCP configuration. Shared Skills are symlinks to
the private Hub already, while Rules stay Hub-owned so tool-specific prompts do
not accidentally spread everywhere.

The selected source becomes authoritative for the shared MCP set. Missing old
shared names are recorded in `mcp/retired-servers.json` and safely removed from
other tools on the next `sync`; servers that were never Hub-managed remain
local. An empty, unreadable, or ambiguous source is rejected before any write.
VS Code automatically selects its only non-builtin MCP Profile; when several
Profiles qualify, choose one explicitly with `vscode:<profile-id>`.

### Auto-update (optional)

Skills use **symlinks** — editing a shared Skill from any linked tool updates
the Hub-backed file immediately.

For the **tool itself** and **hub git backup**:

```bash
agent-sync update          # pull tool updates (--ff-only, safe)
agent-sync update --sync   # pull + re-run skills/mcp/rules
```

Optional in `manifest.yaml` (opt-in):

```yaml
auto_update_check: true   # check once/day when you run agent-sync all
auto_update_apply: false  # set true to auto-pull (default off for safety)
```

Environment:

- `AGENT_HUB_ROOT` — personal hub (default `~/.config/agent-hub`)
- `AGENT_SYNC_HOME` — tool install path (auto-detected)

## Personal hub layout

```text
~/.config/agent-hub/
  manifest.yaml              # skills whitelist
  skills/<name>/SKILL.md
  mcp/shared-servers.json    # shared MCP (use ${ENV} placeholders)
  rules/*.md                 # injected into CLAUDE.md / AGENTS.md / GEMINI.md / Copilot
```

`manifest.yaml` example:

```yaml
skills:
  - my-skill
```

`mcp/shared-servers.json` can contain local stdio servers and remote HTTP
servers:

```json
{
  "mcpServers": {
    "miro-mcp": {
      "type": "http",
      "url": "https://mcp.miro.com"
    }
  }
}
```

For Claude Code, `agent-sync mcp` uses the official `claude mcp add` CLI with
user scope instead of editing Claude's private config files directly.

Normal `sync` does not silently pull or push GitHub. To back up your private Hub:

```bash
agent-sync push -m "promote preferred MCP configuration"
```

On another machine, run `agent-sync pull` followed by `agent-sync sync`.

## Secrets

- Put **placeholders** like `${MINERU_API_TOKEN}` in `mcp/shared-servers.json`.
- On merge, agent-sync resolves values from the process environment or existing local Agent configs.
- Never commit real tokens to a **public** repository.
- Your **private** hub may contain secrets if you accept that risk; prefer placeholders + local donors.

## License

MIT
