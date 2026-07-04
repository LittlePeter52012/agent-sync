#!/usr/bin/env python3
"""Merge canonical MCP servers into tool-specific configs.

Supports:
  - Cursor / Antigravity: { "mcpServers": { name: {...} } }
  - VS Code:              { "servers": { name: {...} } }
  - OpenCode:             { "mcp": { name: { type, command[], ... } } } inside opencode.json

Preserves tool-specific servers and existing secrets. Placeholder values
like ${VAR} are filled from donor configs (Cursor / Antigravity) when available.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


DONORS = [
    Path.home() / ".cursor" / "mcp.json",
    Path.home() / ".gemini" / "config" / "mcp_config.json",
]

PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def extract_servers(doc: dict[str, Any]) -> dict[str, Any]:
    for key in ("mcpServers", "servers", "mcp"):
        if isinstance(doc.get(key), dict):
            return doc[key]
    return {}


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value))


def collect_donors() -> dict[str, dict[str, Any]]:
    """Lowercase name -> best donor server config."""
    out: dict[str, dict[str, Any]] = {}
    for donor in DONORS:
        for name, cfg in extract_servers(load_json(donor)).items():
            key = name.lower()
            # Prefer configs that already have concrete secrets/paths
            if key not in out:
                out[key] = cfg
            else:
                # merge env from later donors if missing
                prev = out[key]
                prev_env = prev.get("env") or prev.get("environment") or {}
                cur_env = cfg.get("env") or cfg.get("environment") or {}
                for ek, ev in cur_env.items():
                    if ek not in prev_env and not is_placeholder(ev):
                        prev_env = {**prev_env, ek: ev}
                if prev_env:
                    prev = {**prev, "env": prev_env}
                    out[key] = prev
    return out


def donor_lookup_map(donor_cfg: dict[str, Any]) -> dict[str, str]:
    """Build ${VAR} -> concrete value map from a donor server config."""
    mapping: dict[str, str] = {}
    env = donor_cfg.get("env") or donor_cfg.get("environment") or {}
    for k, v in env.items():
        if isinstance(v, str) and not is_placeholder(v):
            mapping[k] = v
            # common aliases
            if k == "MINERU_API_TOKEN":
                mapping.setdefault("MINERU_API_TOKEN", v)
            if k == "OUTPUT_DIR":
                mapping.setdefault("MINERU_OUTPUT_DIR", v)
            if k == "OPENAPI_MCP_HEADERS":
                mapping.setdefault("ANYTYPE_MCP_HEADERS", v)

    # Obsidian vault is usually the last arg
    args = donor_cfg.get("args") or []
    if isinstance(args, list):
        for a in args:
            if isinstance(a, str) and ("Obsidian" in a or "KnowledgeBase" in a or a.startswith("/")):
                if "obsidian" in a.lower() or "KnowledgeBase" in a or "CloudStorage" in a:
                    mapping.setdefault("OBSIDIAN_VAULT", a)
        # if command is npx mcp-obsidian, last path-like arg
        for a in reversed(args):
            if isinstance(a, str) and a.startswith("/") and not a.startswith("/usr") and "npx" not in a:
                mapping.setdefault("OBSIDIAN_VAULT", a)
                break

    # OpenCode uses command as array
    cmd = donor_cfg.get("command")
    if isinstance(cmd, list) and len(cmd) >= 2:
        for a in reversed(cmd[1:]):
            if isinstance(a, str) and (
                "Obsidian" in a or "KnowledgeBase" in a or "CloudStorage" in a
            ):
                mapping.setdefault("OBSIDIAN_VAULT", a)

    return mapping


def resolve_value(value: Any, mapping: dict[str, str], existing: Any = None) -> Any:
    if existing is not None and not is_placeholder(existing) and existing not in ("", [], {}):
        # Prefer existing concrete values
        if isinstance(existing, str) and existing.startswith("${"):
            pass
        else:
            return existing
    if is_placeholder(value):
        key = PLACEHOLDER_RE.match(value).group(1)  # type: ignore[union-attr]
        return mapping.get(key, existing if existing is not None else None)
    return value


def resolve_env(template_env: dict[str, Any], existing_env: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for k, v in template_env.items():
        resolved = resolve_value(v, mapping, existing_env.get(k))
        if resolved is not None and not is_placeholder(resolved):
            merged[k] = resolved
    for k, v in existing_env.items():
        if k not in merged and not is_placeholder(v):
            merged[k] = v
    return merged


def resolve_args(template_args: list[Any] | None, existing_args: list[Any] | None, mapping: dict[str, str]) -> list[Any]:
    if existing_args and not any(is_placeholder(a) for a in existing_args):
        # Keep existing concrete args (paths/secrets already configured)
        return list(existing_args)
    args = list(template_args or [])
    out = []
    for i, a in enumerate(args):
        ex = existing_args[i] if existing_args and i < len(existing_args) else None
        resolved = resolve_value(a, mapping, ex)
        if resolved is None:
            continue
        out.append(resolved)
    return out


def resolve_command(template_cmd: Any, existing_cmd: Any, mapping: dict[str, str]) -> Any:
    if existing_cmd and not is_placeholder(existing_cmd):
        # Keep absolute paths already working on this machine
        if isinstance(existing_cmd, str) and existing_cmd.startswith("/"):
            return existing_cmd
        if isinstance(existing_cmd, list):
            return existing_cmd
    return resolve_value(template_cmd, mapping, existing_cmd)


def to_cursor_shape(template: dict[str, Any], existing: dict[str, Any] | None, mapping: dict[str, str]) -> dict[str, Any]:
    out = json.loads(json.dumps(existing or {}))
    t = template.get("type", "stdio")
    if t == "http" or "url" in template:
        out["type"] = out.get("type") or "http"
        out["url"] = resolve_value(template.get("url"), mapping, out.get("url"))
        if template.get("headers") is not None and "headers" not in out:
            out["headers"] = template.get("headers", {})
    else:
        out["type"] = out.get("type") or template.get("type", "stdio")
        out["command"] = resolve_command(template.get("command"), out.get("command"), mapping)
        out["args"] = resolve_args(template.get("args"), out.get("args"), mapping)
    env = resolve_env(template.get("env", {}), out.get("env", {}), mapping)
    if env:
        out["env"] = env
    return out


def to_vscode_shape(template: dict[str, Any], existing: dict[str, Any] | None, mapping: dict[str, str]) -> dict[str, Any]:
    out = json.loads(json.dumps(existing or {}))
    t = template.get("type", "stdio")
    if t == "http" or "url" in template:
        out["type"] = "http"
        out["url"] = resolve_value(template.get("url"), mapping, out.get("url"))
        if template.get("headers") is not None:
            out.setdefault("headers", template.get("headers", {}))
    else:
        out.pop("type", None)
        out["command"] = resolve_command(template.get("command"), out.get("command"), mapping)
        out["args"] = resolve_args(template.get("args"), out.get("args"), mapping)
    env = resolve_env(template.get("env", {}), out.get("env", {}), mapping)
    if env:
        out["env"] = env
    return out


def to_opencode_shape(template: dict[str, Any], existing: dict[str, Any] | None, mapping: dict[str, str]) -> dict[str, Any]:
    out = json.loads(json.dumps(existing or {}))
    t = template.get("type", "stdio")
    if t == "http" or "url" in template:
        out["type"] = "remote"
        out["url"] = resolve_value(template.get("url"), mapping, out.get("url"))
        out["enabled"] = out.get("enabled", True)
        if template.get("headers"):
            out.setdefault("headers", template["headers"])
    else:
        out["type"] = "local"
        existing_cmd = out.get("command")
        if isinstance(existing_cmd, list) and existing_cmd and not any(is_placeholder(x) for x in existing_cmd):
            command = existing_cmd
        else:
            cmd = template.get("command", "")
            args = resolve_args(template.get("args"), None, mapping)
            if isinstance(cmd, list):
                command = [resolve_value(c, mapping) for c in cmd]
            else:
                command = [resolve_value(cmd, mapping)] + list(args)
            command = [c for c in command if c is not None and c != ""]
        out["command"] = command
        out["enabled"] = out.get("enabled", True)
        env = resolve_env(
            template.get("env", {}),
            out.get("environment") or out.get("env") or {},
            mapping,
        )
        if env:
            out["environment"] = env
        out.pop("env", None)
    return out


def detect_format(path: Path, doc: dict[str, Any]) -> str:
    name = path.name.lower()
    if name == "opencode.json" or ("plugin" in doc and "model" in doc):
        return "opencode"
    if "servers" in doc and "mcpServers" not in doc:
        return "vscode"
    if path.name == "mcp.json" and "Application Support/Code" in str(path):
        return "vscode"
    return "cursor"


def merge_into(target_path: Path, canonical_path: Path) -> dict[str, int]:
    canonical = load_json(canonical_path)
    templates = canonical.get("mcpServers", {})
    donors = collect_donors()

    target_path = target_path.expanduser()
    doc = load_json(target_path) if target_path.exists() else {}
    fmt = detect_format(target_path, doc)

    if fmt == "opencode":
        bucket_key = "mcp"
        converter = to_opencode_shape
        if not doc:
            doc = {"$schema": "https://opencode.ai/config.json"}
    elif fmt == "vscode":
        bucket_key = "servers"
        converter = to_vscode_shape
        if not doc:
            doc = {"servers": {}, "inputs": []}
    else:
        bucket_key = "mcpServers"
        converter = to_cursor_shape
        if not doc:
            doc = {"mcpServers": {}}

    servers = doc.setdefault(bucket_key, {})
    lower_map = {k.lower(): k for k in servers}

    stats = {"added": 0, "updated": 0, "unchanged": 0}
    for name, template in templates.items():
        existing_key = lower_map.get(name.lower(), name)
        existing = servers.get(existing_key)
        before = json.dumps(existing, sort_keys=True) if existing is not None else None
        donor_cfg = donors.get(name.lower(), {})
        mapping = donor_lookup_map(donor_cfg)
        # also map from existing
        if existing:
            mapping = {**mapping, **donor_lookup_map(existing)}
        new_cfg = converter(template, existing, mapping)
        # Drop unresolved placeholders
        new_cfg = json.loads(json.dumps(new_cfg))
        if "env" in new_cfg:
            new_cfg["env"] = {k: v for k, v in new_cfg["env"].items() if not is_placeholder(v)}
        if "environment" in new_cfg:
            new_cfg["environment"] = {
                k: v for k, v in new_cfg["environment"].items() if not is_placeholder(v)
            }
        if "args" in new_cfg:
            new_cfg["args"] = [a for a in new_cfg["args"] if not is_placeholder(a)]
        servers[existing_key] = new_cfg
        after = json.dumps(new_cfg, sort_keys=True)
        if before is None:
            stats["added"] += 1
        elif before != after:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stats


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <canonical.json> <target.json>", file=sys.stderr)
        return 1
    stats = merge_into(Path(sys.argv[2]), Path(sys.argv[1]))
    print(f"  {Path(sys.argv[2]).name}: +{stats['added']} updated={stats['updated']} unchanged={stats['unchanged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
