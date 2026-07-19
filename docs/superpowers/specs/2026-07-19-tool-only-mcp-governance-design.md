# Tool-Only MCP Governance Design

**Date:** 2026-07-19

## Problem

Agent Sync deliberately preserves MCP servers that belong to only one Agent
tool. That protects native integrations, but today `doctor` cannot distinguish
an intentional tool-only MCP from accidental configuration drift.

This becomes confusing when one capability has two valid interfaces:

- a product-owned MCP used inside one host application;
- a shared CLI-backed Skill used by every external Agent.

Promoting the product-owned MCP into the private Hub would create duplicate
tools and make other Agents depend on a host application that may not be
running. Removing it would break the host application's native workflow.

## Goals

- Keep shared MCP definitions Hub-owned and distributed everywhere.
- Keep native or host-scoped MCP definitions in their owning tool.
- Let a private Hub declare which tool-only MCP names are allowed or required.
- Report unexpected tool-only MCP drift without reading or printing secrets.
- Document how a local process, a cloud-backed service, a Skill, an MCP, and a
  product plugin differ.
- Preserve all existing merge and source-promotion behavior.

## Non-Goals

- Installing, uninstalling, or enabling native product plugins.
- Promoting tool-only MCP definitions into the shared Hub automatically.
- Runtime probing every MCP endpoint.
- Replacing a product's own confirmation or permission system.

## Capability Taxonomy

Agent Sync uses four ownership classes:

| Class | Owner | Distribution |
|---|---|---|
| Shared Skill | Private Hub manifest | Symlinked to every supported Agent |
| Shared MCP | Private Hub `mcp/shared-servers.json` | Merged into every supported Agent |
| Tool-only MCP | One Agent tool | Preserved only in that tool |
| Native plugin/extension | Product plugin manager | Never synchronized by Agent Sync |

Execution location and data location are separate properties. A local CLI or
local MCP process can still call a remote service. The private Hub runbook must
state both properties instead of calling every locally launched command
"offline".

## Policy Schema

Extend the existing optional `policies/tool-scopes.json` file:

```json
{
  "plugins": {
    "opencode": {
      "required": ["example-opencode-plugin"]
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

Supported tool keys match source-advisor identifiers for top-level tools:
`antigravity`, `cursor`, `claude`, `opencode`, `codex`, and `vscode`.

Rules:

1. If a tool has no MCP policy, Agent Sync preserves current behavior and does
   not judge its tool-only names.
2. If `allowed_tool_only` is present, every enabled tool-only MCP must be in
   that list.
3. Every `required_tool_only` name must be configured and enabled in its
   owning tool.
4. `required_tool_only` must be a subset of `allowed_tool_only`.
5. Matching is case-insensitive; findings show only tool and MCP names.
6. Shared and retired MCP handling remains authoritative and unchanged.

## Doctor Behavior

`agent-doctor.py` will reuse the secret-free inventory already consumed by
`sources` and `trace`. It will:

1. Read and validate the MCP scope policy.
2. Compute enabled tool-only names as inventory names minus Hub shared names.
3. Report an attention finding for:
   - an unexpected tool-only MCP;
   - a missing required tool-only MCP;
   - an invalid required/allowed relationship;
   - malformed MCP policy data.
4. Emit no command arguments, URLs, environment values, headers, or tokens.

The first release is audit-only. `agent-sync fix` will not remove unexpected
tool-only MCP servers because they are outside Hub ownership.

## Host-Scoped Integration Pattern

For an application that exposes a loopback MCP to its own embedded Agent:

- keep the loopback MCP in the embedded Agent's native configuration;
- list it as allowed/required for that Agent in the private policy;
- do not add it to `shared-servers.json`;
- provide a shared CLI Skill for external Agents when a stable CLI exists;
- add a routing rule saying that one operation uses one interface, never both.

This preserves the native workflow while keeping the external Agent surface
small and deterministic.

## Verification

- Unit tests prove allowed, unexpected, missing-required, invalid-policy, and
  secret-free behavior.
- Existing Agent Sync unit and endpoint suites remain green.
- A private Hub with explicit scope policy passes `doctor --strict`.
- `sources` continues to treat intentional tool-only MCPs as source drift; it
  should still prefer the cleanest shared-MCP donor.
- Privacy audit confirms the public repository contains no personal names,
  paths, tokens, or private Hub content.

