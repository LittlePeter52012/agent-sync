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

Create your private hub (once):

```bash
agent-sync init
# edit ~/.config/agent-hub/ ...
agent-sync all
agent-sync test
```

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
agent-sync all           # skills + mcp + rules + verify
agent-sync skills        # symlink whitelist skills
agent-sync mcp           # merge shared MCP (keeps tool-only servers)
agent-sync rules         # inject rules/*.md
agent-sync list          # coverage matrix
agent-sync verify
agent-sync test
agent-sync status
agent-sync update        # pull latest agent-sync tool from GitHub
agent-sync update --sync # update tool + re-sync hub to all AI tools
agent-sync update --hub  # also pull personal hub
agent-sync pull          # pull personal hub only
agent-sync audit         # privacy audit (tokens, PII, repo visibility)
agent-sync push -m "msg" # commit/push the personal hub (if it is a git repo)
```

### Auto-update (optional)

Skills use **symlinks** — editing files in your hub updates all tools instantly (no re-sync).

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

## Secrets

- Put **placeholders** like `${MINERU_API_TOKEN}` in `mcp/shared-servers.json`.
- On merge, agent-sync fills values from **existing local** Cursor/Antigravity MCP configs.
- Never commit real tokens to a **public** repository.
- Your **private** hub may contain secrets if you accept that risk; prefer placeholders + local donors.

## License

MIT
