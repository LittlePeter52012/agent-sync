#!/usr/bin/env python3
"""Resolve effective Skill and MCP ownership for one Agent target.

The resolver treats an installed-and-enabled product plugin as the owner of
capabilities it actually exposes. Cached marketplace payloads alone are not
evidence that a plugin is active.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class McpIdentity:
    transport: str
    endpoint: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class PluginSource:
    target: str
    plugin_id: str
    evidence: str
    root: Path
    skills: frozenset[str]
    mcp_servers: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class CapabilityDecision:
    capability: str
    target: str
    selection: str
    desired_owner: str
    effective_owner: str | None
    state: str
    reason: str
    suppressed_skills: frozenset[str]
    suppressed_mcp: frozenset[str]
    fallback_owner: str | None


@dataclass(frozen=True)
class TargetResolution:
    suppressed_skills: frozenset[str]
    suppressed_mcp: frozenset[str]
    skill_owners: Mapping[str, str]
    mcp_owners: Mapping[str, str]
    mcp_collisions: frozenset[str]
    active_plugins: tuple[PluginSource, ...]
    capabilities: Mapping[str, CapabilityDecision] = field(default_factory=dict)
    findings: tuple[str, ...] = ()
    uncertain: tuple[str, ...] = ()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _enabled(config: Mapping[str, Any]) -> bool:
    return config.get("enabled", True) is not False and config.get("disabled") is not True


def normalize_mcp_identity(config: Mapping[str, Any]) -> McpIdentity:
    """Return a secret-free transport identity for an MCP configuration."""
    url = config.get("url")
    if isinstance(url, str) and url.strip():
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        hostname = (parts.hostname or "").lower()
        port = f":{parts.port}" if parts.port else ""
        path = parts.path.rstrip("/") or ""
        endpoint = urlunsplit((scheme, hostname + port, path, "", ""))
        transport = str(config.get("type") or "http").casefold()
        if transport in {"remote", "sse"}:
            transport = "http" if transport == "remote" else transport
        return McpIdentity(transport, endpoint, ())

    command = config.get("command", "")
    command_args: Sequence[Any]
    if isinstance(command, list):
        endpoint = os.path.basename(str(command[0])) if command else ""
        command_args = [*command[1:], *config.get("args", [])]
    else:
        endpoint = os.path.basename(str(command))
        raw_args = config.get("args", [])
        command_args = raw_args if isinstance(raw_args, list) else []

    args = tuple(
        "<absolute-path>" if os.path.isabs(str(value)) else str(value)
        for value in command_args
    )
    return McpIdentity("stdio", endpoint, args)


def mcp_identity_matches(
    actual: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> bool:
    """Compare transport shape while treating canonical placeholders as slots."""
    actual_identity = normalize_mcp_identity(actual)
    canonical_identity = normalize_mcp_identity(canonical)
    if (
        actual_identity.transport != canonical_identity.transport
        or actual_identity.endpoint != canonical_identity.endpoint
        or len(actual_identity.args) != len(canonical_identity.args)
    ):
        return False
    for actual_arg, canonical_arg in zip(
        actual_identity.args,
        canonical_identity.args,
    ):
        if re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", canonical_arg):
            continue
        if actual_arg != canonical_arg:
            return False
    return True


def _plugin_capabilities(
    target: str,
    plugin_id: str,
    evidence: str,
    root: Path,
) -> PluginSource:
    skills: set[str] = set()
    skills_root = root / "skills"
    if skills_root.exists():
        for skill_file in skills_root.glob("*/SKILL.md"):
            skills.add(skill_file.parent.name.casefold())

    servers: dict[str, Mapping[str, Any]] = {}
    candidates = (
        root / ".mcp.json",
        root / "mcp.json",
        root / ".cursor" / "mcp.json",
    )
    for config_path in candidates:
        document = _load_json(config_path)
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


def _cursor_log_plugins(home: Path) -> dict[str, str]:
    log_root = home / "Library" / "Application Support" / "Cursor" / "logs"
    logs = list(log_root.glob("**/Cursor Plugins.log")) if log_root.exists() else []
    if not logs:
        return {}
    try:
        newest = max(logs, key=lambda path: path.stat().st_mtime)
        text = newest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    pattern = re.compile(
        r"Adding enabled plugin:\s+([^\s]+)\s+from\s+([^\s]+)\s+at\b"
    )
    active: dict[str, str] = {}
    for match in pattern.finditer(text):
        active[match.group(1)] = match.group(2)
    return active


def _cursor_sources(home: Path) -> list[PluginSource]:
    sources: list[PluginSource] = []
    local_root = home / ".cursor" / "plugins" / "local"
    if local_root.exists():
        for root in sorted(path for path in local_root.iterdir() if path.is_dir()):
            source = _plugin_capabilities("cursor", root.name, "cursor-local", root)
            if source.skills or source.mcp_servers:
                sources.append(source)

    cache = home / ".cursor" / "plugins" / "cache"
    for name, revision in sorted(_cursor_log_plugins(home).items()):
        matches = list(cache.glob(f"*/{name}/{revision}"))
        if not matches:
            continue
        source = _plugin_capabilities(
            "cursor",
            name,
            "cursor-log",
            sorted(matches)[0],
        )
        if source.skills or source.mcp_servers:
            sources.append(source)
    return sources


def _codex_sources(
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> list[PluginSource]:
    try:
        completed = runner(
            ["codex", "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    records = payload.get("installed", []) if isinstance(payload, dict) else []
    sources: list[PluginSource] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("installed") is not True or record.get("enabled") is not True:
            continue
        source_info = record.get("source", {})
        path = source_info.get("path") if isinstance(source_info, dict) else None
        plugin_id = record.get("pluginId")
        if not isinstance(path, str) or not isinstance(plugin_id, str):
            continue
        root = Path(path).expanduser()
        source = _plugin_capabilities("codex", plugin_id, "codex-cli", root)
        if source.skills or source.mcp_servers:
            sources.append(source)
    return sources


def active_plugin_sources(
    target: str,
    home: Path | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[PluginSource]:
    """Discover active plugin capabilities for a supported target."""
    resolved_home = (home or Path.home()).expanduser()
    target_name = target.casefold()
    if target_name == "cursor":
        return _cursor_sources(resolved_home)
    if target_name == "codex":
        return _codex_sources(runner)
    return []


def _manifest_skills(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    in_skills = False
    skills: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not line.startswith((" ", "\t")) and stripped.endswith(":"):
            in_skills = stripped == "skills:"
            continue
        if in_skills:
            match = re.match(r"^\s*-\s+([^\s#]+)", line)
            if match:
                skills.append(match.group(1).casefold())
            elif stripped and not line.startswith((" ", "\t")):
                break
    return skills


def _plugin_policy_matches(policy_id: str, source_id: str) -> bool:
    return _normalized_plugin_id(policy_id) in {
        source_id.casefold(),
        _normalized_plugin_id(source_id),
    }


def _normalized_plugin_id(value: str) -> str:
    normalized = value.casefold()
    if normalized.startswith("@"):
        version_marker = normalized.rfind("@")
        return normalized if version_marker == 0 else normalized[:version_marker]
    return normalized.split("@", 1)[0]


def _capability_contract(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    value = policy.get("capability_ownership", {})
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        return None
    return [item.casefold() for item in value]


def _capability_target_policy(
    capability: Mapping[str, Any],
    target_name: str,
    target_family: str,
) -> Mapping[str, Any]:
    targets = capability.get("targets", {})
    if not isinstance(targets, dict):
        return {}
    value = targets.get(target_name, targets.get(target_family, {}))
    return value if isinstance(value, dict) else {}


def _active_official_source(
    official_plugins: Sequence[str],
    sources: Sequence[PluginSource],
) -> PluginSource | None:
    return next(
        (
            source
            for source in sources
            if any(
                _plugin_policy_matches(plugin_id, source.plugin_id)
                for plugin_id in official_plugins
            )
        ),
        None,
    )


def resolve_target_policy(
    target: str,
    home: Path | None = None,
    hub: Path | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> TargetResolution:
    """Resolve capabilities suppressed by product plugins or target policy."""
    resolved_home = (home or Path.home()).expanduser()
    resolved_hub = (
        hub or resolved_home / ".config" / "agent-hub"
    ).expanduser()
    target_name = target.casefold()
    target_family = target_name.split(":", 1)[0]
    sources = active_plugin_sources(target_family, resolved_home, runner=runner)

    skills = _manifest_skills(resolved_hub / "manifest.yaml")
    shared_document = _load_json(resolved_hub / "mcp" / "shared-servers.json")
    shared_value = shared_document.get("mcpServers", {})
    shared = shared_value if isinstance(shared_value, dict) else {}
    policy = _load_json(resolved_hub / "policies" / "tool-scopes.json")

    suppressed_skills: set[str] = set()
    suppressed_mcp: set[str] = set()
    skill_owners: dict[str, str] = {}
    mcp_owners: dict[str, str] = {}
    mcp_collisions: set[str] = set()
    capability_decisions: dict[str, CapabilityDecision] = {}
    findings: list[str] = []

    shared_identities = {
        name.casefold(): normalize_mcp_identity(config)
        for name, config in shared.items()
        if isinstance(name, str) and isinstance(config, dict) and _enabled(config)
    }
    shared_configs = {
        name.casefold(): config
        for name, config in shared.items()
        if isinstance(name, str) and isinstance(config, dict) and _enabled(config)
    }
    for source in sources:
        owner = f"plugin:{source.plugin_id.split('@', 1)[0]}"
        plugin_names = {
            name.casefold()
            for name in source.mcp_servers
            if isinstance(name, str)
        }
        for skill in source.skills:
            if skill in skills:
                suppressed_skills.add(skill)
                skill_owners[skill] = owner
        for name in shared_identities:
            canonical = shared_configs.get(name, {})
            transport_match = any(
                isinstance(config, dict)
                and isinstance(canonical, dict)
                and mcp_identity_matches(config, canonical)
                for config in source.mcp_servers.values()
            )
            if transport_match:
                suppressed_mcp.add(name)
                mcp_owners[name] = owner
            elif name in plugin_names:
                mcp_collisions.add(name)

    contract = _capability_contract(policy)
    defaults = contract.get("defaults", {})
    default_selection = (
        defaults.get("selection", "auto")
        if isinstance(defaults, dict)
        else "auto"
    )
    if not isinstance(default_selection, str):
        default_selection = "auto"
        findings.append("capability_ownership.defaults.selection is invalid")
    capabilities = contract.get("capabilities", {})
    if capabilities and not isinstance(capabilities, dict):
        findings.append("capability_ownership.capabilities is invalid")
        capabilities = {}
    if isinstance(capabilities, dict):
        for raw_name, raw_capability in sorted(capabilities.items()):
            capability_name = str(raw_name).casefold()
            if not isinstance(raw_capability, dict):
                findings.append(f"{capability_name}: capability policy is invalid")
                continue
            hub_bundle = raw_capability.get("hub", {})
            if not isinstance(hub_bundle, dict):
                findings.append(f"{capability_name}: hub bundle is invalid")
                continue
            declared_skills = _string_list(hub_bundle.get("skills", []))
            declared_mcp = _string_list(hub_bundle.get("mcp", []))
            if declared_skills is None:
                findings.append(f"{capability_name}: hub skills list is invalid")
                declared_skills = []
            if declared_mcp is None:
                findings.append(f"{capability_name}: hub mcp list is invalid")
                declared_mcp = []
            valid_skills = {
                name for name in declared_skills if name in skills
            }
            valid_mcp = {
                name for name in declared_mcp if name in shared_identities
            }
            for name in sorted(set(declared_skills) - valid_skills):
                findings.append(f"{capability_name}: unknown Hub Skill {name}")
            for name in sorted(set(declared_mcp) - valid_mcp):
                findings.append(f"{capability_name}: unknown Hub MCP {name}")

            target_capability = _capability_target_policy(
                raw_capability,
                target_name,
                target_family,
            )
            selection = target_capability.get("selection", default_selection)
            if not isinstance(selection, str):
                selection = "auto"
                findings.append(f"{capability_name}: selection is invalid")
            selection = selection.casefold()
            official_plugins = _string_list(
                target_capability.get("official_plugins", [])
            )
            if official_plugins is None:
                official_plugins = []
                findings.append(
                    f"{capability_name}: official_plugins list is invalid"
                )
            active_official = _active_official_source(
                official_plugins,
                sources,
            )

            for name in valid_skills:
                suppressed_skills.discard(name)
                skill_owners.pop(name, None)
            for name in valid_mcp:
                suppressed_mcp.discard(name)
                mcp_owners.pop(name, None)
                mcp_collisions.discard(name)

            desired_owner = selection
            effective_owner: str | None = None
            state = "owner-missing"
            reason = "selected owner is unavailable"
            fallback_owner: str | None = None
            capability_skills: set[str] = set()
            capability_mcp: set[str] = set()

            if selection == "auto":
                fallback_owner = "hub"
                if active_official is None:
                    desired_owner = "hub"
                    effective_owner = "hub"
                    state = "hub-managed"
                    reason = "no active reviewed official plugin"
                else:
                    desired_owner = f"plugin:{_normalized_plugin_id(active_official.plugin_id)}"
                    effective_owner = desired_owner
                    state = "plugin-owned"
                    reason = f"active reviewed plugin via {active_official.evidence}"
                    capability_skills.update(valid_skills)
                    capability_mcp.update(valid_mcp)
            elif selection == "hub":
                desired_owner = "hub"
                effective_owner = "hub"
                if active_official is None:
                    state = "hub-managed"
                    reason = "Hub explicitly selected"
                else:
                    state = "ownership-conflict"
                    reason = (
                        "Hub explicitly selected while an equivalent official "
                        "plugin is active"
                    )
            elif selection.startswith("plugin:"):
                requested = _normalized_plugin_id(selection.removeprefix("plugin:"))
                desired_owner = f"plugin:{requested}"
                requested_source = next(
                    (
                        source
                        for source in sources
                        if _normalized_plugin_id(source.plugin_id) == requested
                        and any(
                            _plugin_policy_matches(plugin_id, source.plugin_id)
                            for plugin_id in official_plugins
                        )
                    ),
                    None,
                )
                capability_skills.update(valid_skills)
                capability_mcp.update(valid_mcp)
                if requested_source is not None:
                    effective_owner = desired_owner
                    state = "plugin-owned"
                    reason = f"explicit plugin via {requested_source.evidence}"
                else:
                    reason = "explicit plugin is not active"
            elif selection == "disabled":
                desired_owner = "disabled"
                state = "disabled-by-policy"
                reason = "capability explicitly disabled"
                capability_skills.update(valid_skills)
                capability_mcp.update(valid_mcp)
            elif selection.startswith("target:"):
                desired_owner = selection
                capability_skills.update(valid_skills)
                capability_mcp.update(valid_mcp)
                reason = "target provider discovery is unavailable"
            else:
                findings.append(
                    f"{capability_name}: unsupported selection {selection}"
                )

            component_owner = (
                effective_owner
                or ("disabled" if state == "disabled-by-policy" else desired_owner)
            )
            for name in capability_skills:
                suppressed_skills.add(name)
                skill_owners[name] = component_owner
            for name in capability_mcp:
                suppressed_mcp.add(name)
                mcp_owners[name] = component_owner
            capability_decisions[capability_name] = CapabilityDecision(
                capability=capability_name,
                target=target_name,
                selection=selection,
                desired_owner=desired_owner,
                effective_owner=effective_owner,
                state=state,
                reason=reason,
                suppressed_skills=frozenset(capability_skills),
                suppressed_mcp=frozenset(capability_mcp),
                fallback_owner=fallback_owner,
            )

    plugins_policy = policy.get("plugins", {})
    target_plugins = (
        plugins_policy.get(target_name, plugins_policy.get(target_family, {}))
        if isinstance(plugins_policy, dict)
        else {}
    )
    preferred = (
        target_plugins.get("prefer_marketplace", {})
        if isinstance(target_plugins, dict)
        else {}
    )
    if isinstance(preferred, dict):
        for policy_id, capabilities in preferred.items():
            if not any(
                _plugin_policy_matches(str(policy_id), source.plugin_id)
                for source in sources
            ):
                continue
            owner = f"plugin:{str(policy_id).split('@', 1)[0]}"
            if not isinstance(capabilities, dict):
                continue
            for skill in capabilities.get("skills", []):
                canonical = str(skill).casefold()
                if canonical in skills:
                    suppressed_skills.add(canonical)
                    skill_owners[canonical] = owner
            for server in capabilities.get("mcp", []):
                canonical = str(server).casefold()
                if canonical in shared_identities:
                    suppressed_mcp.add(canonical)
                    mcp_owners[canonical] = owner

    distribution = policy.get("distribution", {})
    target_distribution = (
        distribution.get(target_name, distribution.get(target_family, {}))
        if isinstance(distribution, dict)
        else {}
    )
    if isinstance(target_distribution, dict):
        for skill in target_distribution.get("exclude_skills", []):
            canonical = str(skill).casefold()
            if canonical in skills:
                suppressed_skills.add(canonical)
                skill_owners[canonical] = "excluded"
        for server in target_distribution.get("exclude_mcp", []):
            canonical = str(server).casefold()
            if canonical in shared_identities:
                suppressed_mcp.add(canonical)
                mcp_owners[canonical] = "excluded"

    mcp_collisions.difference_update(suppressed_mcp)
    return TargetResolution(
        suppressed_skills=frozenset(suppressed_skills),
        suppressed_mcp=frozenset(suppressed_mcp),
        skill_owners=skill_owners,
        mcp_owners=mcp_owners,
        mcp_collisions=frozenset(mcp_collisions),
        active_plugins=tuple(sources),
        capabilities=capability_decisions,
        findings=tuple(findings),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument(
        "--hub",
        type=Path,
        default=Path.home() / ".config" / "agent-hub",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    suppressed_skills = subparsers.add_parser("suppressed-skills")
    suppressed_skills.add_argument("target")
    suppressed_mcp = subparsers.add_parser("suppressed-mcp")
    suppressed_mcp.add_argument("target")
    args = parser.parse_args()

    resolution = resolve_target_policy(
        args.target,
        args.home,
        args.hub,
    )
    values = (
        resolution.suppressed_skills
        if args.command == "suppressed-skills"
        else resolution.suppressed_mcp
    )
    for value in sorted(values):
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
