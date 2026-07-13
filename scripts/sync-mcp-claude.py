#!/usr/bin/env python3
"""Sync shared MCP servers into Claude Code using the official CLI.

The Claude Code CLI owns its MCP storage format, so this script intentionally
uses `claude mcp add` instead of editing private config files directly.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value))


def resolve(value: object, donors: dict[str, str]) -> object | None:
    if is_placeholder(value):
        return donors.get(PLACEHOLDER_RE.match(value).group(1))  # type: ignore[union-attr]
    return value


def donor_maps() -> dict[str, dict[str, str]]:
    """Read only concrete values already configured for the same shared MCP."""
    result: dict[str, dict[str, str]] = {}
    for path in (Path.home() / ".cursor" / "mcp.json", Path.home() / ".gemini" / "config" / "mcp_config.json"):
        if not path.exists():
            continue
        try:
            servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
        except json.JSONDecodeError:
            continue
        for name, config in servers.items():
            if not isinstance(config, dict):
                continue
            values = result.setdefault(name.lower(), {})
            for key, value in (config.get("env") or {}).items():
                if isinstance(value, str) and not is_placeholder(value):
                    values[str(key)] = value
                    if key == "OPENAPI_MCP_HEADERS":
                        values.setdefault("ANYTYPE_MCP_HEADERS", value)
            for index, value in enumerate(config.get("args") or []):
                if isinstance(value, str) and not is_placeholder(value):
                    values.setdefault(f"ARG_{index}", value)
    return result


def concrete_env(env: dict[str, Any], donors: dict[str, str]) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for k, v in env.items():
        value = resolve(v, donors)
        if value is None:
            return None
        if str(value) != "":
            out[str(k)] = str(value)
    return out


def concrete_args(args: list[Any], donors: dict[str, str]) -> list[str] | None:
    out: list[str] = []
    for index, arg in enumerate(args):
        value = resolve(arg, donors)
        if value is None:
            value = donors.get(f"ARG_{index}") if is_placeholder(arg) else None
        if value is None:
            return None
        out.append(str(value))
    return out


def configured_names(claude_bin: str) -> set[str]:
    try:
        result = subprocess.run(
            [claude_bin, "mcp", "list"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except FileNotFoundError:
        return set()

    names: set[str] = set()
    for line in result.stdout.splitlines():
        if ":" in line and not line.startswith("Checking "):
            names.add(line.split(":", 1)[0].strip().lower())
    return names


def command_for(name: str, cfg: dict[str, Any], scope: str, donors: dict[str, str]) -> list[str] | None:
    typ = cfg.get("type", "stdio")
    if typ == "http" or cfg.get("url"):
        url = resolve(cfg.get("url"), donors)
        if not url:
            return None
        cmd = ["mcp", "add", "--scope", scope, "--transport", "http", name, str(url)]
        for key, value in (cfg.get("headers") or {}).items():
            if value is not None and not is_placeholder(value):
                cmd.extend(["--header", f"{key}: {value}"])
        return cmd

    command = resolve(cfg.get("command"), donors)
    if not command:
        return None
    args = concrete_args(cfg.get("args") or [], donors)
    if args is None:
        return None

    cmd = ["mcp", "add", "--scope", scope, name]
    env = concrete_env(cfg.get("env") or {}, donors)
    if env is None:
        return None
    for key, value in env.items():
        cmd.extend(["--env", f"{key}={value}"])
    cmd.extend(["--", str(command), *args])
    return cmd


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    argv = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
    if len(argv) != 1:
        print(f"Usage: {sys.argv[0]} <canonical.json> [--dry-run]", file=sys.stderr)
        return 1

    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    scope = os.environ.get("CLAUDE_MCP_SYNC_SCOPE", "user")
    canonical = json.loads(Path(argv[0]).expanduser().read_text(encoding="utf-8"))
    existing = configured_names(claude_bin) if not dry_run else set()
    donors = donor_maps()

    added = skipped = existing_count = 0
    skipped_names: list[str] = []
    for name, cfg in (canonical.get("mcpServers") or {}).items():
        if name.lower() in existing:
            existing_count += 1
            continue
        cmd = command_for(name, cfg, scope, donors.get(name.lower(), {}))
        if not cmd:
            skipped += 1
            skipped_names.append(name)
            continue
        full_cmd = [claude_bin, *cmd]
        if dry_run:
            display = [shlex.quote(part) for part in full_cmd]
            for index, part in enumerate(full_cmd[:-1]):
                if part == "--env":
                    display[index + 1] = "'<redacted env>'"
            print("  would run: " + " ".join(display))
            added += 1
            continue
        result = subprocess.run(full_cmd, text=True, check=False)
        if result.returncode == 0:
            added += 1
        else:
            skipped += 1
            skipped_names.append(name)

    print(f"  claude mcp: +{added} existing={existing_count} skipped={skipped}")
    if skipped_names:
        print("  skipped: " + ", ".join(skipped_names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
