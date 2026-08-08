# Plugin Marketplace Deduplication and Target Exclusions Design

> 已由 2026-08-08 的《单一能力提供方设计》取代。本文仅保留为历史设计记录；
> 新实现以 `capability_ownership` 的“目标 Agent × 能力”所有权模型为准。

## Goal

Prevent `agent-sync` from installing a second copy of a Skill or MCP capability
into an Agent when that Agent already receives the same capability from its own
enabled plugin marketplace.

The solution must be generic rather than Cloudflare-specific. It must also keep
the Hub as the fallback source: when a product plugin is disabled or removed,
the next sync restores the Hub-managed capability automatically.

## Problem Statement

`agent-sync` currently has one narrow Cursor exception:

- it reads MCP declarations from `~/.cursor/plugins/local/*/mcp.json`;
- it prunes a Cursor user MCP only when its name exactly matches a Hub MCP;
- verification counts that local-plugin MCP as covered.

This leaves four gaps:

1. Cursor marketplace plugins live under a separate cache and are not detected;
2. the cache contains stale or disabled plugins, so cache presence alone is not
   evidence that a plugin is active;
3. Skills are linked to every target unconditionally, so a marketplace Skill
   and a Hub Skill can coexist under the same product;
4. diagnostics cannot explain whether a capability is Hub-owned, plugin-owned,
   intentionally excluded, stale, or accidentally duplicated.

Cloudflare exposes all four gaps. Cursor's Cloudflare marketplace plugin
provides a `cloudflare` Skill plus several focused MCP servers, while the Hub
provides a `cloudflare` Skill plus the broader `cloudflare-local` MCP. The two
Skills are duplicates. The MCPs are related but are not exact substitutes.

## Design Principles

1. **Product plugins own product-plugin capabilities.** `agent-sync` must not
   copy an exact duplicate into the same Agent.
2. **The Hub remains canonical for cross-Agent distribution.** A capability is
   suppressed only for the target that already has it.
3. **Active state and cached state are different.** A cached plugin is never
   treated as enabled without current product evidence.
4. **Exact matches can be automatic; semantic matches require policy.**
5. **Removal is reversible.** No plugin files or product-managed settings are
   modified.
6. **Diagnostics explain ownership.** A passing check must say how coverage was
   achieved instead of silently adjusting counts.

## Scope

### Phase 1

- Add a reusable product-plugin capability model.
- Implement marketplace adapters for Cursor and Codex.
- Apply plugin precedence to both Skills and MCPs.
- Add per-target conditional and unconditional exclusions.
- Update sync, verify, status, trace, and Doctor behavior.
- Add deterministic tests for detection, suppression, restoration, and stale
  cache handling.

### Future adapters

The model must allow adapters for VS Code, Claude, Gemini, Antigravity, or other
products when their plugin managers expose reliable enabled-state evidence.
Phase 1 does not guess those formats or scan arbitrary caches.

### Non-goals

- Installing, enabling, disabling, or deleting marketplace plugins.
- Writing to Cursor's state database, logs, cache, or generated runtime files.
- Treating all tools from the same vendor as interchangeable.
- Comparing natural-language descriptions to infer semantic equivalence.
- Hiding genuine duplicate registrations that were not authorized by policy.

## Capability Model

Each active plugin is represented internally as:

```text
PluginCapabilitySource
  target             product target, for example "cursor"
  plugin_id          stable product plugin name, for example "cloudflare"
  active_evidence    source and timestamp of enabled-state evidence
  skills             normalized Skill names
  mcp_servers        normalized MCP name plus transport identity
  manifest_path      read-only path used to obtain declarations
```

MCP transport identity is normalized as follows:

- HTTP/Streamable HTTP/SSE: lowercase scheme and host, remove a trailing slash,
  preserve path and non-secret query parameters;
- stdio: normalized command plus ordered arguments;
- secrets, environment variable values, headers, and OAuth data are never used
  as identity material or printed.

Two MCPs are automatic duplicates when either:

- their normalized names are equal; or
- their normalized transport identities are equal.

Two Skills are automatic duplicates when their normalized directory/manifest
names are equal.

## Cursor Active-Plugin Detection

Cursor needs a layered, read-only adapter because no supported CLI currently
lists marketplace plugins.

### Trusted active evidence

The adapter may accept either of these current-runtime signals:

1. the newest Cursor plugin log contains an enabled-plugin record for the
   plugin and matches the cached plugin revision; or
2. Cursor has materialized a current project runtime directory named
   `plugin-<plugin>-<server>` for at least one declared server.

When both signals exist, they are combined. When they conflict, the newest
enabled-plugin log is authoritative and Doctor reports the conflict.

