#!/usr/bin/env python3
"""Build a secret-free inventory of local Agent MCP configurations."""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from capability_resolver import (
        TargetResolution,
        mcp_identity_matches,
        resolve_target_policy,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capability_resolver import (
        TargetResolution,
        mcp_identity_matches,
        resolve_target_policy,
    )


CODEX_TABLE_RE = re.compile(
    r"^\s*\[mcp_servers\.([^\.\]\s]+)(?:\.(?:env|headers|http_headers))?\]\s*$"
)
SATISFIED_MCP_STATES = frozenset(
    {
        "hub-managed",
        "plugin-owned",
        "target-owned",
        "excluded",
        "disabled-by-policy",
    }
)


def _read_json(path: Path, bucket: str) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    value = data.get(bucket, {})
    return (value if isinstance(value, dict) else {}), not isinstance(value, dict)


def read_canonical_shared_mcp(
    hub: Path,
) -> tuple[dict[str, Any], str | None]:
    path = hub / "mcp" / "shared-servers.json"
    try:
        exists = path.exists()
        dangling_symlink = not exists and path.is_symlink()
    except OSError:
        return {}, "unreadable"
    if not exists:
        return ({}, "unreadable") if dangling_symlink else ({}, None)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return {}, "unreadable"
    except json.JSONDecodeError:
        return {}, "invalid-json"
    if not isinstance(document, dict):
        return {}, "invalid-root"
    servers = document.get("mcpServers")
    if not isinstance(servers, dict):
        return {}, "invalid-servers"
    return servers, None


def _read_codex(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, True

    servers: dict[str, dict[str, Any]] = {}
    current: Optional[dict[str, Any]] = None
    nested = False
    for line in text.splitlines():
        table = CODEX_TABLE_RE.match(line)
        if table:
            name = table.group(1)
            current = servers.setdefault(name, {})
            nested = bool(re.search(r"\.(?:env|headers|http_headers)\]$", line.strip()))
            continue
        if line.lstrip().startswith("["):
            current = None
            nested = False
            continue
        if current is None or nested or "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        raw_value = raw.strip()
        try:
            value = ast.literal_eval(raw_value)
        except (SyntaxError, ValueError):
            if raw_value == "true":
                value = True
            elif raw_value == "false":
                value = False
            else:
                value = raw_value
        current[key.strip()] = value
    return servers, False


def _read_retired(hub: Path) -> set[str]:
    path = hub / "mcp" / "retired-servers.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    values = data.get("retiredServers", []) if isinstance(data, dict) else []
    return {str(value).lower() for value in values if isinstance(value, str)}


def _read_shared(hub: Path) -> set[str]:
    servers, issue = read_canonical_shared_mcp(hub)
    return set() if issue else {str(name).lower() for name in servers}


def _command(config: dict[str, Any]) -> str | None:
    command = config.get("command")
    if isinstance(command, list):
        return str(command[0]) if command else ""
    return command if isinstance(command, str) else None


def _is_remote(config: dict[str, Any]) -> bool:
    return bool(config.get("url")) or config.get("type") in {"http", "sse", "remote"}


def _is_enabled(config: dict[str, Any]) -> bool:
    return config.get("enabled", True) is not False and config.get("disabled", False) is not True


def _command_available(command: str) -> bool:
    if not command:
        return False
    path = Path(command).expanduser()
    if path.is_absolute() or "/" in command:
        return path.exists()
    return bool(shutil.which(command))


def _source_specs(home: Path) -> list[tuple[str, str, Path, str]]:
    vscode = home / "Library" / "Application Support" / "Code" / "User"
    specs = [
        ("cursor", "Cursor", home / ".cursor" / "mcp.json", "mcpServers"),
        (
            "gemini",
            "Gemini CLI",
            home / ".gemini" / "settings.json",
            "mcpServers",
        ),
        (
            "gemini-global",
            "Gemini global",
            home / ".gemini" / "config" / "mcp_config.json",
            "mcpServers",
        ),
        (
            "antigravity-app",
            "Antigravity App",
            home / ".gemini" / "antigravity" / "mcp_config.json",
            "mcpServers",
        ),
        (
            "antigravity-cli",
            "Antigravity CLI",
            home / ".gemini" / "antigravity-cli" / "mcp_config.json",
            "mcpServers",
        ),
        (
            "antigravity-ide",
            "Antigravity IDE",
            home / ".gemini" / "antigravity-ide" / "mcp_config.json",
            "mcpServers",
        ),
        ("claude", "Claude", home / ".claude.json", "mcpServers"),
        (
            "opencode",
            "OpenCode",
            home / ".config" / "opencode" / "opencode.json",
            "mcp",
        ),
        ("codex", "Codex / ChatGPT", home / ".codex" / "config.toml", "toml"),
        ("vscode", "Copilot / VS Code", vscode / "mcp.json", "servers"),
    ]
    profiles = vscode / "profiles"
    if profiles.exists():
        for profile in sorted(path for path in profiles.iterdir() if path.is_dir()):
            specs.append(
                (
                    f"vscode:{profile.name}",
                    f"VS Code profile {profile.name}",
                    profile / "mcp.json",
                    "servers",
                )
            )
    return specs


def _duplicate_names(names: list[str]) -> list[list[str]]:
    grouped: dict[str, list[str]] = {}
    for name in names:
        grouped.setdefault(name.casefold(), []).append(name)
    return [
        sorted(values)
        for values in grouped.values()
        if len(values) > 1
    ]


def collect_inventory(
    home: Path,
    hub: Path,
    *,
    shared: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return only metadata needed to assess MCP source health."""
    retired_names = _read_retired(hub)
    shared_names = (
        _read_shared(hub)
        if shared is None
        else {str(name).lower() for name in shared}
    )
    records: list[dict[str, Any]] = []
    for tool, label, path, bucket in _source_specs(home):
        if bucket == "toml":
            servers, invalid = _read_codex(path)
        else:
            servers, invalid = _read_json(path, bucket)

        enabled = {
            str(name): config
            for name, config in servers.items()
            if isinstance(config, dict) and _is_enabled(config)
        }
        retired = sorted(name for name in enabled if name.lower() in retired_names)
        missing_commands = sorted(
            name
            for name, config in enabled.items()
            if not _is_remote(config)
            and (_command(config) is None or not _command_available(_command(config) or ""))
        )
        mcp_names = sorted(enabled, key=str.lower)
        duplicate_names = _duplicate_names(mcp_names)
        exists = path.exists()
        ready = bool(
            exists
            and mcp_names
            and not invalid
            and not retired
            and not missing_commands
            and not duplicate_names
        )
        try:
            modified = path.stat().st_mtime if exists else 0.0
        except OSError:
            modified = 0.0
        shared_count = len({name.lower() for name in mcp_names} & shared_names)
        records.append(
            {
                "tool": tool,
                "label": label,
                "is_profile": tool.startswith("vscode:"),
                "promotable": tool != "vscode:builtin",
                "config_present": exists,
                "parse_error": invalid,
                "mcp_names": mcp_names,
                "mcp_count": len(mcp_names),
                "shared_count": shared_count,
                "tool_only_count": len(mcp_names) - shared_count,
                "retired": retired,
                "duplicate_names": duplicate_names,
                "missing_commands": missing_commands,
                "modified": modified,
                "ready": ready,
            }
        )
    return records


def rank_sources(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank usable source candidates; modification time is only a tie-breaker."""
    candidates = [
        record
        for record in records
        if record["promotable"] and record["config_present"] and record["mcp_count"] > 0
    ]
    return sorted(
        candidates,
        key=lambda record: (
            not record["ready"],
            -record["shared_count"],
            record["tool_only_count"],
            record["is_profile"],
            -record["modified"],
            record["tool"],
        ),
    )


def _target_servers(target: str, home: Path) -> tuple[dict[str, Any], bool]:
    for tool, _label, path, bucket in _source_specs(home):
        if tool != target:
            continue
        if bucket == "toml":
            return _read_codex(path)
        return _read_json(path, bucket)
    return {}, False


def resolve_mcp_coverage(
    target: str,
    home: Path,
    hub: Path,
    *,
    shared: dict[str, Any] | None = None,
    resolution: TargetResolution | None = None,
) -> dict[str, dict[str, str]]:
    """Classify every shared MCP for a target without exposing configuration."""
    if shared is None:
        shared, issue = read_canonical_shared_mcp(hub)
        if issue:
            return {}
    servers, parse_error = _target_servers(target, home)
    by_name: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for name, config in servers.items():
        if isinstance(name, str) and isinstance(config, dict):
            by_name.setdefault(name.casefold(), []).append((name, config))

    if resolution is None:
        resolution = resolve_target_policy(target, home, hub)
    capability_by_mcp = {
        name.casefold(): decision
        for decision in resolution.capabilities.values()
        for name in decision.hub_mcp
    }
    coverage: dict[str, dict[str, str]] = {}
    for name, canonical in shared.items():
        if not isinstance(name, str) or not isinstance(canonical, dict):
            continue
        key = name.casefold()
        capability = capability_by_mcp.get(key)
        if capability is not None and capability.state in {
            "ownership-conflict",
            "owner-missing",
            "disabled-by-policy",
            "target-owned",
        }:
            coverage[name] = {
                "state": capability.state,
                "owner": capability.desired_owner,
            }
            continue
        owner = resolution.mcp_owners.get(key)
        if key in resolution.suppressed_mcp:
            state = "excluded" if owner == "excluded" else "plugin-owned"
            coverage[name] = {"state": state, "owner": owner or state}
            continue
        matches = by_name.get(key, [])
        if parse_error or len(matches) > 1:
            coverage[name] = {"state": "drifted", "owner": "hub-managed"}
            continue
        if not matches:
            coverage[name] = {"state": "missing", "owner": "hub-managed"}
            continue
        _actual_name, config = matches[0]
        if not _is_enabled(config):
            coverage[name] = {"state": "disabled", "owner": "hub-managed"}
            continue
        state = (
            "hub-managed"
            if mcp_identity_matches(config, canonical)
            else "drifted"
        )
        coverage[name] = {"state": state, "owner": "hub-managed"}
    for name, record in coverage.items():
        if name.casefold() in resolution.mcp_collisions:
            record["collision"] = "plugin-transport"
    return coverage


def trace_mcp(name: str, home: Path, hub: Path) -> dict[str, Any]:
    """Locate an MCP by name without exposing its configuration values."""
    normalized = name.lower()
    shared = _read_shared(hub)
    retired = _read_retired(hub)
    if normalized in shared:
        status = "shared"
    elif normalized in retired:
        status = "retired"
    else:
        status = "tool-only"
    locations = [
        record["tool"]
        for record in collect_inventory(home, hub)
        if normalized in {server.lower() for server in record["mcp_names"]}
    ]
    ownership: dict[str, str] = {}
    for tool, _label, _path, _bucket in _source_specs(home):
        if ":" in tool:
            continue
        coverage = resolve_mcp_coverage(tool, home, hub)
        for canonical, record in coverage.items():
            if canonical.casefold() == normalized:
                ownership[tool] = record["state"]
                break
    return {
        "name": name,
        "hub_status": status,
        "locations": locations,
        "ownership": ownership,
    }
