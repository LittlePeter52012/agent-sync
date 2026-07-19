#!/usr/bin/env python3
"""Build a secret-free inventory of local Agent MCP configurations."""

from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path
from typing import Any, Optional


CODEX_TABLE_RE = re.compile(
    r"^\s*\[mcp_servers\.([^\.\]\s]+)(?:\.(?:env|headers|http_headers))?\]\s*$"
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
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    values = data.get("retiredServers", []) if isinstance(data, dict) else []
    return {str(value).lower() for value in values if isinstance(value, str)}


def _read_shared(hub: Path) -> set[str]:
    servers, invalid = _read_json(hub / "mcp" / "shared-servers.json", "mcpServers")
    return set() if invalid else {str(name).lower() for name in servers}


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
            "antigravity",
            "Gemini / Antigravity",
            home / ".gemini" / "config" / "mcp_config.json",
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


def collect_inventory(home: Path, hub: Path) -> list[dict[str, Any]]:
    """Return only metadata needed to assess MCP source health."""
    retired_names = _read_retired(hub)
    shared_names = _read_shared(hub)
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
        exists = path.exists()
        ready = bool(exists and mcp_names and not invalid and not retired and not missing_commands)
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
    return {
        "name": name,
        "hub_status": status,
        "locations": locations,
    }
