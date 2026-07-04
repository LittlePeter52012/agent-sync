#!/usr/bin/env python3
"""Append missing shared MCP servers to Codex config.toml (never overwrite existing)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def existing_server_names(toml_text: str) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(r"^\[mcp_servers\.([^\].]+)\]", toml_text, re.M)}


def toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value))


def donor_maps() -> dict[str, dict[str, str]]:
    """name.lower() -> {VAR: concrete value}"""
    out: dict[str, dict[str, str]] = {}
    for donor in (Path.home() / ".cursor" / "mcp.json", Path.home() / ".gemini" / "config" / "mcp_config.json"):
        if not donor.exists():
            continue
        data = json.loads(donor.read_text(encoding="utf-8"))
        for n, cfg in (data.get("mcpServers") or {}).items():
            m = out.setdefault(n.lower(), {})
            for k, v in (cfg.get("env") or {}).items():
                if isinstance(v, str) and not is_placeholder(v):
                    m[k] = v
                    if k == "OUTPUT_DIR":
                        m.setdefault("MINERU_OUTPUT_DIR", v)
                    if k == "OPENAPI_MCP_HEADERS":
                        m.setdefault("ANYTYPE_MCP_HEADERS", v)
            for a in cfg.get("args") or []:
                if isinstance(a, str) and ("Obsidian" in a or "KnowledgeBase" in a or "CloudStorage" in a):
                    m.setdefault("OBSIDIAN_VAULT", a)
    return out


def resolve(value: object, mapping: dict[str, str]) -> object | None:
    if is_placeholder(value):
        key = PLACEHOLDER_RE.match(value).group(1)  # type: ignore[union-attr]
        return mapping.get(key)
    return value


def render_server(name: str, cfg: dict, mapping: dict[str, str]) -> str | None:
    lines: list[str] = []
    t = cfg.get("type", "stdio")
    if t == "http" or "url" in cfg:
        url = resolve(cfg.get("url"), mapping)
        if not url:
            return None
        lines.append(f"[mcp_servers.{name}]")
        lines.append(f'url = "{toml_escape(str(url))}"')
        lines.append("")
        return "\n".join(lines)

    cmd = resolve(cfg.get("command", ""), mapping)
    if not cmd:
        return None
    lines.append(f"[mcp_servers.{name}]")
    lines.append(f'command = "{toml_escape(str(cmd))}"')
    args = []
    for a in cfg.get("args") or []:
        ra = resolve(a, mapping)
        if ra is None:
            continue
        args.append(ra)
    if args:
        args_lit = ", ".join(f'"{toml_escape(str(a))}"' for a in args)
        lines.append(f"args = [{args_lit}]")
    else:
        lines.append("args = []")
    lines.append("")

    env = {}
    for k, v in (cfg.get("env") or {}).items():
        rv = resolve(v, mapping)
        if rv is not None and not is_placeholder(rv):
            env[k] = rv
    # map MINERU_OUTPUT_DIR -> OUTPUT_DIR for mineru
    if "OUTPUT_DIR" not in env and "MINERU_OUTPUT_DIR" in mapping:
        env["OUTPUT_DIR"] = mapping["MINERU_OUTPUT_DIR"]
    if env:
        lines.append(f"[mcp_servers.{name}.env]")
        for k, v in env.items():
            lines.append(f'{k} = "{toml_escape(str(v))}"')
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <canonical.json> <config.toml>", file=sys.stderr)
        return 1

    canonical = json.loads(Path(sys.argv[1]).expanduser().read_text(encoding="utf-8"))
    target = Path(sys.argv[2]).expanduser()
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    have = existing_server_names(text)
    donors = donor_maps()

    added = 0
    blocks: list[str] = []
    for name, cfg in (canonical.get("mcpServers") or {}).items():
        if name.lower() in have:
            continue
        block = render_server(name, cfg, donors.get(name.lower(), {}))
        if not block:
            continue
        blocks.append(block)
        added += 1

    if not blocks:
        print("  config.toml: +0 (all shared servers already present)")
        return 0

    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n# --- agent-hub shared MCP (auto-appended) ---\n"
    text += "\n".join(blocks)
    target.write_text(text, encoding="utf-8")
    print(f"  config.toml: +{added} appended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
