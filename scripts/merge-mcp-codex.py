#!/usr/bin/env python3
"""Converge Hub-managed MCP servers into Codex config.toml."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
TABLE_RE = re.compile(r"^\s*\[mcp_servers\.([^\.\]\s]+)(?:\.(env|headers|http_headers))?\]\s*$")
ANY_TABLE_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")
MARKER = "# --- agent-hub shared MCP (managed) ---"


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value))


def parse_value(value: str) -> Any:
    try:
        return ast.literal_eval(value.strip())
    except (SyntaxError, ValueError):
        return value.strip()


def parse_servers(text: str) -> dict[str, dict[str, Any]]:
    servers: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    section: str | None = None
    for line in text.splitlines():
        table = TABLE_RE.match(line)
        if table:
            name, section = table.groups()
            current = servers.setdefault(name.lower(), {})
            if section:
                current.setdefault(section, {})
            continue
        if ANY_TABLE_RE.match(line):
            current = None
            section = None
            continue
        if current is None or "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if section:
            current[section][key] = parse_value(raw)
        else:
            current[key] = parse_value(raw)
    return servers


def remove_server_blocks(text: str, names: set[str]) -> str:
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        header = line.rstrip("\r\n")
        if ANY_TABLE_RE.match(header):
            table = TABLE_RE.match(header)
            skipping = bool(table and table.group(1).lower() in names)
        if not skipping:
            out.append(line)
    result = "".join(out).replace(MARKER + "\n", "")
    return re.sub(r"\n{3,}", "\n\n", result).rstrip() + "\n"


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def placeholder_name(value: Any) -> str | None:
    match = PLACEHOLDER_RE.match(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def mapping_for(template: dict[str, Any], existing: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for section in ("env", "headers"):
        template_values = template.get(section, {})
        existing_values = existing.get(section, {}) or existing.get("http_headers", {})
        if not isinstance(template_values, dict) or not isinstance(existing_values, dict):
            continue
        for key, value in template_values.items():
            variable = placeholder_name(value)
            concrete = existing_values.get(key)
            if variable and isinstance(concrete, str) and not is_placeholder(concrete):
                mapping[variable] = concrete
    return mapping


def resolve(value: Any, mapping: dict[str, str], existing: Any = None) -> Any | None:
    variable = placeholder_name(value)
    if variable:
        if variable in mapping:
            return mapping[variable]
        if variable in os.environ:
            return os.environ[variable]
        if existing is not None and not is_placeholder(existing):
            return existing
        return None
    return value


def render_server(name: str, template: dict[str, Any], existing: dict[str, Any]) -> str:
    mapping = mapping_for(template, existing)
    lines = [f"[mcp_servers.{name}]"]
    typ = template.get("type", "stdio")
    if typ == "http" or template.get("url"):
        url = resolve(template.get("url"), mapping, existing.get("url"))
        if not url:
            raise ValueError(f"unresolved URL for {name}")
        lines.extend((f'url = "{toml_escape(str(url))}"', ""))
        headers: dict[str, Any] = {}
        existing_headers = existing.get("headers", {}) or existing.get("http_headers", {})
        for key, value in (template.get("headers") or {}).items():
            resolved = resolve(value, mapping, existing_headers.get(key))
            if resolved is not None:
                headers[key] = resolved
        for key, value in existing_headers.items():
            headers.setdefault(key, value)
        if headers:
            lines.append(f"[mcp_servers.{name}.http_headers]")
            lines.extend(f'{key} = "{toml_escape(str(value))}"' for key, value in headers.items())
            lines.append("")
        return "\n".join(lines)

    command = template.get("command", "")
    if isinstance(existing.get("command"), str) and existing["command"].startswith("/"):
        command = existing["command"]
    command = resolve(command, mapping, existing.get("command"))
    if not command:
        raise ValueError(f"unresolved command for {name}")
    lines.append(f'command = "{toml_escape(str(command))}"')
    args: list[Any] = []
    existing_args = existing.get("args") if isinstance(existing.get("args"), list) else []
    for index, value in enumerate(template.get("args") or []):
        old = existing_args[index] if index < len(existing_args) else None
        resolved = resolve(value, mapping, old)
        if resolved is not None:
            args.append(resolved)
    args_text = ", ".join(f'"{toml_escape(str(value))}"' for value in args)
    lines.extend((f"args = [{args_text}]", ""))

    env: dict[str, Any] = {}
    existing_env = existing.get("env", {}) if isinstance(existing.get("env"), dict) else {}
    for key, value in (template.get("env") or {}).items():
        resolved = resolve(value, mapping, existing_env.get(key))
        if resolved is not None:
            env[key] = resolved
    for key, value in existing_env.items():
        env.setdefault(key, value)
    if env:
        lines.append(f"[mcp_servers.{name}.env]")
        lines.extend(f'{key} = "{toml_escape(str(value))}"' for key, value in env.items())
        lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <canonical.json> <config.toml>", file=sys.stderr)
        return 1
    canonical = json.loads(Path(sys.argv[1]).expanduser().read_text(encoding="utf-8"))
    templates = canonical.get("mcpServers", {})
    target = Path(sys.argv[2]).expanduser()
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    existing = parse_servers(text)
    names = {name.lower() for name in templates}
    base = remove_server_blocks(text, names) if text else ""
    blocks = [render_server(name, cfg, existing.get(name.lower(), {})) for name, cfg in templates.items()]
    output = base.rstrip()
    if blocks:
        output += ("\n\n" if output else "") + MARKER + "\n" + "\n".join(blocks)
    output = output.rstrip() + "\n"
    atomic_write(target, output)
    print(f"  config.toml: converged={len(blocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
