#!/usr/bin/env python3
"""Remove retired Hub-managed MCP names from one JSON or Codex TOML target."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


MCP_TABLE_RE = re.compile(r"^\s*\[mcp_servers\.([^\.\]\s]+)(?:\.[^\]]+)?\]\s*$")


def read_retired(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("retiredServers", [])
    return {str(value).lower() for value in values} if isinstance(values, list) else set()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def prune_json(path: Path, retired: set[str]) -> int:
    if not path.exists() or not retired:
        return 0
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    bucket = next(
        (key for key in ("mcpServers", "servers", "mcp") if isinstance(data.get(key), dict)),
        None,
    )
    if bucket is None:
        return 0
    servers = data[bucket]
    remove = [name for name in servers if name.lower() in retired]
    for name in remove:
        del servers[name]
    if remove:
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return len(remove)


def prune_codex_text(text: str, retired: set[str]) -> tuple[str, int]:
    if not retired:
        return text, 0
    out: list[str] = []
    skipping = False
    removed: set[str] = set()
    for line in text.splitlines(keepends=True):
        match = MCP_TABLE_RE.match(line.rstrip("\r\n"))
        if match:
            name = match.group(1).lower()
            skipping = name in retired
            if skipping:
                removed.add(name)
        if not skipping:
            out.append(line)
    result = "".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result, len(removed)


def prune_codex(path: Path, retired: set[str]) -> int:
    if not path.exists() or not retired:
        return 0
    before = path.read_text(encoding="utf-8")
    after, count = prune_codex_text(before, retired)
    if count:
        atomic_write(path, after)
    return count


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <retired.json> <target>", file=sys.stderr)
        return 1
    retired = read_retired(Path(sys.argv[1]))
    target = Path(sys.argv[2]).expanduser()
    count = prune_codex(target, retired) if target.suffix == ".toml" else prune_json(target, retired)
    print(f"  {target.name}: retired removed={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
