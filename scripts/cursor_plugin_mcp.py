#!/usr/bin/env python3
"""Discover Cursor plugin MCP servers and prune shadowed user entries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from atomic_file import atomic_write_text


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def plugin_server_names(home: Path) -> set[str]:
    """Return enabled MCP names declared by local Cursor plugins."""
    names: set[str] = set()
    root = home / ".cursor" / "plugins" / "local"
    if not root.exists():
        return names
    for config_path in sorted(root.glob("*/mcp.json")):
        servers = _load_json(config_path).get("mcpServers", {})
        if not isinstance(servers, dict):
            continue
        for name, config in servers.items():
            if (
                isinstance(name, str)
                and name.strip()
                and isinstance(config, dict)
                and config.get("disabled") is not True
            ):
                names.add(name.casefold())
    return names


def prune_shadowed(
    home: Path,
    canonical_path: Path,
    target_path: Path,
) -> list[str]:
    """Remove shared user MCP entries already supplied by a Cursor plugin."""
    canonical = _load_json(canonical_path).get("mcpServers", {})
    shared = (
        {name.casefold() for name in canonical}
        if isinstance(canonical, dict)
        else set()
    )
    shadowed = shared & plugin_server_names(home)
    if not shadowed:
        return []

    document = _load_json(target_path)
    servers = document.get("mcpServers", {})
    if not isinstance(servers, dict):
        return []
    removed = [
        name for name in list(servers)
        if name.casefold() in shadowed
    ]
    if not removed:
        return []
    for name in removed:
        servers.pop(name, None)
    atomic_write_text(
        target_path,
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
    )
    return sorted(removed, key=str.casefold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    prune = subparsers.add_parser("prune")
    prune.add_argument("--canonical", type=Path, required=True)
    prune.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "list":
        for name in sorted(plugin_server_names(args.home)):
            print(name)
        return 0

    removed = prune_shadowed(
        args.home,
        args.canonical,
        args.target,
    )
    if removed:
        print(
            "  Cursor plugin owns shared MCP: "
            + ", ".join(removed)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
