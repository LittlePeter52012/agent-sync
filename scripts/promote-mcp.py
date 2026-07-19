#!/usr/bin/env python3
"""Promote one Agent tool's MCP configuration into the private Hub."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is expected
    tomllib = None


PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
SECRET_QUERY_KEYS = ("token", "secret", "password", "key", "auth")
CODEX_TABLE_RE = re.compile(
    r"^\s*\[mcp_servers\.([^\.\]\s]+)(?:\.(env|headers|http_headers))?\]\s*$"
)


class PromotionError(Exception):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromotionError(f"Source configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PromotionError(f"Source configuration is invalid JSON: {path.name}") from exc
    if not isinstance(data, dict):
        raise PromotionError(f"Source configuration must be an object: {path.name}")
    return data


def vscode_source(source: str, home: Path) -> tuple[str, Path, str]:
    user = home / "Library" / "Application Support" / "Code" / "User"
    if ":" in source:
        profile_id = source.split(":", 1)[1]
        if not profile_id or Path(profile_id).name != profile_id:
            raise PromotionError("Invalid VS Code Profile identifier")
        return f"vscode:{profile_id}", user / "profiles" / profile_id / "mcp.json", "vscode"

    profiles_root = user / "profiles"
    profiles = []
    if profiles_root.exists():
        profiles = sorted(
            path
            for path in profiles_root.iterdir()
            if path.is_dir() and path.name != "builtin" and (path / "mcp.json").exists()
        )
    if len(profiles) > 1:
        choices = ", ".join(f"vscode:{path.name}" for path in profiles)
        raise PromotionError(f"Multiple VS Code Profiles contain MCP configuration: {choices}")
    if len(profiles) == 1:
        return f"vscode:{profiles[0].name}", profiles[0] / "mcp.json", "vscode"
    return "vscode", user / "mcp.json", "vscode"


def locate_source(source: str, home: Path) -> tuple[str, Path, str]:
    source = source.lower()
    if source.startswith("vscode"):
        return vscode_source(source, home)
    sources = {
        "cursor": (home / ".cursor" / "mcp.json", "cursor"),
        "antigravity": (home / ".gemini" / "config" / "mcp_config.json", "cursor"),
        "gemini": (home / ".gemini" / "config" / "mcp_config.json", "cursor"),
        "opencode": (home / ".config" / "opencode" / "opencode.json", "opencode"),
        "codex": (home / ".codex" / "config.toml", "codex"),
        "claude": (home / ".claude.json", "cursor"),
    }
    if source not in sources:
        supported = "vscode, cursor, antigravity, opencode, codex, claude"
        raise PromotionError(f"Unknown source '{source}'. Supported sources: {supported}")
    path, kind = sources[source]
    return source, path, kind


def normalize_server(kind: str, cfg: dict[str, Any]) -> dict[str, Any]:
    if kind == "opencode":
        if cfg.get("type") == "remote" or cfg.get("url"):
            out = {"type": "http", "url": cfg.get("url")}
            if isinstance(cfg.get("headers"), dict):
                out["headers"] = cfg["headers"]
            return out
        command = cfg.get("command", [])
        if isinstance(command, list):
            out = {
                "type": "stdio",
                "command": command[0] if command else "",
                "args": command[1:],
            }
        else:
            out = {"type": "stdio", "command": command, "args": cfg.get("args", [])}
        environment = cfg.get("environment") or cfg.get("env")
        if isinstance(environment, dict):
            out["env"] = environment
        return out

    if cfg.get("type") in {"http", "sse"} or cfg.get("url"):
        out = {"type": "http", "url": cfg.get("url")}
        headers = cfg.get("headers") or cfg.get("http_headers")
        if isinstance(headers, dict):
            out["headers"] = headers
        return out

    out = {
        "type": "stdio",
        "command": cfg.get("command", ""),
        "args": cfg.get("args", []),
    }
    environment = cfg.get("env") or cfg.get("environment")
    if isinstance(environment, dict):
        out["env"] = environment
    return out


def parse_codex_servers(text: str) -> dict[str, dict[str, Any]]:
    servers: dict[str, dict[str, Any]] = {}
    current: Optional[dict[str, Any]] = None
    section: Optional[str] = None
    for line in text.splitlines():
        table = CODEX_TABLE_RE.match(line)
        if table:
            name, section = table.groups()
            current = servers.setdefault(name, {})
            if section:
                normalized_section = "headers" if section == "http_headers" else section
                section = normalized_section
                current.setdefault(section, {})
            continue
        if current is None or "=" not in line or line.lstrip().startswith("#"):
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
        if section:
            current[section][key.strip()] = value
        else:
            current[key.strip()] = value
    return servers


def config_enabled(config: dict[str, Any]) -> bool:
    return (
        config.get("enabled", True) is not False
        and config.get("disabled", False) is not True
    )


def load_source(source: str, home: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    label, path, kind = locate_source(source, home)
    if kind == "codex":
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise PromotionError(f"Source configuration not found: {path}") from exc
        if tomllib is None:
            raw = parse_codex_servers(text)
        else:
            try:
                data = tomllib.loads(text)
            except Exception as exc:
                raise PromotionError("Source Codex configuration is invalid TOML") from exc
            raw = data.get("mcp_servers", {})
    else:
        data = load_json(path)
        bucket = {"vscode": "servers", "opencode": "mcp"}.get(kind, "mcpServers")
        raw = data.get(bucket, {})
    if not isinstance(raw, dict) or not raw:
        raise PromotionError("Source MCP configuration is empty; Hub was not changed")
    servers = {
        str(name): normalize_server(kind, cfg)
        for name, cfg in raw.items()
        if isinstance(cfg, dict) and config_enabled(cfg)
    }
    if not servers:
        raise PromotionError("Source MCP configuration is empty; Hub was not changed")
    return label, servers


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value))


def safe_variable(*parts: str) -> str:
    value = "_".join(parts).upper()
    value = re.sub(r"[^A-Z0-9_]", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value or value[0].isdigit():
        value = "VALUE_" + value
    return value


def sanitize_mapping(
    server_name: str,
    field: str,
    values: dict[str, Any],
    old_values: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        old_value = old_values.get(key)
        if is_placeholder(old_value):
            out[str(key)] = old_value
        elif is_placeholder(value):
            out[str(key)] = value
        elif field == "env":
            out[str(key)] = "${" + safe_variable(str(key)) + "}"
        else:
            out[str(key)] = "${AGENT_SYNC_" + safe_variable(server_name, str(key)) + "}"
    return out


def validate_url(url: Any) -> None:
    if not isinstance(url, str):
        return
    for key, _value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if any(marker in key.lower() for marker in SECRET_QUERY_KEYS):
            raise PromotionError("Source URL contains a credential-like query parameter")


def sanitize_server(name: str, cfg: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(cfg))
    validate_url(out.get("url"))
    for field in ("env", "headers"):
        values = out.get(field)
        if isinstance(values, dict):
            old_values = old.get(field) if isinstance(old.get(field), dict) else {}
            out[field] = sanitize_mapping(name, field, values, old_values)
    return out


def read_canonical(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = load_json(path)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise PromotionError("Hub mcp/shared-servers.json has no mcpServers object")
    return {str(name): cfg for name, cfg in servers.items() if isinstance(cfg, dict)}


def build_plan(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    old_names = set(old)
    new_names = set(new)
    return {
        "added": sorted(new_names - old_names, key=str.lower),
        "changed": sorted(
            (name for name in old_names & new_names if old[name] != new[name]), key=str.lower
        ),
        "removed": sorted(old_names - new_names, key=str.lower),
        "unchanged": sorted(
            (name for name in old_names & new_names if old[name] == new[name]), key=str.lower
        ),
    }


def read_retired(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromotionError("Hub retired-servers.json is invalid JSON") from exc
    values = data.get("retiredServers", [])
    return {str(value) for value in values} if isinstance(values, list) else set()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        Path(tmp_name).replace(path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def ensure_minimal_hub(hub: Path) -> None:
    for directory in (hub / "skills", hub / "mcp", hub / "rules"):
        directory.mkdir(parents=True, exist_ok=True)
    manifest = hub / "manifest.yaml"
    if not manifest.exists():
        manifest.write_text("version: 1\nname: my-agent-hub\nskills: []\n", encoding="utf-8")


def apply_promotion(
    hub: Path,
    canonical_path: Path,
    servers: dict[str, Any],
    plan: dict[str, list[str]],
) -> None:
    ensure_minimal_hub(hub)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if canonical_path.exists():
        backup = hub / ".sync-backups" / stamp / "mcp" / canonical_path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical_path, backup)

    retired_path = hub / "mcp" / "retired-servers.json"
    retired = read_retired(retired_path)
    retired.update(plan["removed"])
    retired.difference_update(servers)
    atomic_json(canonical_path, {"mcpServers": servers})
    atomic_json(retired_path, {"retiredServers": sorted(retired, key=str.lower)})


def render_plan(label: str, plan: dict[str, list[str]]) -> str:
    labels = {
        "added": "新增",
        "changed": "修改",
        "removed": "删除",
        "unchanged": "不变",
    }
    lines = ["Agent Sync MCP 提升计划", f"来源: {label}"]
    for key in ("added", "changed", "removed", "unchanged"):
        names = ", ".join(plan[key]) if plan[key] else "—"
        lines.append(f"{labels[key]}: {names}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--hub", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--yes", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        label, raw_servers = load_source(args.source, args.home)
        canonical_path = args.hub / "mcp" / "shared-servers.json"
        old_servers = read_canonical(canonical_path)
        servers = {
            name: sanitize_server(name, cfg, old_servers.get(name, {}))
            for name, cfg in raw_servers.items()
        }
        plan = build_plan(old_servers, servers)
        print(render_plan(label, plan))
        if args.dry_run:
            return 0
        if not args.yes:
            answer = input("采用此来源并同步全部 Agent？[y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("已取消。")
                return 1
        apply_promotion(args.hub, canonical_path, servers, plan)
        print("Hub MCP 已更新。")
        return 0
    except PromotionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
