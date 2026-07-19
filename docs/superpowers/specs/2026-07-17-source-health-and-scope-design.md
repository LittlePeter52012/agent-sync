# Agent Sync Source Health and Scope Design

## Goal

Make source promotion safer and make health reports reflect effective local
configuration, while preserving the private Hub as the only shared source of
truth.

## Decisions

1. `agent-sync sync --from TOOL` remains the only command that promotes a
   tool configuration into the Hub.
2. Agent Sync never chooses a source solely because its file has the newest
   modification time. A newer file can contain stale, broken, or tool-only
   entries.
3. `agent-sync sources` is read-only. It ranks source candidates using static
   health first, MCP coverage second, and modification time only as a
   tie-breaker.
4. `agent-sync doctor` checks every configured local MCP executable, including
   tool-only entries, and checks for retired names left in any managed target.
5. `agent-sync doctor --runtime` adds bounded CLI probes for supported tools.
   It emits only tool names and status summaries, never raw command output.
6. Optional Hub policy in `policies/tool-scopes.json` declares required or
   forbidden plugins per tool. Agent Sync audits this policy but never installs
   or uninstalls plugins.
7. `agent-sync trace mcp NAME` explains whether a server is shared, retired,
   or tool-only and lists the static configurations containing it.
8. `agent-sync verify --strict` combines structural verification with runtime
   doctor findings and exits non-zero when actionable findings remain.

## Source Ranking

Each supported source receives these observations:

- configuration exists and parses;
- configured MCP count;
- retired MCP names still present;
- local MCP commands missing from `PATH` or the filesystem;
- configuration modification time.

A source is `ready` only when it is non-empty, contains no retired names, and
has no missing local command. Ready sources rank ahead of unhealthy sources.
Among ready sources, shared-MCP coverage ranks first, lower tool-only drift
ranks second, top-level tools rank before VS Code profile replicas, and
modification time is only the final tie-breaker. The command recommends a source
but never promotes it. Disabled source entries are ignored by both
recommendation and promotion; the VS Code builtin profile is audited but is not
offered as a promotion source.

## Runtime Health

Runtime probes use installed product CLIs with a fixed timeout. Parsers retain
only MCP names and normalized states such as `connected`, `failed`, or
`timeout`. Raw stdout and stderr are discarded after parsing because they may
contain paths, environment values, or account data.

Runtime checks are opt-in so the normal `doctor` and `sync` workflows remain
fast and offline-safe.

## Plugin Scope

The private Hub may contain:

```json
{
  "plugins": {
    "opencode": {
      "required": ["example-opencode-plugin"]
    },
    "codex": {
      "forbidden": ["example-plugin@example-marketplace"]
    }
  }
}
```

Static doctor checks OpenCode plugin specs and Codex plugin configuration.
Findings are advisory. Native plugin managers remain authoritative because
installs, hook trust, and upgrades have product-specific side effects.

## Privacy and Failure Handling

- Never print MCP environment values, headers, tokens, URLs with credentials,
  account identifiers, or raw runtime output.
- A missing optional CLI is reported as unavailable, not as a global failure.
- A runtime timeout is an attention finding in strict mode.
- Invalid policy JSON produces one clear finding and does not block structural
  synchronization.
- Existing tool-only MCP entries remain untouched unless their name is in the
  Hub retired list.

## Success Criteria

- A missing executable in a tool-only MCP is reported by normal doctor.
- A retired MCP left in any managed target is reported.
- Runtime doctor reports a failed OpenCode or Claude MCP without leaking raw
  output.
- Source recommendation rejects a newer broken source in favor of a healthy
  source.
- Plugin policy reports required/forbidden drift without changing plugins.
- Trace explains one MCP name across the Hub and all managed targets.
- Existing tests and the public/private privacy audit remain green.
