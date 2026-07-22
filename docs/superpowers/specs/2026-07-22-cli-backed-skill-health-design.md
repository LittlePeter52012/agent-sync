# Agent Sync CLI-Backed Skill Health Design

## Goal

Make Agent Sync distinguish between a Skill that is structurally synchronized
and a Skill that is actually usable because its required local CLI exists.

## Decisions

1. The private Hub remains the source of truth for personal requirements.
2. `policies/tool-scopes.json` gains an optional top-level `skills` object.
3. Each Skill policy may declare `required_commands`, a list of executable
   names or paths that must be available on the local machine.
4. `agent-sync doctor` reports missing executables and invalid policy entries;
   `agent-sync verify --strict` fails when those findings remain.
5. Agent Sync never installs, upgrades, or removes a required CLI. Package
   ownership stays with the local package/install workflow.
6. A CLI-backed Skill remains a shared Skill. It is not promoted to shared MCP
   merely because its implementation uses a local executable.

## Policy Shape

```json
{
  "skills": {
    "example-cli-skill": {
      "required_commands": ["example-cli"]
    }
  }
}
```

Skill names must be listed in the Hub manifest. `required_commands` must be a
list of non-empty strings. Bare command names are resolved through `PATH`;
values containing a slash are checked as filesystem paths after expanding `~`.

## Privacy and Failure Handling

- Doctor reports only Skill and executable names, never command output,
  arguments, document contents, credentials, or environment values.
- Invalid or missing dependencies create read-only findings; normal structural
  synchronization remains unchanged.
- Absence of the optional `skills` policy preserves existing behavior.

## OfficeCLI Deployment Boundary

- OfficeCLI is installed locally as a pinned, checksum-verified binary.
- Its official Skill is stored in the private Hub and distributed by
  `agent-sync skills`.
- OfficeCLI auto-update and auto-install are disabled; Agent Sync remains the
  only configuration distributor.
- OfficeCLI MCP is not registered. Existing Microsoft 365 and native Office
  integrations retain their current ownership.

## Success Criteria

- A synchronized OfficeCLI Skill with a missing `officecli` binary is reported.
- The same policy is healthy once `officecli` is present on `PATH`.
- Unknown Skills and malformed command lists are reported without crashing.
- Existing Agent Sync tests, privacy audit, and synchronization behavior remain
  green.
