# Agent Sync Reliability Hardening Design

## Goal

Make `agent-sync` report and reconcile the actual ownership and usability of
shared capabilities instead of treating name presence as health.

This design extends the approved
`2026-07-30-plugin-marketplace-dedup-exclusions-design.md` with the reliability,
privacy, and test defects found during the full local audit.

## Current Failure Modes

The audit reproduced three false-success states:

1. every shared MCP was disabled and pointed at the wrong endpoint, but
   `verify` returned success;
2. every managed rule was missing from its target, but `verify` returned
   success;
3. a token-shaped value in a public-repository documentation file was not
   detected by `audit`.

The live machine also has a Cursor marketplace Cloudflare Skill shadowing the
Hub Skill, while Doctor reports no finding, and a group-readable Claude config
contains concrete MCP environment fields.

## Unified Capability Resolution

One Python module will resolve desired and actual capability state for every
command. It owns:

- canonical Hub Skill and MCP inventory;
- target configuration readers;
- enabled/disabled MCP state;
- normalized MCP transport identity;
- active product-plugin evidence;
- conditional plugin precedence;
- unconditional target exclusions;
- ownership labels and diagnostic findings.

`status`, `trace`, `verify`, `doctor`, Skill sync, and MCP sync consume this
model. They must not maintain independent name-counting implementations.

## Verification Semantics

A shared MCP is usable only when one of these is true:

- the target has an enabled Hub-managed entry whose normalized transport
  matches the canonical transport;
- an active product plugin supplies an exact name or transport match;
- an active plugin-precedence policy explicitly owns the canonical name;
- the target policy intentionally excludes it.

Excluded capabilities pass policy validation but are reported as `excluded`,
not `configured`.

For stdio transports, verification compares the command basename and the
canonical non-secret argument structure. Placeholder positions may resolve to
local absolute paths or environment values. For HTTP transports, verification
compares normalized scheme, host, port, and path while never printing query
values, headers, or credentials.

A managed rule passes only when its normalized managed block occurs exactly
once in every declared target.

## Plugin Ownership

The plugin capability model and policy schema are defined in the companion
plugin-marketplace design. Phase-one active adapters are:

- Cursor marketplace logs plus declared cache revision, and explicit local
  plugins;
- Codex `codex plugin list --json`;
- Claude `claude plugin list --json`;
- Gemini `gemini extensions list`;
- OpenCode's configured plugin list.

Only Cursor and Codex receive automatic Skill/MCP suppression in this release
because their target roots are unambiguous. Other adapters report conflicts
until a product-safe reconciliation path exists.

Plugin cache presence alone never proves active state.

## Secret and Permission Boundary

Hub templates remain placeholder-only. Concrete values may be resolved locally,
but cross-Agent donor propagation is allowed only for fields explicitly listed
in a target policy. Existing compatibility behavior remains available through
an explicit `allow_secret_donors` target option.

Doctor reports:

- sensitive local configuration files that are group/world readable;
- concrete sensitive fields by server and field name, never by value;
- local, plugin, Hub, and account/product ownership separately.

Sync enforces mode `0600` on target files containing concrete MCP environment
or header values after a successful write.

The public privacy audit scans the complete working tree, including untracked
files, while excluding `.git` and known generated caches. It scans documentation
and tests. Placeholder syntax does not exempt an entire line from token
detection.

## Complete Test Entry Point

`agent-sync test` runs, in order:

1. the Python `unittest` suite;
2. the existing Bash evaluation suite;
3. Python compile checks and Bash syntax checks.

It exits nonzero when any phase fails and prints the count for each phase.

## Idempotency

`agent-sync fix --dry-run` reports only actual changes. Codex reconciliation
compares normalized section content so product formatting of blank lines does
not create perpetual drift.

Running `agent-sync all` twice must leave every tracked and target configuration
byte-identical on the second run, except product-managed files that the product
rewrites independently; those are compared semantically.

## Agent Hub Policy

The private Hub adds:

```json
{
  "plugins": {
    "cursor": {
      "prefer_marketplace": {
        "cloudflare": {
          "skills": ["cloudflare"],
          "mcp": []
        }
      }
    },
    "codex": {
      "forbidden": [
        "omo@sisyphuslabs",
        "cloudflare@openai-curated-remote"
      ]
    }
  },
  "distribution": {
    "cursor": {
      "exclude_skills": [],
      "exclude_mcp": []
    }
  },
  "secrets": {
    "targets": {
      "cursor": {"allow_secret_donors": true},
      "gemini": {"allow_secret_donors": true},
      "gemini-global": {"allow_secret_donors": true},
      "antigravity-app": {"allow_secret_donors": true},
      "antigravity-cli": {"allow_secret_donors": true},
      "antigravity-ide": {"allow_secret_donors": true},
      "claude": {"allow_secret_donors": true},
      "opencode": {"allow_secret_donors": true},
      "vscode": {"allow_secret_donors": true},
      "codex": {"allow_secret_donors": false}
    }
  }
}
```

The explicit compatibility allowlist preserves current working MCPs while
making the data-flow boundary visible. Codex remains local-existing/env only.

## Acceptance Criteria

- Disabled or wrong-transport shared MCP fixtures fail verification.
- Missing and duplicate managed-rule fixtures fail verification.
- An active Cursor Cloudflare plugin owns the duplicate Skill; the Hub symlink
  is absent only in Cursor and returns automatically when the plugin disappears.
- `cloudflare-local` remains in Cursor and Codex because no plugin exact match
  replaces the full API endpoint.
- Codex Cloudflare marketplace plugin remains absent and is forbidden by policy.
- `trace`, `status`, `verify`, and Doctor report identical ownership.
- Secret-bearing target files are mode `0600`.
- A token-shaped value in `docs/` or `tests/` fails the privacy audit.
- `agent-sync test` runs all Python and Bash tests.
- Two consecutive syncs are idempotent.
- The full unit suite, Bash suite, strict verification, runtime Doctor, and
  privacy audit pass on the live machine.
