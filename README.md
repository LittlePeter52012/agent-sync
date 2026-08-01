# agent-sync

**Cross-tool synchronizer for Agent Skills, MCP servers, and agent rules.**

This repository is the **tool only**. It does **not** contain anyone’s personal skills, MCP secrets, or private rules.

Your personal configuration belongs in a **separate private hub** (default `~/.config/agent-hub`).

Supported tools:

- Cursor
- Gemini CLI
- Gemini global configuration
- Antigravity App
- Antigravity CLI (`agy`)
- Antigravity IDE
- Claude Code
- Codex
- OpenCode
- VS Code GitHub Copilot
- Hermes (shared Skills through native `skills.external_dirs`)
- Multica (optional explicit control-plane adapter)

## Install

```bash
git clone https://github.com/LittlePeter52012/agent-sync.git ~/.local/share/agent-sync
ln -sf ~/.local/share/agent-sync/bin/agent-sync ~/.local/bin/agent-sync
```

## Quick start

If one Agent tool is already configured the way you want, ask Agent Sync to
rank the available sources, preview the recommended source, and then promote it:

```bash
agent-sync sources
agent-sync sync --from vscode --dry-run
agent-sync sync --from vscode
```

Supported sources are `vscode`, `cursor`, `gemini`, `gemini-global`,
`antigravity`, `antigravity-app`, `antigravity-cli`, `antigravity-ide`,
`opencode`, `codex`, and `claude`. `gemini` means the real Gemini CLI user
settings; the legacy compatibility name `antigravity` means
`gemini-global`. `sources` is read-only: healthy configurations rank before
broken ones, shared-MCP coverage ranks first, lower tool-only drift ranks
second, top-level tools rank before profile replicas, and modification time is
only a final tie-breaker. Agent Sync never promotes a source automatically.

`sync --from` shows added, changed, removed, and unchanged MCP names before
asking for confirmation. It creates a minimal private Hub automatically on first
use, then distributes Skills, MCP servers, and Rules to all supported tools.

If you prefer to build the private Hub directly:

```bash
agent-sync init
# edit ~/.config/agent-hub/ ...
agent-sync sync
```

## How synchronization works

```mermaid
flowchart TB
    SOURCE["1 · agent-sync sources<br/>Static health → Shared coverage → Low drift → Timestamp"]
    PROMOTE["2 · agent-sync sync --from TOOL<br/>Explicit preview → Confirm once"]
    HUB[("3 · Private Agent Hub<br/>Skills · MCP · Rules<br/>Single source of truth")]
    SYNC["4 · agent-sync sync<br/>Distribute and convert for every tool"]
    TARGETS["Claude · Codex / ChatGPT · Cursor · Hermes<br/>Gemini CLI · Gemini global · Antigravity App / CLI / IDE<br/>OpenCode · VS Code / Copilot"]
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
agent-sync sources                      # rank safe MCP source candidates
agent-sync sync --from vscode           # VS Code → Hub → every Agent
agent-sync fix                          # repair deterministic issues and verify
```

Use `--dry-run` to preview a source promotion without writing, or `--yes` for
intentional non-interactive use. `agent-sync all` remains an alias for the
original full Hub-to-tools workflow.

### Gemini and Antigravity surfaces

Agent Sync treats the installed Gemini/Antigravity surfaces as separate
configuration consumers:

| Surface | Skills | MCP |
|---------|--------|-----|
| Gemini CLI | `~/.agents/skills` (official alias) | `~/.gemini/settings.json` |
| Gemini global | `~/.gemini/config/skills` | `~/.gemini/config/mcp_config.json` |
| Antigravity App | `~/.gemini/antigravity/skills` | `~/.gemini/antigravity/mcp_config.json` |
| Antigravity CLI | `~/.gemini/antigravity-cli/skills` | `~/.gemini/antigravity-cli/mcp_config.json` |
| Antigravity IDE | `~/.gemini/antigravity-ide/skills` | `~/.gemini/antigravity-ide/mcp_config.json` |

Gemini CLI is the `gemini` executable. Agent Sync uses its real user MCP
configuration, `~/.gemini/settings.json`, while keeping hooks and unrelated
settings intact. Gemini CLI discovers Hub Skills through its official
`~/.agents/skills` alias, so Agent Sync does not create a second
`~/.gemini/skills` copy.