### Untrusted inventory evidence

Directories under `~/.cursor/plugins/cache/` are inventory only. The adapter
uses a cached manifest only after trusted evidence identifies the plugin as
active. A cache-only plugin remains inactive.

### Local plugins

Existing local plugin support under `~/.cursor/plugins/local/` remains active
evidence because that directory is the explicit local-plugin installation
location. Its MCP declaration may be `mcp.json` or `.mcp.json`.

### Failure behavior

If active state cannot be established reliably:

- sync keeps the Hub capability;
- verification does not count the plugin as coverage;
- Doctor reports an informational or attention finding with the evidence that
  was missing;
- no destructive cleanup occurs.

This fail-open behavior prefers a visible duplicate over silently removing the
only working capability.

## Codex Active-Plugin Detection

Codex exposes authoritative local plugin state through:

```bash
codex plugin list --json
```

The Codex adapter uses only entries for which both `installed` and `enabled`
are true. It records the full `pluginId`, marketplace name, version, install
policy, and source class, then reads the declared Skill/MCP capabilities from
the reported plugin source.

The adapter does not infer enabled state from `~/.codex/plugins/cache/`. A
cached plugin absent from the CLI's installed list is inactive.

If the Codex CLI is missing, returns malformed JSON, or reports a source that
cannot be inspected:

- sync keeps the Hub capability;
- verification does not count the plugin as coverage;
- Doctor reports `plugin-state-uncertain`;
- no Codex plugin or local MCP configuration is modified.

### Codex local privacy boundary

The ownership report must distinguish these surfaces:

- **Codex marketplace plugin** — installed and enabled through Codex's plugin
  manager; its lifecycle may follow the Codex product/account policy;
- **Codex local MCP** — defined in the local
  `~/.codex/config.toml`;
- **Hub Skill** — stored in the local personal Hub and linked into the local
  Codex Skill directory;
- **MCP OAuth credentials** — runtime authentication material managed locally
  by Codex or the operating-system credential store and never copied into the
  Hub.

For the present Cloudflare setup:

- `cloudflare@openai-curated-remote` must remain absent from the installed Codex
  plugin list;
- `cloudflare-local` must remain enabled in the local Codex MCP list;
- its local config stores only `https://mcp.cloudflare.com/mcp`, not a raw
  token;
- the `cloudflare` Skill remains Hub-managed and local;
- Doctor treats reinstallation of
  `cloudflare@openai-curated-remote` as a policy violation so shared-account
  exposure is visible immediately.

## Policy Schema

`policies/tool-scopes.json` gains two optional sections.

### Conditional plugin precedence

```json
{
  "plugins": {
    "cursor": {
      "prefer_marketplace": {
        "cloudflare": {
          "skills": [
            "cloudflare"
          ],
          "mcp": []
        }
      }
    }
  }
}
```

The listed Hub capabilities are suppressed only while the named marketplace
plugin is detected as active. This handles semantic aliases that cannot be
proved by exact name or transport matching.

Automatic exact duplicate detection does not require a policy entry.

### Unconditional target exclusions

```json
{
  "distribution": {
    "cursor": {
      "exclude_skills": [],
      "exclude_mcp": []
    }
  }
}
```

These entries are always excluded from the selected target. They are intended
for deliberate product-specific routing decisions, not plugin detection.

Unknown targets, plugin IDs, Skills, or MCP names are reported by verification.
An exclusion never applies to another Agent.

## Cloudflare Initial Policy

For Cursor, the initial policy conditionally suppresses only the Hub
`cloudflare` Skill while the Cursor Cloudflare plugin is active.

It does **not** suppress `cloudflare-local` automatically:

- the Hub server is the full Cloudflare API MCP at
  `https://mcp.cloudflare.com/mcp`;
- the Cursor plugin currently declares focused documentation, bindings, builds,
  and observability MCP servers;
- their names and normalized endpoints are different.

This prevents duplicate documentation/Skill guidance without removing the
broader domain and account management surface. If the user later prefers a
plugin-only Cursor setup, `cloudflare-local` can be added to the conditional
`mcp` list without changing code.

For Codex, the initial policy adds
`cloudflare@openai-curated-remote` to the existing Codex forbidden-plugin list.
The local `cloudflare-local` MCP and Hub-managed `cloudflare` Skill remain
enabled. `agent-sync` does not install or authenticate a Codex marketplace
plugin as part of synchronization.

## Sync Behavior

### Skills

For each target:

1. load the Hub Skill allowlist;
2. subtract unconditional target exclusions;
3. discover active plugin Skills;
4. subtract exact plugin duplicates;
5. apply conditional plugin-precedence entries for active plugins;
6. reconcile only Hub-managed links.

