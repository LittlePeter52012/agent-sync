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


def concrete_env(env: dict[str, Any]) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for k, v in env.items():
        if is_placeholder(v):
            return None
        if v is not None and str(v) != "":
            out[str(k)] = str(v)
    return out


def concrete_args(args: list[Any]) -> list[str] | None:
    out: list[str] = []
    for arg in args:
        if is_placeholder(arg):
            return None
        if arg is not None:
            out.append(str(arg))
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


def command_for(name: str, cfg: dict[str, Any], scope: str) -> list[str] | None:
    typ = cfg.get("type", "stdio")
    if typ == "http" or cfg.get("url"):
        url = cfg.get("url")
        if not url or is_placeholder(url):
            return None
        cmd = ["mcp", "add", "--scope", scope, "--transport", "http", name, str(url)]
        for key, value in (cfg.get("headers") or {}).items():
            if value is not None and not is_placeholder(value):
                cmd.extend(["--header", f"{key}: {value}"])
        return cmd

    command = cfg.get("command")
    if not command or is_placeholder(command):
        return None
    args = concrete_args(cfg.get("args") or [])
    if args is None:
        return None

    cmd = ["mcp", "add", "--scope", scope, name]
    env = concrete_env(cfg.get("env") or {})
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

    added = skipped = existing_count = 0
    for name, cfg in (canonical.get("mcpServers") or {}).items():
        if name.lower() in existing:
            existing_count += 1
            continue
        cmd = command_for(name, cfg, scope)
        if not cmd:
            skipped += 1
            continue
        full_cmd = [claude_bin, *cmd]
        if dry_run:
            print("  would run: " + " ".join(shlex.quote(part) for part in full_cmd))
            added += 1
            continue
        result = subprocess.run(full_cmd, text=True, check=False)
        if result.returncode == 0:
            added += 1
        else:
            skipped += 1

    print(f"  claude mcp: +{added} existing={existing_count} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
