# Changelog

## 1.14.0 — 2026-10-19

- Add a host-local policy, `~/.config/agent-sync/host-policy.json`, that
  disables a Hub capability for chosen targets on one machine only, for
  example when the plugin the Hub selects is not installed there and the Hub
  MCP fallback is unwanted. It lives outside the Hub and the tool, so it is
  never distributed and `agent-sync deploy` does not touch it.
- Such targets resolve to `disabled-on-host` with the stated reason; sync,
  coverage, verify and Doctor treat them as satisfied.
- The file is validated strictly; an invalid file fails `capability_resolver
  validate`, so `agent-sync sync` stops before changing anything, and
  `agent-sync verify` now checks the Hub and host policy explicitly.

## 1.13.0 — 2026-10-19

- Give Hermes its own Skill target, `$HERMES_HOME/hub-skills`, instead of the
  shared `~/.agents/skills` directory, so whatever other tools place there no
  longer reaches Hermes and name collisions with Hermes' own Skills can be
  avoided. Other tools keep using `~/.agents/skills` unchanged.
- Add an optional `targets.<target>.exclude` list to the Hub manifest that
  withholds allowlisted Skills from one target. The parser is strict, and an
  invalid section stops synchronization before anything changes.
- `agent-sync hermes` registers the new directory in the default config and in
  every profile, backs each config up under `.sync-backups/` before an atomic
  rewrite, and accepts `--dry-run`. It removes the old `~/.agents/skills`
  entry only when Hermes would lose nothing; otherwise it keeps the entry and
  lists what is in the way.
- Status, verify, Doctor and the evaluation suite count Hermes against its own
  target: the allowlist minus its exclusions.

## 1.12.3 — 2026-10-19

- Give `codex plugin list` 60 seconds instead of 10 seconds. A cold start
  refreshes remote marketplaces and can take over 30 seconds, which made Doctor
  report installed and enabled Codex plugins as uncertain or owner-missing.
- Treat enabled Codex remote-marketplace plugins as active with no local Skills
  or MCP, instead of marking all plugin state uncertain because they have no
  local path.
- Stop the privacy audit from matching words that end in `sk-` (for example
  `risk-based`) as API tokens, and make the test suite accept every MCP state
  that coverage already counts as satisfied.

## 1.12.2 — 2026-08-31

- Make remote deployment roll back only when structural synchronization fails;
  host-specific runtime Doctor findings remain visible but no longer discard a
  structurally valid deployment.

## 1.12.1 — 2026-08-31

- Register the shared Skill directory in every detected Hermes profile, not
  only the default profile, while preserving each profile's native Skills,
  memories, sessions, schedules, and unrelated configuration.
- Make Hermes profile drift visible in `--check` and cover the multi-profile
  behavior with regression tests.

## 1.12.0 — 2026-08-08

- Add semantic capability ownership so each Agent uses one provider bundle per
  capability, with explicit `auto`, `hub`, `plugin:*`, `target:*`, and
  `disabled` selections.
- Add `agent-sync ownership` for secret-free ownership explanations and make
  synchronization, coverage, verification, and Doctor consume the same
  decisions.
- Discover only policy-declared plugins through read-only adapters for Cursor,
  Codex, Claude, Gemini, OpenCode, AntiGravity App/CLI/IDE, and VS Code
  Profiles; uncertain evidence keeps the Hub fallback.
- Migrate marketplace deduplication from same-name heuristics to reviewed full
  Skill-and-MCP capability bundles, with automatic Hub restoration when an
  automatically selected plugin disappears.
- Preserve marketplace identity during plugin matching, reject ambiguous
  Profile and plugin metadata, require allowlisted target-owned providers, and
  fail invalid ownership policy before synchronization writes.
- Report selection source, competing providers, evidence category, execution
  location, and known data destination without exposing credentials or paths.

## 1.11.0 — 2026-08-01

- Add guarded MCP dependency maintenance: `deps check` reports package-registry
  status, `deps pin` makes an explicit, registry-validated exact-pin update,
  and `deps smoke` runs a bounded launcher probe.
- Separate the Copilot Skill policy key from the VS Code MCP policy key in
  coverage and Doctor output, without changing account or authentication state.
- Match plugin-provided MCP capabilities by normalized transport identity, so a
  same-name, different-transport server is reported as a collision instead of
  suppressing the Hub-managed server.
- Add managed verification scope so strict verification isolates broken shared
  MCP configuration from unrelated local drift.
- Bind cached dependency advisories to the current local Hub specification and
  refresh ordinary checks when otherwise-valid cache records no longer match.
- Fail managed and structural verification when the canonical shared MCP file
  is unreadable, invalid JSON, or has an invalid root or `mcpServers` shape.

## 1.10.1 — 2026-07-28

- Make the full evaluation suite count enabled local Cursor plugin MCP servers
  with the same ownership-aware coverage logic used by `verify` and `doctor`.

## 1.10.0 — 2026-07-28

- Treat enabled local Cursor plugin MCP servers as product-owned providers for
  shared capability coverage, removing only duplicate user-level registrations
  and restoring the Hub entry automatically when the plugin disappears.
- Extend `doctor --runtime` with a bounded, unauthenticated, loopback-only
  Cursor HTTP compatibility check that reports the `GET /mcp` 404 failure mode
  without exposing configured URLs, headers, or response bodies.

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