The standalone Antigravity CLI executable is `agy`. A local executable named
`antigravity` may only launch the desktop app and is not used as proof that the
CLI is installed. `agent-sync list`, `verify`, and `doctor` report all four
Antigravity surfaces plus Gemini CLI separately. The compatibility source name `--from antigravity`
continues to promote from the Gemini global MCP file.

Skills remain Hub-owned links. MCP synchronization merges shared servers into
each surface while preserving local product-only servers and local credential
values. It removes retired servers and case-only aliases of canonical shared
server names, preventing duplicate tool exposure such as `MinerU` plus
`mineru`. For App, CLI, and IDE, Agent Sync also creates missing per-server cache
directories. Shared rules still use the single global
`~/.gemini/GEMINI.md`; root-specific rule copies are intentionally not created.

### Product plugin ownership and duplicate prevention

Product plugins remain owned by their product manager. For Cursor, Agent Sync
uses the active plugin log plus the declared cache revision, or an explicit
local-plugin directory; a cache directory by itself is not treated as enabled.
When an active plugin exposes the same Skill, MCP name, or normalized MCP
transport as the Hub, Agent Sync counts it as `plugin-owned` and removes only
the duplicate Hub-managed link or entry. Plugin files and unrelated local
capabilities are never modified. If the plugin disappears, the next sync
restores the Hub capability automatically.

Target-specific `distribution.exclude_skills` and `exclude_mcp` policy entries
provide explicit exclusions. Conditional `plugins.<target>.prefer_marketplace`
entries apply only while that plugin is active, preventing a stale marketplace
cache from hiding a Hub capability.

## Two-layer model

| Layer | What | Where | Visibility |
|-------|------|--------|------------|
| **agent-sync** (this repo) | Sync CLI + merge logic | `~/.local/share/agent-sync` | Public |
| **Personal hub** | Your skills / MCP / rules | `~/.config/agent-hub` | **Private** (your choice) |

### Privacy boundary

- The **public agent-sync repository** contains only the synchronizer, generic
  examples, tests, and the generic workflow diagram above.
- The **private Agent Hub** contains personal Skills, shared MCP definitions,
  Rules, the Skill whitelist, and retired shared-server names.
- Local credentials and machine-specific paths stay in environment variables or
  local Agent configurations; Hub definitions should use `${ENV}` placeholders.
- Cross-Agent secret donor compatibility is controlled explicitly through
  `secrets.targets.<target>.allow_secret_donors`. Secret-bearing MCP
  configurations are written with mode `0600`.
- `agent-sync push` runs the privacy audit before staging or pushing Hub files.
  Normal `sync` never copies private Hub content into the public tool repository.

