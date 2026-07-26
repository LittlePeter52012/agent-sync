# Managed Skills, Hook Health, and Hermes Integration

Date: 2026-07-26
Status: Approved (Option A)

## Problem

Agent Sync currently counts a Skill as synchronized when `SKILL.md` exists at
the target. A stale directory or a symlink to another Agent therefore looks
healthy even though the private Hub is not authoritative. CLI-backed Skills
can also be present while their local executable is missing. Lifecycle hooks
are product-local and have no shared desired-state audit. Hermes is not a
supported consumer, so it cannot discover Hub Skills consistently.

The observed failure is `qwen3-transcribe`: Codex reaches a Gemini-owned
symlink, several Agents contain older directories, other Agents have no copy,
and Hermes can choose an unrelated Whisper workflow.

## Ownership Model

| Capability | Source of truth | Agent Sync behavior |
|---|---|---|
| Shared Skill | Hub manifest + `skills/` | Link through the Hub and verify canonical ownership |
| Skill CLI/runtime | Local machine | Audit required commands; never copy credentials |
| Shared MCP | Hub shared MCP definition | Keep the existing additive merge |
| Lifecycle hook | Product-native config + Hub desired-state policy | Audit required event/command wiring and executable paths |
| Product UI hook/plugin | Product-native manager | Preserve and ignore unless explicitly listed in policy |
| Hermes shared Skills | `~/.agents/skills` | Register that directory through Hermes `skills.external_dirs` |

CMUX hooks remain product-local. Antigravity automatic MemPalace wake/save
hooks remain disabled. Hook policy is read-only: Agent Sync reports drift but
does not rewrite a product's full native hook configuration.

## Design

### Canonical Skill identity

For every ordinary Skill target, healthy means:

1. `<target>/<skill>/SKILL.md` exists; and
2. `<target>/<skill>` is a symlink whose resolved destination is the matching
   Hub Skill directory.

Doctor reports coverage and ownership separately. Structural verification
fails when a copy exists but is not Hub-managed. `agent-sync skills --force`
remains the explicit migration action because it creates a backup before
replacing a real directory.

### Hermes

Hermes already supports external Skill directories. Agent Sync will add
`~/.agents/skills` to `skills.external_dirs` without replacing existing
entries. This avoids duplicating Hub Skills under `~/.hermes/skills` and lets
Hermes use the same managed links as other Agents.

The integration is Skills-only in this release. Hermes MCP configuration
remains native until a secret-preserving YAML merge is designed and tested.
This keeps the transcription fix on the smallest surface: CLI + Skill.

### Hook health

The private Hub may declare required event/command fragments for Codex,
Gemini, and Cursor. Doctor reads only those products' native hook files,
checks the required command is wired under the required event, and verifies a
leading absolute executable when present. It does not run hooks and does not
inspect or modify unrelated CMUX entries.

### CLI-backed Skills

The existing `required_commands` policy is expanded in the private Hub to
cover the shared Skills that have a strict CLI dependency. Agent Sync continues
to audit only; package managers remain authoritative.

### Qwen transcription

`qwen3-transcribe` becomes a Hub Skill. The cross-Agent path is
`~/.agents/skills/qwen3-transcribe`. The wrapper gains a local, secret-free
`--check` command and accepts a pinned Python interpreter through
`QWEN3_TRANSCRIBE_PYTHON`. Credentials resolve from:

1. `DASHSCOPE_API_KEY`;
2. `~/.config/qwen3-transcribe/dashscope_key`;
3. legacy `~/.gemini/dashscope_key`.

The Skill remains online-only and explicitly states that local execution
uploads the media to DashScope. It never falls back to Whisper or another ASR.

### Gemini CLI configuration truth

Gemini CLI uses `~/.gemini/settings.json` for user MCP configuration and
discovers user Skills from `~/.gemini/skills` or the official
`~/.agents/skills` alias. Agent Sync therefore treats Gemini CLI as a separate
consumer from the existing Gemini global and Antigravity roots:

- shared MCP is merged into `~/.gemini/settings.json` without replacing hooks
  or unrelated settings;
- Hub Skills are counted through the already managed `~/.agents/skills`;
- retired MCP entries are pruned;
- case-only aliases of canonical shared servers are pruned to prevent duplicate
  tool exposure;
- intentional Gemini-only MCP servers remain explicitly allowlisted in the
  private tool-scope policy.

## Verification

- Unit tests reproduce and fix stale Skill ownership, Hermes config
  preservation/idempotency, and hook-policy drift.
- `agent-sync test` passes.
- `agent-sync all --force` converges every supported target.
- `agent-sync verify --strict` passes on the MacBook Air.
- Qwen `--check` passes and a synthetic, non-personal audio sample completes
  through the online API.
- Public Agent Sync and private Agent Hub pass their privacy audits before
  commit and push.
