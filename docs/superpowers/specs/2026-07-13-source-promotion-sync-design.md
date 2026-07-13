# Source Promotion Sync Design

## Goal

Keep agent-sync as a small personal tool with three clear operations:

- `agent-sync sync`: use the local private Hub as the source of truth and
  distribute it to every supported Agent tool.
- `agent-sync sync --from <tool>`: promote one Agent tool's MCP configuration
  into the Hub, then distribute the promoted configuration to every tool.
- `agent-sync fix`: repair deterministic coverage and managed-rule problems,
  then print the health report.

The same commands must cover both an existing installation and first-time
onboarding. A user who already has one well-configured Agent can use that Agent
as the initial template without manually constructing the Hub first.

Reverse promotion applies only to MCP. Shared Skills are already Hub symlinks,
and Rules remain Hub-owned to avoid promoting tool-specific instructions.

## Chosen approach

The source tool is always explicit. The synchronizer must not infer authority
from modification time, file order, version number, or whichever Agent ran
most recently. `sync --from vscode` means that VS Code is authoritative for
this one MCP promotion; a later `sync --from opencode` explicitly transfers
authority to OpenCode for that operation.

Before writing, the command prints a terminal plan containing the selected
source plus added, changed, removed, and unchanged MCP server names. Interactive
execution requires one confirmation. `--dry-run` prints the same plan and makes
no changes; `--yes` is available for intentional non-interactive use.

## Commands

```text
agent-sync sync
agent-sync sync --from vscode [--dry-run|--yes]
agent-sync sync --from vscode:<profile-id> [--dry-run|--yes]
agent-sync sync --from cursor|antigravity|opencode|codex|claude
agent-sync fix [--dry-run]
```

`all` remains as a backward-compatible alias for `sync`. Existing granular
commands (`skills`, `mcp`, and `rules`) remain available.

When `sync --from <tool>` is used without an existing Hub, agent-sync creates
the minimal private Hub structure first, promotes the source MCP configuration,
and then performs the normal full synchronization. Plain `sync` still requires
an existing Hub because it has no safe source from which to bootstrap one.

For VS Code, an unqualified source selects the only non-builtin Profile with an
MCP configuration, falling back to the default user MCP file when no such
Profile exists. If several non-builtin Profiles qualify, the command stops and
lists safe profile identifiers; the user must choose `vscode:<profile-id>`.

## Promotion semantics

The selected source's MCP server set becomes the desired shared set in
`mcp/shared-servers.json`:

- source-only names are added;
- names in both locations are updated from the source;
- Hub-only names are removed;
- MCP servers that were never managed by the Hub remain tool-local in other
  Agent configurations.

An empty or unreadable source is rejected so a missing file cannot erase the
Hub. Before changing the Hub, agent-sync writes a timestamped local backup.

The source is converted from its native format into the existing canonical
`mcpServers` format. Existing Hub placeholders are retained for corresponding
sensitive fields. New concrete environment values and header credentials are
replaced by deterministic environment placeholders. Secret values are never
printed, written into the public agent-sync repository, or included in the
terminal plan. During local distribution, concrete values may be resolved from
the selected source, another existing local Agent configuration, or the process
environment; they are not copied into the canonical Hub file. Any value that
cannot be resolved is reported as a safe finding instead of being guessed.

After confirmation, the operation is transactional at the Hub boundary:

1. validate and normalize the selected source;
2. back up the current canonical MCP file;
3. atomically replace the canonical MCP file;
4. run the existing Skills, MCP, and Rules synchronizers;
5. run verification and print `doctor`.

If validation or Hub replacement fails, no target is changed. If a downstream
target fails, the new Hub remains authoritative, the failure is reported, and
`agent-sync fix` can retry distribution.

Verification covers all three managed capabilities. It confirms that every
supported target has the Hub's Skill whitelist, shared MCP names in the
target's native format, and the managed Rule blocks. It also reports unresolved
placeholders and missing local MCP executables without attempting network login
or starting authenticated remote servers. Runtime authentication and an
application's first-use trust prompt remain explicit user actions.

## GitHub behavior

Normal synchronization does not silently pull from or push to GitHub. This
keeps local edits deterministic and prevents unrelated dirty Hub files from
being committed. The complete two-command remote workflow is:

```text
agent-sync sync --from vscode
agent-sync push -m "promote VS Code MCP configuration"
```

On another machine, use `agent-sync pull` followed by `agent-sync sync`.
`agent-sync push` and the privacy audit remain responsible for remote backup
and secret checks.

## Implementation boundaries

Add one dependency-free Python promotion script responsible for source
discovery, format conversion, redacted planning, backup, and atomic Hub writes.
The shell entrypoint owns command parsing and invokes the existing downstream
synchronizers. Do not add a database, background watcher, modification-time
heuristics, per-Agent overlay system, or automatic GitHub commit.

## Public documentation

The public README provides two short paths after installation:

```text
# Start from an existing private Hub
agent-sync sync

# First use: adopt the Agent that already works
agent-sync sync --from vscode
```

It documents supported source names, VS Code Profile selection, dry-run and
confirmation behavior, `fix`, `doctor`, private Hub GitHub backup, and the fact
that authentication prompts are not synchronized. Examples use generic names,
environment placeholders, and paths only. No personal repository URL, account
name, secret, private Hub content, or machine-specific path is committed to the
public repository.

## Verification

Fixture-based tests must prove:

- a VS Code change updates the Hub and is then available to other targets;
- dry-run is byte-for-byte read-only;
- multiple VS Code Profiles require an explicit safe identifier;
- unreadable and empty sources do not change the Hub;
- additions, updates, and removals are reported without secret values;
- existing placeholders survive promotion and new sensitive values become
  placeholders;
- `sync` remains equivalent to the previous `all` workflow;
- first-use `sync --from` creates the minimal Hub and completes distribution;
- Skills, MCP names, and managed Rules reach every supported target;
- unresolved placeholders and missing MCP executables are reported safely;
- the public privacy audit and the complete existing evaluation suite pass.

The explanatory flowchart is a separate private/local artifact and is not
committed to the public agent-sync repository.
