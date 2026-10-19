#!/usr/bin/env python3
"""Remove case-only aliases of canonical shared MCP server names."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from atomic_file import atomic_write_text


BUCKETS = ("mcpServers", "mcp", "servers")
PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def load_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_atomic(path: Path, value: dict[str, Any]) -> None:
    # Follows a symlinked config, keeps the file mode, fsyncs before replace.
    atomic_write_text(
        path,
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        default_mode=0o600,
    )


def fill_missing(primary: Any, fallback: Any) -> Any:
    """Preserve canonical values while copying missing nested JSON fields."""
    if isinstance(primary, dict) and isinstance(fallback, dict):
        for key, value in fallback.items():
            if key not in primary:
                primary[key] = value
            else:
                primary[key] = fill_missing(primary[key], value)
        return primary
    if isinstance(primary, list) and isinstance(fallback, list):
        for index, value in enumerate(fallback):
            if index >= len(primary):
                primary.append(value)
            else:
                primary[index] = fill_missing(primary[index], value)
        return primary
    missing = (
        primary is None
        or primary == ""
        or (
            isinstance(primary, str)
            and PLACEHOLDER_RE.match(primary)
        )
    )
    if missing and fallback not in (None, ""):
        return fallback
    return primary


def prune_aliases(canonical_path: Path, target_path: Path) -> list[str]:
    canonical = load_object(canonical_path).get("mcpServers", {})
    if not isinstance(canonical, dict):
        raise ValueError("canonical mcpServers must be a JSON object")
    canonical_names = {
        str(name).casefold(): str(name)
        for name in canonical
    }

    target = load_object(target_path)
    bucket = next(
        (
            name
            for name in BUCKETS
            if isinstance(target.get(name), dict)
        ),
        "mcpServers",
    )
    servers = target.setdefault(bucket, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{target_path} {bucket} must be a JSON object")

    removed: list[str] = []
    for folded_name, canonical_name in canonical_names.items():
        aliases = sorted(
            name
            for name in list(servers)
            if name.casefold() == folded_name and name != canonical_name
        )
        if canonical_name not in servers and aliases:
            source_name = aliases.pop(0)
            servers[canonical_name] = servers.pop(source_name)
            removed.append(source_name)
        for name in aliases:
            fill_missing(servers.get(canonical_name), servers[name])
            del servers[name]
            removed.append(name)
    removed.sort()
    if removed:
        write_atomic(target_path, target)
    return removed


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: prune-mcp-aliases.py <shared-servers.json> <target.json>",
            file=sys.stderr,
        )
        return 2
    removed = prune_aliases(Path(sys.argv[1]), Path(sys.argv[2]))
    if removed:
        print(f"  removed case-only aliases: {', '.join(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