```text
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
agent-sync sync          # Hub → skills + Hermes registration + MCP + rules + verify
agent-sync sources       # rank local MCP configurations without changing them
agent-sync sync --from vscode --dry-run
agent-sync sync --from vscode # VS Code → Hub → all tools
agent-sync trace mcp NAME # show shared/retired/tool-only ownership and locations
agent-sync all           # full Hub sync + structural and managed runtime gates
agent-sync skills        # symlink whitelist skills
agent-sync mcp           # merge shared MCP (keeps tool-only servers)
agent-sync rules         # inject rules/*.md
agent-sync hermes        # register ~/.agents/skills in Hermes external_dirs
agent-sync deploy HOST   # read-only remote SSH preflight
agent-sync deploy HOST --apply
                         # push committed tool + Hub snapshots, then sync
agent-sync multica       # read-only check of allowlisted Multica state
agent-sync multica --apply # explicitly apply allowlisted Multica changes
agent-sync list          # coverage matrix
agent-sync verify        # structural verification
agent-sync verify --strict # structural verification + full strict runtime doctor
agent-sync verify --strict --scope managed
                         # structural verification + Hub-managed strict gate
agent-sync test          # Python tests + syntax checks + Bash evaluation suite
agent-sync status
agent-sync doctor        # local agent capabilities and sync-health report
agent-sync doctor --runtime # add bounded Cursor/OpenCode/Claude MCP probes
agent-sync doctor --runtime --strict --scope managed
                         # full report; block only Hub/policy-owned findings
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
model/provider names, Skill coverage and canonical Hub ownership, shared-MCP
coverage, tool-only MCP executable health, optional CLI-backed Skill
dependencies, required lifecycle-hook wiring, retired MCP residue, plugin/MCP
scope drift, exact managed-rule drift, disabled or wrong-transport shared MCPs,
and unsafe permissions on secret-bearing configurations.
`doctor --runtime` adds bounded OpenCode and Claude CLI probes plus a two-second
compatibility check for Cursor loopback HTTP MCP endpoints. The Cursor check
sends an unauthenticated `GET` only to `127.0.0.1`, `localhost`, or `::1`, never
uses configured headers, and reports a `404` because Cursor cannot open its
optional SSE stream in that state. Raw command output is discarded after status
parsing. A failed CLI probe is rechecked once before it becomes a finding, while
persistent failures remain visible. Reports never print MCP values, tokens,
private configuration paths, cookies, or account/subscription information. The
default bounds are 30 seconds for OpenCode and 90 seconds for Claude. Override
them with
`AGENT_SYNC_RUNTIME_TIMEOUT_OPENCODE` and
`AGENT_SYNC_RUNTIME_TIMEOUT_CLAUDE`, or set
`AGENT_SYNC_RUNTIME_TIMEOUT` for both.

Strict verification has two exit gates. The default `verify --strict` and
`doctor --strict` use the full gate: every configured local, plugin-owned, and
tool-only runtime finding can fail the command. `--scope managed` keeps the
same complete Doctor report but limits its nonzero exit gate to Hub distribution
and policy findings. `agent-sync all` first completes structural verification,
then prints one runtime Doctor report using that managed gate. Ordinary
`agent-sync sync` remains structural-only and does not run runtime probes.
Neither strict scope mutates accounts, credentials, or product plugins.

`fix` is intentionally narrow: it repairs missing sync coverage, removes retired
Hub-managed MCP names, and normalizes managed rule blocks. Tool-only MCP servers
remain untouched. For shared MCP names, Hub command, URL, transport, and normal
arguments are authoritative; existing local secrets and machine-specific
absolute paths are preserved.

`verify`, `list`, `trace`, and Doctor share one capability resolver. A shared
MCP is healthy only when its enabled URL or stdio structure matches the Hub, an
active plugin owns an exact match, or target policy explicitly excludes it.
The reported states are `hub-managed`, `plugin-owned`, `excluded`, `disabled`,
`drifted`, and `missing`.

### Optional Multica control-plane adapter

Multica is treated as a task control plane, not as another copy of every local
Agent configuration. Put an explicit allowlist at
`$AGENT_HUB_ROOT/multica/desired-state.json`, using
`examples/multica/desired-state.json` as the schema example.

```bash
agent-sync multica          # read-only; 0 = converged, 2 = drift, 1 = error
agent-sync multica --apply  # the only command that writes to Multica
```

The adapter can manage allowlisted workspace Skill content and supporting
files, complete Skill assignments for named Agents, and the name, leader,
description, and instructions of named Squads. Exact names must resolve
uniquely. Managed Skill sources and Squad instruction files must stay inside
the private Hub.

It never manages runtimes, models, credentials, MCP servers, product plugins,
Issues, comments, task history, or Git repositories. `agent-sync sync`, `all`,
`fix`, and `update --sync` never invoke the Multica adapter. A local Multica CLI
can still communicate with a remote or self-hosted Multica workspace; local
execution and remote data destination are separate security properties.

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

Do not choose a source by file modification time alone: a recently touched
configuration may still contain a retired or broken MCP. Use `agent-sync
sources`, inspect `agent-sync sync --from TOOL --dry-run`, and promote the
source explicitly.

### Optional tool-scope policy

The private Hub can audit native plugin ownership and intentional tool-only MCP
servers without installing, uninstalling, or removing anything. Put a policy at
`policies/tool-scopes.json`:

```json
{
  "skills": {
    "example-cli-skill": {
      "required_commands": ["example-cli"]
    }
  },
  "hooks": {
    "codex": {
      "required_events": {
        "SessionStart": ["example-lifecycle-hook"]
      }
    }
  },
  "plugins": {
    "opencode": {
      "required": ["example-opencode-plugin"]
    },
    "codex": {
      "forbidden": ["example-plugin@example-marketplace"]
    }
  },
  "mcp": {
    "codex": {
      "allowed_tool_only": ["native-loopback"],
      "required_tool_only": ["native-loopback"]
    },
    "cursor": {
      "allowed_tool_only": ["editor-native-server"]
    }
  }
}
```

`skills.<name>.required_commands` makes Doctor verify that a shared
CLI-backed Skill is usable, not merely linked. Skill names must also appear in
`manifest.yaml`. Bare commands are resolved through `PATH`; executable paths
are checked directly. The check is read-only: Agent Sync never installs or
updates the CLI. Prefer this pattern over adding a duplicate shared MCP when
the Agents can already invoke the local command through a Skill.

`hooks.<tool>.required_events` maps a native event name to command fragments
that must be present under that event. Doctor verifies Codex, Gemini, and
Cursor wiring and the leading absolute executable without running the hook.
Unlisted product-native hooks remain outside Agent Sync.

`allowed_tool_only` rejects accidental MCP drift while preserving the names
listed for that Agent. `required_tool_only` also reports when a native
integration disappears. Matching is case-insensitive. Supported keys are
`gemini`, `gemini-global`, `antigravity-app`, `antigravity-cli`,
`antigravity-ide`, `cursor`, `claude`, `opencode`, `codex`, and `vscode`.
The v1.8 key `antigravity` remains a compatibility alias for
`gemini-global`.

The ownership rule is:

| Capability | Source of truth | Agent Sync behavior |
|---|---|---|
| Shared Skill | Hub manifest and `skills/` | Symlink to every supported Agent |
| CLI used by a shared Skill | Local package manager or pinned binary; optional Hub health policy | Audit availability only |
| Shared MCP | Hub `mcp/shared-servers.json` | Merge into every supported Agent |
| Lifecycle hook | Native hook file plus optional Hub health policy | Audit required wiring only |
| Tool-only MCP | Its native Agent config | Preserve and audit only |
| Native plugin/extension | Product plugin manager | Never synchronize |

`doctor` only reports scope drift. Each product's native plugin manager remains
authoritative, and `agent-sync fix` never removes tool-only MCP servers.

A locally launched CLI or MCP can still call a cloud service. Record execution
location and data destination separately in the private Hub runbook instead of
assuming that every local process is offline.

### Auto-update (optional)

Skills use **symlinks** — editing a shared Skill from any linked tool updates
the Hub-backed file immediately.

For the **tool itself** and **hub git backup**:

```bash
agent-sync update          # pull tool updates (--ff-only, safe)
agent-sync update --sync   # pull + re-run skills/Hermes/MCP/rules
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
  mcp/retired-servers.json   # shared MCP names intentionally removed
  policies/tool-scopes.json  # optional CLI, hook, plugin, and tool-only MCP audit
  multica/desired-state.json # optional explicit Multica allowlist
  multica/squads/*.md         # optional Squad instructions
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

On another machine with working GitHub authentication, run `agent-sync pull`
followed by `agent-sync sync`.

If the remote machine should not store a private GitHub token, deploy from the
canonical machine instead:

```bash
agent-sync deploy HOST          # read-only SSH preflight
agent-sync deploy HOST --apply  # explicit push + remote sync
```

Remote deployment archives only committed `HEAD` content from the public tool
and private Hub repositories. It refuses dirty source repositories, keeps the
previous remote copies under
`~/.local/state/agent-sync/remote-backups/`, and leaves product credentials and
tool-native configuration outside those two directories untouched.

### Hermes shared Skills

Hermes natively supports external Skill directories. `agent-sync hermes`
adds `~/.agents/skills` to `skills.external_dirs` without replacing existing
entries, and full sync runs this step automatically when Hermes is configured.
Hermes-local Skills remain under `~/.hermes/skills`.

This integration is Skills-only. Hermes MCP configuration remains product
native until a secret-preserving YAML merge is available; Agent Sync does not
claim shared-MCP coverage for Hermes.

## Secrets

- Put **placeholders** like `${MINERU_API_TOKEN}` in `mcp/shared-servers.json`.
- On merge, agent-sync resolves values from the process environment or existing local Agent configs.
- Never commit real tokens to a **public** repository.
- Your **private** hub may contain secrets if you accept that risk; prefer placeholders + local donors.

## License

MIT
