#!/usr/bin/env python3
"""Read-only product plugin discovery with fail-safe evidence handling."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class PluginSource:
    target: str
    plugin_id: str
    evidence: str
    root: Path
    skills: frozenset[str]
    mcp_servers: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class PluginDiscovery:
    sources: tuple[PluginSource, ...] = ()
    uncertain: tuple[str, ...] = ()


def normalize_plugin_id(value: str) -> str:
    """Remove a version suffix while preserving scoped package identity."""
    normalized = value.strip().casefold()
    if normalized.startswith("@"):
        marker = normalized.rfind("@")
        return normalized if marker == 0 else normalized[:marker]
    return normalized.split("@", 1)[0]


def _safe_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _declared(value: str, declared_ids: frozenset[str]) -> bool:
    if "*" in declared_ids:
        return True
    normalized = value.strip().casefold()
    short = normalize_plugin_id(normalized)
    declared = {item.casefold() for item in declared_ids}
    declared_short = {normalize_plugin_id(item) for item in declared}
    return normalized in declared or short in declared or short in declared_short


def _enabled(config: Mapping[str, Any]) -> bool:
    return config.get("enabled", True) is not False and config.get("disabled") is not True


def _read_json(path: Path) -> Any | None:
    try:
        return _safe_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None


def _manifest_source(
    target: str,
    plugin_id: str,
    evidence: str,
    root: Path,
) -> PluginSource:
    skills: set[str] = set()
    skills_root = root / "skills"
    if skills_root.is_dir():
        for skill_file in skills_root.glob("*/SKILL.md"):
            skills.add(skill_file.parent.name.casefold())

    servers: dict[str, Mapping[str, Any]] = {}
    for config_path in (
        root / ".mcp.json",
        root / "mcp.json",
        root / ".cursor" / "mcp.json",
    ):
        document = _read_json(config_path)
        if not isinstance(document, dict):
            continue
        bucket = document.get("mcpServers", document.get("servers", {}))
        if not isinstance(bucket, dict):
            continue
        for name, config in bucket.items():
            if isinstance(name, str) and isinstance(config, dict) and _enabled(config):
                servers[name] = config

    return PluginSource(
        target=target,
        plugin_id=plugin_id,
        evidence=evidence,
        root=root,
        skills=frozenset(skills),
        mcp_servers=servers,
    )


def _run_json(
    command: list[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> Any | None:
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return _safe_json(completed.stdout)


def _records(payload: Any, *keys: str) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _cursor_log_plugins(home: Path) -> dict[str, str]:
    log_root = home / "Library" / "Application Support" / "Cursor" / "logs"
    logs = list(log_root.glob("**/Cursor Plugins.log")) if log_root.exists() else []
    if not logs:
        return {}
    try:
        newest = max(logs, key=lambda path: path.stat().st_mtime)
        content = newest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    pattern = re.compile(r"Adding enabled plugin:\s+([^\s]+)\s+from\s+([^\s]+)\s+at\b")
    return {match.group(1): match.group(2) for match in pattern.finditer(content)}


def _cursor(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    del runner
    sources: list[PluginSource] = []
    local_root = home / ".cursor" / "plugins" / "local"
    if local_root.is_dir():
        for root in sorted(path for path in local_root.iterdir() if path.is_dir()):
            if _declared(root.name, declared_ids):
                sources.append(
                    _manifest_source(target, root.name, "cursor-local", root)
                )

    cache = home / ".cursor" / "plugins" / "cache"
    for name, revision in sorted(_cursor_log_plugins(home).items()):
        if not _declared(name, declared_ids):
            continue
        matches = sorted(cache.glob(f"*/{name}/{revision}"))
        if matches:
            sources.append(
                _manifest_source(target, name, "cursor-log", matches[0])
            )
    return PluginDiscovery(tuple(sources))


def _codex(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    del home
    payload = _run_json(["codex", "plugin", "list", "--json"], runner)
    records = _records(payload, "installed", "plugins")
    if records is None:
        return PluginDiscovery(uncertain=("codex:plugin-state-uncertain",))
    sources: list[PluginSource] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        plugin_id = record.get("pluginId", record.get("id"))
        if not isinstance(plugin_id, str) or not _declared(plugin_id, declared_ids):
            continue
        if record.get("installed") is not True or record.get("enabled") is not True:
            continue
        source_info = record.get("source", {})
        path = source_info.get("path") if isinstance(source_info, dict) else None
        if not isinstance(path, str) or not Path(path).expanduser().is_dir():
            return PluginDiscovery(uncertain=("codex:plugin-state-uncertain",))
        sources.append(
            _manifest_source(target, plugin_id, "codex-cli", Path(path).expanduser())
        )
    return PluginDiscovery(tuple(sources))


def _claude(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    del home
    payload = _run_json(["claude", "plugin", "list", "--json"], runner)
    records = _records(payload, "installed", "plugins")
    if records is None:
        return PluginDiscovery(uncertain=("claude:plugin-state-uncertain",))
    sources: list[PluginSource] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        plugin_id = record.get("id", record.get("pluginId"))
        if not isinstance(plugin_id, str) or not _declared(plugin_id, declared_ids):
            continue
        if record.get("enabled") is not True:
            continue
        install_path = record.get("installPath")
        if not isinstance(install_path, str) or not Path(install_path).expanduser().is_dir():
            return PluginDiscovery(uncertain=("claude:plugin-state-uncertain",))
        sources.append(
            _manifest_source(
                target,
                plugin_id,
                "claude-cli",
                Path(install_path).expanduser(),
            )
        )
    return PluginDiscovery(tuple(sources))


def _gemini(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    del home
    payload = _run_json(
        ["gemini", "extensions", "list", "--output-format", "json"], runner
    )
    records = _records(payload, "extensions", "installed")
    if records is None:
        return PluginDiscovery(uncertain=("gemini:plugin-state-uncertain",))
    sources: list[PluginSource] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        plugin_id = record.get("name", record.get("id"))
        if not isinstance(plugin_id, str) or not _declared(plugin_id, declared_ids):
            continue
        if "enabled" not in record:
            return PluginDiscovery(uncertain=("gemini:plugin-state-uncertain",))
        if record.get("enabled") is not True:
            continue
        path = record.get("path", record.get("installPath"))
        if not isinstance(path, str) or not Path(path).expanduser().is_dir():
            return PluginDiscovery(uncertain=("gemini:plugin-state-uncertain",))
        sources.append(
            _manifest_source(
                target, plugin_id, "gemini-cli", Path(path).expanduser()
            )
        )
    return PluginDiscovery(tuple(sources))


def _opencode(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    del runner
    config_path = home / ".config" / "opencode" / "opencode.json"
    if not config_path.exists():
        return PluginDiscovery()
    document = _read_json(config_path)
    if not isinstance(document, dict):
        return PluginDiscovery(uncertain=("opencode:plugin-state-uncertain",))
    plugins = document.get("plugin", [])
    if not isinstance(plugins, list):
        return PluginDiscovery(uncertain=("opencode:plugin-state-uncertain",))
    sources = []
    for value in plugins:
        if not isinstance(value, str) or not _declared(value, declared_ids):
            continue
        plugin_id = normalize_plugin_id(value)
        sources.append(
            _manifest_source(target, plugin_id, "opencode-config", config_path.parent)
        )
    return PluginDiscovery(tuple(sources))


def _antigravity_manifest_root(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    del runner
    product = "antigravity" if target == "antigravity-app" else "antigravity-cli"
    root = home / ".gemini" / product / "plugins"
    sources: list[PluginSource] = []
    if not root.is_dir():
        return PluginDiscovery()
    for manifest_path in sorted(root.glob("*/plugin.json")):
        record = _read_json(manifest_path)
        if not isinstance(record, dict):
            continue
        plugin_id = record.get("name", record.get("id"))
        if not isinstance(plugin_id, str) or not _declared(plugin_id, declared_ids):
            continue
        if record.get("disabled") is True or record.get("enabled") is False:
            continue
        sources.append(
            _manifest_source(target, plugin_id, f"{target}-manifest", manifest_path.parent)
        )
    return PluginDiscovery(tuple(sources))


def _disabled_extensions(database: Path) -> set[str] | None:
    if not database.exists():
        return set()
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT value FROM ItemTable "
                "WHERE key = 'extensionsIdentifiers/disabled'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None:
        return set()
    payload = _safe_json(row[0]) if isinstance(row[0], str) else None
    if not isinstance(payload, list):
        return None
    disabled: set[str] = set()
    for item in payload:
        if isinstance(item, str):
            disabled.add(normalize_plugin_id(item))
        elif isinstance(item, dict):
            value = item.get("id", item.get("identifier"))
            if isinstance(value, str):
                disabled.add(normalize_plugin_id(value))
    return disabled


def _extension_ids(stdout: str) -> set[str]:
    return {
        normalize_plugin_id(line)
        for line in stdout.splitlines()
        if line.strip() and not line.lstrip().startswith(("[", "WARNING", "Error"))
    }


def _run_extensions(
    command: list[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> set[str] | None:
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return _extension_ids(completed.stdout)


def _vscode(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    profile = target.split(":", 1)[1] if ":" in target else None
    user_root = home / "Library" / "Application Support" / "Code" / "User"
    command = ["code"]
    if profile:
        profile_root = user_root / "profiles" / profile
        if not profile_root.is_dir():
            return PluginDiscovery(
                uncertain=(f"vscode:{profile}:plugin-state-uncertain",)
            )
        command.extend(["--profile", profile])
        database = profile_root / "globalStorage" / "state.vscdb"
    else:
        database = user_root / "globalStorage" / "state.vscdb"
    command.extend(["--list-extensions", "--show-versions"])
    installed = _run_extensions(command, runner)
    disabled = _disabled_extensions(database)
    label = f"vscode:{profile}" if profile else "vscode"
    if installed is None or disabled is None:
        return PluginDiscovery(uncertain=(f"{label}:plugin-state-uncertain",))
    sources = tuple(
        _manifest_source(target, plugin_id, "vscode-cli", user_root)
        for plugin_id in sorted(installed - disabled)
        if _declared(plugin_id, declared_ids)
    )
    return PluginDiscovery(sources)


def _antigravity_ide(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> PluginDiscovery:
    command = [
        "/Applications/Antigravity IDE.app/Contents/Resources/app/bin/antigravity-ide",
        "--list-extensions",
        "--show-versions",
    ]
    installed = _run_extensions(command, runner)
    database = (
        home
        / "Library"
        / "Application Support"
        / "Antigravity IDE"
        / "User"
        / "globalStorage"
        / "state.vscdb"
    )
    disabled = _disabled_extensions(database)
    if installed is None or disabled is None:
        return PluginDiscovery(
            uncertain=("antigravity-ide:plugin-state-uncertain",)
        )
    root = home / ".antigravity-ide" / "extensions"
    sources = tuple(
        _manifest_source(target, plugin_id, "antigravity-ide-cli", root)
        for plugin_id in sorted(installed - disabled)
        if _declared(plugin_id, declared_ids)
    )
    return PluginDiscovery(sources)


_ADAPTERS = {
    "cursor": _cursor,
    "codex": _codex,
    "claude": _claude,
    "gemini": _gemini,
    "gemini-global": _gemini,
    "opencode": _opencode,
    "antigravity-app": _antigravity_manifest_root,
    "antigravity-cli": _antigravity_manifest_root,
    "antigravity-ide": _antigravity_ide,
    "vscode": _vscode,
}


def discover_plugins(
    target: str,
    home: Path,
    declared_ids: frozenset[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> PluginDiscovery:
    """Discover only reviewed plugin IDs for one target without modifying it."""
    if not declared_ids:
        return PluginDiscovery()
    target_name = target.casefold()
    adapter = _ADAPTERS.get(target_name.split(":", 1)[0])
    if adapter is None:
        return PluginDiscovery(
            uncertain=(f"{target_name}:unsupported-plugin-adapter",)
        )
    try:
        return adapter(target_name, home.expanduser(), declared_ids, runner)
    except Exception:
        return PluginDiscovery(
            uncertain=(f"{target_name}:plugin-state-uncertain",)
        )
