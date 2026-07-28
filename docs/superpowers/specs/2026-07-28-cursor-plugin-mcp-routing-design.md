# Cursor Plugin MCP Routing Design

## Goal

Keep shared MCP capabilities available in every supported Agent without
registering a second user-level MCP in Cursor when a local Cursor plugin already
provides the same server.

## Chosen Approach

Agent Sync will treat Cursor's local plugin MCP declarations as an additional
product-owned capability source. During Cursor synchronization it will:

1. merge the Hub shared MCP definitions as usual;
2. discover server names declared by local Cursor plugins under
   `~/.cursor/plugins/local/*/mcp.json`;
3. remove only user-level Cursor entries whose case-insensitive names are both
   Hub-shared and plugin-provided.

The plugin files remain untouched. If a plugin is later removed, the next sync
restores the Hub definition to Cursor automatically.

Verification, status, and Doctor coverage count a shared server as configured
when it is present either in Cursor's user MCP file or in a local Cursor plugin.
This preserves the meaning of complete capability coverage while making
ownership explicit.

## Runtime Diagnostics

`agent-sync doctor --runtime` will add a bounded Cursor check for loopback HTTP
MCP entries. It sends only an unauthenticated `GET` with
`Accept: text/event-stream`, matching Cursor's optional SSE discovery request.
The probe:

- accepts `200`, `401`, `403`, and `405` as transport-reachable outcomes;
- reports `404` as Streamable HTTP incompatibility;
- reports connection failures without printing URLs, headers, or response
  bodies;
- skips non-loopback endpoints so Doctor never contacts remote MCP services.

This catches the MemPalace failure mode where JSON-RPC `POST /mcp` worked but
`GET /mcp` returned 404 and Cursor tombstoned the transport.

## Personal Hub Routing

The private Hub will use MemPalace's official local stdio entry point:

```json
{
  "type": "stdio",
  "command": "mempalace-mcp",
  "args": []
}
```

Cursor's official local MemPalace plugin remains the Cursor owner. Other Agents
receive the Hub stdio definition. The obsolete always-on MemPalace HTTP
LaunchAgent can then be retired, avoiding an unused background process.

## Safety and Compatibility

- Plugin directories and product-managed settings are never modified.
- Only duplicate user-level Cursor entries are pruned.
- Matching is case-insensitive and limited to names in the Hub manifest.
- Existing tool-only Cursor MCP entries remain untouched.
- The runtime probe is read-only, loopback-only, bounded, and secret-free.

## Verification

- A sync test proves a plugin-provided shared server is removed from Cursor's
  user MCP file while unrelated tool-only entries remain.
- A fallback test proves the shared entry is restored when the plugin is absent.
- Verify and Doctor tests prove plugin-provided names count toward shared
  coverage.
- Runtime tests inject standard-library openers returning 404 and 405 at the
  network boundary, keeping the probe deterministic without weakening its URL
  and response handling.
- The full unit suite, structural verify, live sync, and Cursor log/status check
  must pass before completion.