If a previously suppressed plugin disappears, the next sync recreates the Hub
Skill link.

The reconciler removes a Hub-managed duplicate symlink but never deletes a
real directory owned by the product or user.

### MCP

For each target:

1. merge Hub shared MCP definitions as today;
2. subtract unconditional target exclusions;
3. discover active plugin MCP declarations;
4. remove exact duplicates by normalized name or transport identity;
5. apply conditional plugin-precedence aliases;
6. preserve unrelated user/tool-only entries.

If a plugin disappears, the next sync restores the Hub MCP definition.

## Verification, Status, Trace, and Doctor

### Verification

A Hub capability is considered covered when it is:

- present as a Hub-managed target entry;
- present through an active exact-duplicate plugin capability;
- conditionally suppressed by an active plugin-precedence rule; or
- intentionally excluded by target policy.

These states are reported separately. "Excluded" is not presented as
"configured."

### Status and trace

`agent-sync status` and `agent-sync trace` show one of:

- `hub-managed`;
- `plugin-owned:<plugin-id>`;
- `target-excluded`;
- `duplicate`;
- `missing`;
- `plugin-state-uncertain`.

For plugin-owned entries, the output includes the evidence class but not
private paths, tokens, headers, or response bodies.

### Doctor

Doctor checks:

- cache-only plugins are not counted as active;
- enabled-state evidence resolves to an installed manifest;
- exact duplicates do not remain registered twice;
- conditional rules reference valid Hub capabilities;
- suppressed capabilities have an active owning plugin;
- plugin removal restores Hub coverage after sync;
- stale runtime evidence is reported rather than silently trusted.
- a Codex forbidden plugin is absent from `codex plugin list --json`;
- Codex local MCP ownership is not mislabeled as account/plugin ownership;
- the Cloudflare local MCP config contains no inline token or secret.

The current runtime diagnostic behavior must also be audited so a disabled or
tool-only MCP is not probed under the wrong Agent. Runtime checks consume the
same resolved ownership model as structural verification.

## Error Handling and Safety

- All marketplace inspection is read-only.
- Invalid JSON, truncated logs, unavailable databases, and missing manifests
  produce bounded findings rather than stack traces.
- No plugin cache entry is deleted.
- No product plugin is enabled or disabled.
- No raw OAuth token, API token, header, environment value, or query secret is
  copied into the Hub.
- Reconciliation writes only the same Hub-managed target files or symlinks that
  `agent-sync` already owns.

## Test Matrix

1. A cache-only Cursor plugin does not suppress a Hub Skill or MCP.
2. An active marketplace plugin with the same Skill name suppresses only the
   Cursor Hub symlink and counts as plugin-owned coverage.
3. An active marketplace plugin with the same MCP name suppresses the Cursor
   user entry.
4. An active marketplace plugin with a different name but identical normalized
   endpoint suppresses the Cursor user entry.
5. A conditional semantic alias suppresses only the listed capability while
   the listed plugin is active.
6. Removing active evidence restores the Hub Skill and MCP on the next sync.
7. An unconditional exclusion applies only to the selected target and is
   reported as excluded.
8. Other Agents remain unchanged.
9. Tool-only entries and unrelated user entries survive reconciliation.
10. A stale cached plugin never counts toward verification coverage.
11. Conflicting log/runtime evidence produces a Doctor finding.
12. Cloudflare's duplicate Skill is suppressed in Cursor, while
    `cloudflare-local` remains because its API endpoint is not an exact match.
13. A disabled/tool-only MCP is not runtime-probed as if it were an active
    shared MCP.
14. An enabled Codex plugin suppresses an exact Hub duplicate and is reported
    as Codex plugin-owned.
15. A cached but uninstalled Codex plugin does not suppress Hub distribution.
16. A forbidden Codex plugin produces a Doctor policy finding.
17. The current Cloudflare Codex state passes: no marketplace Cloudflare
    plugin, local `cloudflare-local` enabled, URL-only local config, and no raw
    token in the Hub.

## Acceptance Criteria

- The full unit suite passes.
- Structural verification passes for all supported Agents.
- A live Cursor sync reports the Cloudflare Skill as plugin-owned and leaves
  the broad `cloudflare-local` MCP available.
- A live Codex check reports the Cloudflare marketplace plugin absent and the
  local `cloudflare-local` MCP as local-only.
- Disabling the test fixture's plugin restores Hub-managed capability without a
  manual policy change.
- Doctor distinguishes active, stale, excluded, duplicate, and missing states.
- No product-managed plugin file or plugin setting is modified.
- No secret is introduced into the Hub or diagnostic output.
