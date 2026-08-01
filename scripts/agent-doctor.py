#!/usr/bin/env python3
"""Report local agent capabilities and agent-sync coverage without secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from capability_resolver import active_plugin_sources, resolve_target_policy
from config_inventory import collect_inventory, resolve_mcp_coverage
from sync_hermes import is_registered as hermes_skills_registered


HOME = Path.home()
HUB = Path(os.environ.get("AGENT_HUB_ROOT", HOME / ".config" / "agent-hub"))
PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
HOOK_PARSE_ERROR = "<hook-parse-error>"


def make_finding(
    message: str,
    *,
    scope: str,
    owner: str,
    severity: str = "attention",
) -> dict[str, str]:
    return {
        "severity": severity,
        "message": message,
        "scope": scope,
        "owner": owner,
    }


def policy_file_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return [
            make_finding(
                "Tool scope policy could not be read.",
                scope="policy",
                owner="hub",
            )
        ]
    except json.JSONDecodeError:
        return [
            make_finding(
                "Tool scope policy is invalid JSON.",
                scope="policy",
                owner="hub",
            )
        ]
    return []


def read_manifest_skills() -> list[str]:
    path = HUB / "manifest.yaml"
    if not path.exists():
        return []
    skills: list[str] = []
    in_skills = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("skills:", "math_skills:")):
            in_skills = True
        elif in_skills and line.startswith("  - "):
            skills.append(line[4:].strip())
        elif in_skills and line and not line.startswith(" "):
            break
    return skills


def read_shared_mcp() -> list[str]:
    path = HUB / "mcp" / "shared-servers.json"
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {}))
    except json.JSONDecodeError:
        return []


def read_shared_mcp_config() -> dict[str, dict[str, Any]]:
    path = HUB / "mcp" / "shared-servers.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(name): cfg
        for name, cfg in value.items()
        if isinstance(cfg, dict)
    }


def placeholder_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, str):
        match = PLACEHOLDER_RE.match(value)
        if match:
            names.add(match.group(1))
    elif isinstance(value, dict):
        for child in value.values():
            names.update(placeholder_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(placeholder_names(child))
    return names


def local_variable_names() -> set[str]:
    names = set(os.environ)
    paths = [
        HOME / ".cursor" / "mcp.json",
        HOME / ".gemini" / "settings.json",
        HOME / ".gemini" / "config" / "mcp_config.json",
        HOME / ".claude.json",
        HOME / ".config" / "opencode" / "opencode.json",
        HOME / "Library" / "Application Support" / "Code" / "User" / "mcp.json",
    ]
    profiles = HOME / "Library" / "Application Support" / "Code" / "User" / "profiles"
    if profiles.exists():
        paths.extend(sorted(profiles.glob("*/mcp.json")))

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and child and not PLACEHOLDER_RE.match(child):
                    names.add(str(key))
                    if key == "OUTPUT_DIR":
                        names.add("MINERU_OUTPUT_DIR")
                    if key == "OPENAPI_MCP_HEADERS":
                        names.add("ANYTYPE_MCP_HEADERS")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, str) and child.startswith("/") and "obsidian" in child.lower():
                    names.add("OBSIDIAN_VAULT")
                visit(child)

    for path in paths:
        if not path.exists():
            continue
        try:
            visit(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    codex = HOME / ".codex" / "config.toml"
    if codex.exists():
        for key, value in re.findall(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"\n]+)"', codex.read_text(encoding="utf-8"), re.M):
            if value and not PLACEHOLDER_RE.match(value):
                names.add(key)
    return names


def mcp_usability_findings() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    resolved = local_variable_names()
    for server_name, config in read_shared_mcp_config().items():
        for variable in sorted(placeholder_names(config)):
            if variable not in resolved:
                findings.append(
                    make_finding(
                        f"Unresolved Hub placeholder for {server_name}: {variable}",
                        scope="managed",
                        owner="hub",
                    )
                )
        if config.get("type", "stdio") == "http" or config.get("url"):
            continue
        command = config.get("command")
        if not isinstance(command, str) or PLACEHOLDER_RE.match(command):
            continue
        present = Path(command).exists() if command.startswith("/") else bool(shutil.which(command))
        if not present:
            command_name = command.replace("\\", "/").rsplit("/", 1)[-1]
            findings.append(
                make_finding(
                    f"Missing MCP executable for {server_name}: {command_name}",
                    scope="managed",
                    owner="hub",
                )
            )
    return findings


def configured_mcp_findings() -> list[dict[str, str]]:
    """Audit every configured MCP, including tool-only and retired entries."""
    findings: list[dict[str, str]] = []
    shared = {name.casefold() for name in read_shared_mcp()}
    for record in collect_inventory(HOME, HUB):
        if record["parse_error"]:
            findings.append(
                make_finding(
                    f"{record['label']} MCP configuration is invalid.",
                    scope="local",
                    owner=record["tool"],
                )
            )
        for name in record["missing_commands"]:
            scope, owner = (
                ("managed", "hub")
                if name.casefold() in shared
                else ("local", record["tool"])
            )
            findings.append(
                make_finding(
                    f"Missing MCP executable for {record['label']}/{name}.",
                    scope=scope,
                    owner=owner,
                )
            )
        for name in record["retired"]:
            findings.append(
                make_finding(
                    f"Retired MCP still configured in {record['label']}: {name}",
                    scope="managed",
                    owner="hub",
                )
            )
        for names in record.get("duplicate_names", []):
            findings.append(
                make_finding(
                    (
                        f"Duplicate MCP aliases in {record['label']}: "
                        f"{', '.join(names)}"
                    ),
                    scope="local",
                    owner=record["tool"],
                )
            )
    return findings


def _json_has_concrete_sensitive(path: Path) -> bool:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    def visit(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        for key, child in value.items():
            if key in {"env", "environment", "headers", "http_headers"}:
                if isinstance(child, dict) and any(
                    isinstance(item, str)
                    and item
                    and not PLACEHOLDER_RE.match(item)
                    for item in child.values()
                ):
                    return True
            if isinstance(child, dict) and visit(child):
                return True
            if isinstance(child, list) and any(
                visit(item) for item in child if isinstance(item, dict)
            ):
                return True
        return False

    return visit(document)


def _toml_has_concrete_sensitive(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    in_sensitive = False
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            in_sensitive = bool(
                re.match(
                    r"^\s*\[mcp_servers\.[^\]]+\."
                    r"(?:env|headers|http_headers)\]\s*$",
                    line,
                )
            )
            continue
        if not in_sensitive or "=" not in line or line.lstrip().startswith("#"):
            continue
        _key, raw = line.split("=", 1)
        value = raw.strip().strip('"')
        if value and not PLACEHOLDER_RE.match(value):
            return True
    return False


def sensitive_config_findings() -> list[dict[str, str]]:
    configs = (
        ("Cursor", "cursor", HOME / ".cursor" / "mcp.json", "json"),
        ("Gemini CLI", "gemini", HOME / ".gemini" / "settings.json", "json"),
        ("Gemini global", "gemini-global", HOME / ".gemini" / "config" / "mcp_config.json", "json"),
        ("Claude", "claude", HOME / ".claude.json", "json"),
        ("OpenCode", "opencode", HOME / ".config" / "opencode" / "opencode.json", "json"),
        (
            "Copilot / VS Code",
            "vscode",
            HOME / "Library" / "Application Support" / "Code" / "User" / "mcp.json",
            "json",
        ),
        ("Codex / ChatGPT", "codex", HOME / ".codex" / "config.toml", "toml"),
    )
    findings: list[dict[str, str]] = []
    for label, owner, path, kind in configs:
        if not path.exists():
            continue
        sensitive = (
            _toml_has_concrete_sensitive(path)
            if kind == "toml"
            else _json_has_concrete_sensitive(path)
        )
        try:
            unsafe_mode = bool(path.stat().st_mode & 0o077)
        except OSError:
            continue
        if sensitive and unsafe_mode:
            findings.append(
                make_finding(
                    (
                        "Secret-bearing MCP config is group/world readable: "
                        f"{label}."
                    ),
                    scope="local",
                    owner=owner,
                )
            )
    return findings


def normalize_plugin_spec(value: str) -> str:
    if value.startswith("@"):
        marker = value.find("@", 1)
        return value if marker < 0 else value[:marker]
    return value.split("@", 1)[0]


def opencode_plugins() -> set[str]:
    path = HOME / ".config" / "opencode" / "opencode.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    values = data.get("plugin", []) if isinstance(data, dict) else []
    return {
        normalize_plugin_spec(value)
        for value in values
        if isinstance(value, str)
    }


def codex_plugins() -> set[str]:
    path = HOME / ".codex" / "config.toml"
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    plugins: set[str] = set()
    pattern = re.compile(
        r'^\[plugins\."([^"]+)"\]\s*$([\s\S]*?)(?=^\[|\Z)',
        re.M,
    )
    for match in pattern.finditer(text):
        body = match.group(2)
        if re.search(r"^enabled\s*=\s*false\s*$", body, re.M):
            continue
        plugins.add(match.group(1))
    plugins.update(
        source.plugin_id
        for source in active_plugin_sources("codex", HOME)
    )
    return plugins


def cursor_plugins() -> set[str]:
    return {
        normalize_plugin_spec(source.plugin_id)
        for source in active_plugin_sources("cursor", HOME)
    }


def plugin_scope_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    policies = data.get("plugins", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            make_finding(
                "Tool scope policy has an invalid plugins section.",
                scope="policy",
                owner="hub",
            )
        ]
    inventories = {
        "opencode": opencode_plugins(),
        "codex": codex_plugins(),
        "cursor": cursor_plugins(),
    }
    labels = {
        "opencode": "OpenCode",
        "codex": "Codex / ChatGPT",
        "cursor": "Cursor",
    }
    findings: list[dict[str, str]] = []
    for tool, policy in policies.items():
        if tool not in inventories:
            findings.append(
                make_finding(
                    f"Plugin scope policy has an unsupported tool: {tool}",
                    scope="policy",
                    owner="hub",
                )
            )
            continue
        if not isinstance(policy, dict):
            findings.append(
                make_finding(
                    f"Plugin scope policy has an invalid entry for {tool}.",
                    scope="policy",
                    owner="hub",
                )
            )
            continue
        installed = inventories[tool]
        for plugin in policy.get("required", []):
            if isinstance(plugin, str) and normalize_plugin_spec(plugin) not in installed:
                findings.append(
                    make_finding(
                        f"Required plugin missing from {labels[tool]}: {plugin}",
                        scope="local",
                        owner=tool,
                    )
                )
        for plugin in policy.get("forbidden", []):
            if not isinstance(plugin, str):
                continue
            normalized = normalize_plugin_spec(plugin)
            if plugin in installed or normalized in installed:
                findings.append(
                    make_finding(
                        f"Forbidden plugin configured in {labels[tool]}: {plugin}",
                        scope="local",
                        owner=tool,
                    )
                )
    return findings


def codex_orchestration_findings() -> list[dict[str, str]]:
    policy_path = HUB / "policies" / "tool-scopes.json"
    config_path = HOME / ".codex" / "config.toml"
    if not policy_path.exists() or not config_path.exists():
        return []
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    orchestration = (
        policy.get("orchestration", {}).get("codex", {})
        if isinstance(policy, dict)
        else {}
    )
    if not isinstance(orchestration, dict):
        return []
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []

    findings: list[dict[str, str]] = []
    expected_agents = orchestration.get("agents", {})
    configured_agents: dict[str, Any] = {}
    agents_match = re.search(
        r"(?ms)^\[agents\]\s*\n(?P<body>.*?)(?=^\[|\Z)",
        config_text,
    )
    if agents_match:
        for line in agents_match.group("body").splitlines():
            match = re.match(
                r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$",
                line,
            )
            if not match:
                continue
            key, raw = match.groups()
            if raw in {"true", "false"}:
                configured_agents[key] = raw == "true"
            elif re.fullmatch(r"-?\d+", raw):
                configured_agents[key] = int(raw)
            elif raw.startswith('"') and raw.endswith('"'):
                try:
                    configured_agents[key] = json.loads(raw)
                except json.JSONDecodeError:
                    continue
    if isinstance(expected_agents, dict):
        for key, expected in expected_agents.items():
            configured = configured_agents.get(key, "<unset>")
            if configured != expected:
                findings.append(
                    make_finding(
                        (
                            "Codex agent setting differs from Hub policy: "
                            f"{key} (configured {configured}, expected {expected})."
                        ),
                        scope="local",
                        owner="codex",
                    )
                )

    remove_agent_keys = orchestration.get("remove_agent_keys", [])
    if isinstance(remove_agent_keys, list):
        for key in remove_agent_keys:
            if isinstance(key, str) and key in configured_agents:
                findings.append(
                    make_finding(
                        (
                            "Forbidden Codex agent legacy setting is present: "
                            f"{key}."
                        ),
                        scope="local",
                        owner="codex",
                    )
                )

    table_names = set(re.findall(r"(?m)^\[([^\]]+)\]\s*$", config_text))
    forbidden_tables = orchestration.get("forbidden_tables", [])
    if isinstance(forbidden_tables, list):
        for name in forbidden_tables:
            if isinstance(name, str) and name in table_names:
                findings.append(
                    make_finding(
                        (
                            "Forbidden Codex orchestration table is present: "
                            f"{name}."
                        ),
                        scope="local",
                        owner="codex",
                    )
                )

    forbidden_marketplaces = orchestration.get("forbidden_marketplaces", [])
    if isinstance(forbidden_marketplaces, list):
        for name in forbidden_marketplaces:
            table = f"marketplaces.{name}"
            if isinstance(name, str) and table in table_names:
                findings.append(
                    make_finding(
                        (
                            "Forbidden Codex marketplace is configured: "
                            f"{name}."
                        ),
                        scope="local",
                        owner="codex",
                    )
                )

    prefixes = orchestration.get("forbidden_hook_prefixes", [])
    hook_names = re.findall(
        r'(?m)^\[hooks\.state\."([^"]+)"\]\s*$',
        config_text,
    )
    if isinstance(prefixes, list):
        for prefix in prefixes:
            if not isinstance(prefix, str) or not prefix:
                continue
            if any(
                isinstance(name, str) and name.startswith(prefix)
                for name in hook_names
            ):
                findings.append(
                    make_finding(
                        (
                            "Forbidden Codex hook state is still trusted: "
                            f"{prefix}"
                        ),
                        scope="local",
                        owner="codex",
                    )
                )
    return findings


def skill_dependency_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    policies = data.get("skills", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            make_finding(
                "Tool scope policy has an invalid skills section.",
                scope="policy",
                owner="hub",
            )
        ]

    manifest_skills = set(read_manifest_skills())
    findings: list[dict[str, str]] = []
    for skill, policy in policies.items():
        if skill not in manifest_skills:
            display_name = skill.replace(chr(92), "/").rsplit("/", 1)[-1]
            findings.append(
                make_finding(
                    (
                        "Skill dependency policy names unlisted Hub Skill: "
                        f"{display_name}"
                    ),
                    scope="policy",
                    owner="hub",
                )
            )
            continue
        commands = (
            policy.get("required_commands", [])
            if isinstance(policy, dict)
            else None
        )
        if not isinstance(commands, list) or not all(
            isinstance(command, str) and command.strip() for command in commands
        ):
            findings.append(
                make_finding(
                    (
                        "Skill dependency policy has invalid required_commands "
                        f"for {skill}"
                    ),
                    scope="policy",
                    owner="hub",
                )
            )
            continue
        for command in commands:
            candidate = Path(command).expanduser()
            if candidate.is_absolute() or "/" in command:
                available = candidate.is_file() and os.access(candidate, os.X_OK)
            else:
                available = bool(shutil.which(command))
            if not available:
                findings.append(
                    make_finding(
                        (
                            f"Required CLI missing for Skill {skill}: "
                            f"{command.replace(chr(92), '/').rsplit('/', 1)[-1]}"
                        ),
                        scope="managed",
                        owner="hub",
                    )
                )
    return findings


def hook_commands_for_event(path: Path, event: str) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    hooks = data.get("hooks", data) if isinstance(data, dict) else {}
    if not isinstance(hooks, dict):
        return []
    values = hooks.get(event, [])
    commands: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            command = value.get("command")
            if isinstance(command, str):
                commands.append(command)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(values)
    return commands


def hook_command_executable(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return HOOK_PARSE_ERROR
    for token in tokens:
        if "=" in token and not token.startswith(("/", "./", "../")):
            name = token.split("=", 1)[0]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                continue
        if token.startswith("/"):
            candidate = Path(token)
            if not (candidate.is_file() and os.access(candidate, os.X_OK)):
                return candidate.name
        break
    return None


def hook_scope_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    policies = data.get("hooks", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            make_finding(
                "Tool scope policy has an invalid hooks section.",
                scope="policy",
                owner="hub",
            )
        ]

    configs = {
        "codex": (
            "Codex / ChatGPT",
            HOME / ".codex" / "hooks.json",
        ),
        "gemini": (
            "Gemini CLI",
            HOME / ".gemini" / "settings.json",
        ),
        "cursor": (
            "Cursor",
            HOME / ".cursor" / "hooks.json",
        ),
    }
    findings: list[dict[str, str]] = []
    for tool, policy in policies.items():
        if tool not in configs:
            findings.append(
                make_finding(
                    (
                        "Hook scope policy has an unsupported tool: "
                        f"{tool}"
                    ),
                    scope="policy",
                    owner="hub",
                )
            )
            continue
        required = (
            policy.get("required_events")
            if isinstance(policy, dict)
            else None
        )
        if not isinstance(required, dict):
            findings.append(
                make_finding(
                    (
                        "Hook scope policy has invalid required_events "
                        f"for {tool}."
                    ),
                    scope="policy",
                    owner="hub",
                )
            )
            continue
        label, config = configs[tool]
        for event, fragments in required.items():
            if (
                not isinstance(event, str)
                or not isinstance(fragments, list)
                or not all(
                    isinstance(fragment, str) and fragment.strip()
                    for fragment in fragments
                )
            ):
                findings.append(
                    make_finding(
                        (
                            "Hook scope policy has an invalid event entry "
                            f"for {tool}."
                        ),
                        scope="policy",
                        owner="hub",
                    )
                )
                continue
            actual = hook_commands_for_event(config, event)
            for fragment in fragments:
                matched = next(
                    (command for command in actual if fragment in command),
                    None,
                )
                if matched is None:
                    findings.append(
                        make_finding(
                            (
                                f"{label} {event} lifecycle hook is missing."
                            ),
                            scope="local",
                            owner=tool,
                        )
                    )
                    continue
                missing_executable = hook_command_executable(matched)
                if missing_executable == HOOK_PARSE_ERROR:
                    findings.append(
                        make_finding(
                            (
                                f"{label} {event} lifecycle hook command "
                                "is malformed."
                            ),
                            scope="local",
                            owner=tool,
                        )
                    )
                elif missing_executable:
                    findings.append(
                        make_finding(
                            (
                                f"{label} {event} lifecycle hook executable "
                                f"is missing: {missing_executable}"
                            ),
                            scope="local",
                            owner=tool,
                        )
                    )
    return findings


def mcp_scope_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    policies = data.get("mcp", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            make_finding(
                "Tool scope policy has an invalid mcp section.",
                scope="policy",
                owner="hub",
            )
        ]

    labels = {
        "gemini": "Gemini CLI",
        "gemini-global": "Gemini global",
        "antigravity": "Gemini global",
        "antigravity-app": "Antigravity App",
        "antigravity-cli": "Antigravity CLI",
        "antigravity-ide": "Antigravity IDE",
        "cursor": "Cursor",
        "claude": "Claude",
        "opencode": "OpenCode",
        "codex": "Codex / ChatGPT",
        "vscode": "Copilot / VS Code",
    }
    inventory = {
        record["tool"]: record
        for record in collect_inventory(HOME, HUB)
        if not record["is_profile"]
    }
    shared = {name.casefold() for name in read_shared_mcp()}
    findings: list[dict[str, str]] = []

    for tool, policy in policies.items():
        if tool not in labels:
            findings.append(
                make_finding(
                    f"Tool scope policy has an unsupported MCP tool: {tool}",
                    scope="policy",
                    owner="hub",
                )
            )
            continue
        if not isinstance(policy, dict):
            findings.append(
                make_finding(
                    f"Tool scope policy has an invalid MCP entry for {tool}.",
                    scope="policy",
                    owner="hub",
                )
            )
            continue

        normalized: dict[str, dict[str, str]] = {}
        valid = True
        for key in ("allowed_tool_only", "required_tool_only"):
            values = policy.get(key, [])
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                findings.append(
                    make_finding(
                        f"Tool scope policy has invalid {key} for {tool}.",
                        scope="policy",
                        owner="hub",
                    )
                )
                valid = False
                continue
            normalized[key] = {
                value.casefold(): value for value in values
            }
        if not valid:
            continue

        allowed = normalized["allowed_tool_only"]
        required = normalized["required_tool_only"]
        outside_allowlist = sorted(set(required) - set(allowed))
        if outside_allowlist:
            findings.append(
                make_finding(
                    (
                        f"Invalid tool-only MCP policy for {tool}: "
                        "required_tool_only must be a subset of allowed_tool_only."
                    ),
                    scope="policy",
                    owner="hub",
                )
            )

        inventory_tool = (
            "gemini-global"
            if tool == "antigravity"
            else tool
        )
        record = inventory.get(inventory_tool, {"mcp_names": []})
        actual = {
            name.casefold(): name
            for name in record["mcp_names"]
            if name.casefold() not in shared
        }
        if "allowed_tool_only" in policy:
            for name in sorted(set(actual) - set(allowed)):
                findings.append(
                    make_finding(
                        (
                            f"Unexpected tool-only MCP in {labels[tool]}: "
                            f"{actual[name]}"
                        ),
                        scope="local",
                        owner=inventory_tool,
                    )
                )
        for name in sorted(set(required) - set(actual)):
            findings.append(
                make_finding(
                    (
                        f"Required tool-only MCP missing from {labels[tool]}: "
                        f"{required[name]}"
                    ),
                    scope="local",
                    owner=inventory_tool,
                )
            )
    return findings


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def failed_runtime_names(tool: str, output: str) -> list[str]:
    names: set[str] = set()
    for raw_line in output.splitlines():
        line = ANSI_RE.sub("", raw_line)
        if tool == "OpenCode":
            match = re.search(
                r"(?:^|\s)(?:x|✗|×)\s+([A-Za-z0-9_.:-]+).*?\bfailed\b",
                line,
                re.I,
            )
        else:
            match = re.search(
                r"^\s*([A-Za-z0-9_.-]+):.*\b(?:failed|error)\b",
                line,
                re.I,
            )
        if match:
            names.add(match.group(1))
    return sorted(names)


def classify_runtime_failure(
    target: str,
    name: str,
    coverage: dict[str, dict[str, str]],
) -> tuple[str, str]:
    record = next(
        (
            value
            for candidate, value in coverage.items()
            if candidate.casefold() == name.casefold()
        ),
        {},
    )
    if (
        record.get("owner") == "hub-managed"
        and record.get("state") not in {"plugin-owned", "excluded"}
    ):
        return "managed", "hub"
    return "local", target


def cursor_loopback_http_findings(
    config_path: Path = HOME / ".cursor" / "mcp.json",
    *,
    opener: Any = urlopen,
    timeout: float = 2.0,
    coverage: dict[str, dict[str, str]] | None = None,
) -> tuple[list[dict[str, str]], bool]:
    """Probe Cursor loopback HTTP MCP GET behavior without credentials."""
    if not config_path.exists():
        return [], False
    try:
        servers = json.loads(
            config_path.read_text(encoding="utf-8")
        ).get("mcpServers", {})
    except (OSError, json.JSONDecodeError):
        return [], False
    if not isinstance(servers, dict):
        return [], False

    findings: list[dict[str, str]] = []
    coverage = coverage or {}
    checked = False
    for name, config in servers.items():
        if not isinstance(name, str) or not isinstance(config, dict):
            continue
        url = config.get("url")
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            continue
        checked = True
        request = Request(
            url,
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        try:
            response = opener(request, timeout=timeout)
            response.close()
        except HTTPError as exc:
            if exc.code == 404:
                scope, owner = classify_runtime_failure(
                    "cursor",
                    name,
                    coverage,
                )
                findings.append(
                    make_finding(
                        (
                            "Cursor HTTP MCP is not Streamable HTTP "
                            f"compatible: {name}"
                        ),
                        scope=scope,
                        owner=owner,
                    )
                )
        except (URLError, TimeoutError, OSError):
            scope, owner = classify_runtime_failure(
                "cursor",
                name,
                coverage,
            )
            findings.append(
                make_finding(
                    (
                        f"Cursor HTTP MCP connection failed: {name}"
                    ),
                    scope=scope,
                    owner=owner,
                )
            )
    return findings, checked


def runtime_mcp_findings() -> tuple[list[dict[str, str]], list[str]]:
    """Run bounded, read-only CLI probes and keep only sanitized outcomes."""
    probes = [
        ("OpenCode", "opencode", "opencode", ["mcp", "list"], 30),
        ("Claude", "claude", "claude", ["mcp", "list"], 90),
    ]
    cursor_coverage = resolve_mcp_coverage("cursor", HOME, HUB)
    findings, cursor_checked = cursor_loopback_http_findings(
        coverage=cursor_coverage,
    )
    checked: list[str] = []
    if cursor_checked:
        checked.append("Cursor")
    common_timeout = os.environ.get("AGENT_SYNC_RUNTIME_TIMEOUT")
    for label, target, executable, arguments, default_timeout in probes:
        command = shutil.which(executable)
        if not command:
            continue
        coverage = resolve_mcp_coverage(target, HOME, HUB)
        checked.append(label)
        tool_timeout = os.environ.get(
            f"AGENT_SYNC_RUNTIME_TIMEOUT_{label.upper()}"
        )
        try:
            timeout = max(
                1,
                int(tool_timeout or common_timeout or default_timeout),
            )
        except ValueError:
            timeout = default_timeout
        try:
            result = subprocess.run(
                [command, *arguments],
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            findings.append(
                make_finding(
                    f"Runtime MCP probe timed out for {label}.",
                    scope="local",
                    owner=target,
                )
            )
            continue
        except OSError:
            findings.append(
                make_finding(
                    f"Runtime MCP probe could not start for {label}.",
                    scope="local",
                    owner=target,
                )
            )
            continue
        output = result.stdout + "\n" + result.stderr
        failed = failed_runtime_names(label, output)
        if failed or result.returncode:
            try:
                result = subprocess.run(
                    [command, *arguments],
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass
            else:
                output = result.stdout + "\n" + result.stderr
                failed = failed_runtime_names(label, output)
        for name in failed:
            scope, owner = classify_runtime_failure(target, name, coverage)
            findings.append(
                make_finding(
                    f"Runtime MCP connection failed in {label}: {name}",
                    scope=scope,
                    owner=owner,
                )
            )
        if result.returncode and not failed:
            findings.append(
                make_finding(
                    f"Runtime MCP probe failed for {label}.",
                    scope="local",
                    owner=target,
                )
            )
    return findings, checked


def json_object_keys(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    value = data.get(key, {})
    return set(value) if isinstance(value, dict) else set()


def codex_mcp_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(re.findall(r"^\[mcp_servers\.([^\].]+)\]", path.read_text(encoding="utf-8"), re.M))


def model_names(path: Path, kind: str) -> list[str]:
    if not path.exists():
        return []
    if kind == "toml":
        return re.findall(r'^model\s*=\s*"([^"\\]+)"', path.read_text(encoding="utf-8"), re.M)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    names: list[str] = []
    if isinstance(data.get("model"), str):
        names.append(data["model"])
    providers = data.get("provider")
    if isinstance(providers, dict):
        names.extend(str(name) for name in providers)
    return names


def application_present(names: tuple[str, ...]) -> bool:
    return any((base / name).exists() for base in (Path("/Applications"), HOME / "Applications") for name in names)


def count_skills(path: Path, skills: list[str]) -> dict[str, int]:
    return {"configured": sum((path / skill / "SKILL.md").exists() for skill in skills), "expected": len(skills)}


def count_managed_skills(path: Path, skills: list[str]) -> dict[str, int]:
    configured = 0
    for skill in skills:
        destination = path / skill
        source = HUB / "skills" / skill
        if not destination.is_symlink():
            continue
        try:
            if destination.resolve() == source.resolve():
                configured += 1
        except (OSError, RuntimeError):
            continue
    return {"configured": configured, "expected": len(skills)}


def effective_skill_counts(
    target: str,
    path: Path,
    skills: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    resolution = resolve_target_policy(target, HOME, HUB)
    suppressed = {
        skill.casefold()
        for skill in resolution.suppressed_skills
    }
    configured = 0
    managed = 0
    managed_expected = 0
    for skill in skills:
        if skill.casefold() in suppressed:
            configured += 1
            continue
        managed_expected += 1
        destination = path / skill
        source = HUB / "skills" / skill
        if (destination / "SKILL.md").exists():
            configured += 1
        if destination.is_symlink():
            try:
                if destination.resolve() == source.resolve():
                    managed += 1
            except (OSError, RuntimeError):
                pass
    return (
        {"configured": configured, "expected": len(skills)},
        {"configured": managed, "expected": managed_expected},
    )


def count_mcp(names: set[str], shared: list[str]) -> dict[str, int]:
    shared_lower = {name.lower() for name in shared}
    return {"configured": len({name.lower() for name in names} & shared_lower), "expected": len(shared)}


def vscode_profiles(shared: list[str]) -> list[dict[str, Any]]:
    root = HOME / "Library" / "Application Support" / "Code" / "User" / "profiles"
    if not root.exists():
        return []
    profiles = []
    for profile in sorted(path for path in root.iterdir() if path.is_dir()):
        config = profile / "mcp.json"
        coverage = resolve_mcp_coverage(
            f"vscode:{profile.name}",
            HOME,
            HUB,
        )
        accepted = {"hub-managed", "plugin-owned", "excluded"}
        profiles.append(
            {
                "id": profile.name,
                "mcp": {
                    "configured": sum(
                        value["state"] in accepted
                        for value in coverage.values()
                    ),
                    "expected": len(coverage),
                },
            }
        )
    return profiles


def capability_labels(name: str, config: Path) -> list[str]:
    labels = ["MCP"] if config.exists() else []
    if name == "Codex / ChatGPT":
        if (HOME / ".codex" / "plugins").exists():
            labels.append("plugins")
        labels.extend(("browser", "computer-use"))
    elif name == "Cursor" and (HOME / ".cursor" / "extensions").exists():
        labels.append("extensions")
    elif name == "Gemini CLI" and (HOME / ".gemini" / "extensions").exists():
        labels.append("extensions")
    elif name in {"Antigravity App", "Antigravity CLI"}:
        root_name = "antigravity" if name == "Antigravity App" else "antigravity-cli"
        if (HOME / ".gemini" / root_name / "plugins").exists():
            labels.append("plugins")
    elif name == "Antigravity IDE" and (HOME / ".antigravity-ide" / "extensions").exists():
        labels.append("extensions")
    elif name == "Claude" and (HOME / ".claude" / "plugins").exists():
        labels.append("plugins")
    return labels


def target_records(skills: list[str], shared: list[str]) -> list[dict[str, Any]]:
    targets = [
        ("Codex / ChatGPT", "codex", "codex", "codex", HOME / ".codex" / "config.toml", HOME / ".codex" / "skills", "toml", ("ChatGPT.app",)),
        ("Claude", "claude", "claude", "claude", HOME / ".claude.json", HOME / ".claude" / "skills", "json", ("Claude.app",)),
        ("Cursor", "cursor", "cursor", "cursor", HOME / ".cursor" / "mcp.json", HOME / ".cursor" / "skills", "json", ("Cursor.app",)),
        ("Gemini CLI", "gemini", "gemini", "gemini", HOME / ".gemini" / "settings.json", HOME / ".agents" / "skills", "json", ()),
        ("Gemini global", "", "gemini-global", "gemini-global", HOME / ".gemini" / "config" / "mcp_config.json", HOME / ".gemini" / "config" / "skills", "json", ("Gemini.app",)),
        ("Antigravity App", "", "antigravity-app", "antigravity-app", HOME / ".gemini" / "antigravity" / "mcp_config.json", HOME / ".gemini" / "antigravity" / "skills", "json", ("Antigravity.app",)),
        ("Antigravity CLI", "agy", "antigravity-cli", "antigravity-cli", HOME / ".gemini" / "antigravity-cli" / "mcp_config.json", HOME / ".gemini" / "antigravity-cli" / "skills", "json", ()),
        ("Antigravity IDE", "", "antigravity-ide", "antigravity-ide", HOME / ".gemini" / "antigravity-ide" / "mcp_config.json", HOME / ".gemini" / "antigravity-ide" / "skills", "json", ("Antigravity IDE.app",)),
        ("OpenCode", "opencode", "opencode", "opencode", HOME / ".config" / "opencode" / "opencode.json", HOME / ".config" / "opencode" / "skills", "json", ()),
        ("Copilot / VS Code", "copilot", "copilot", "vscode", HOME / "Library" / "Application Support" / "Code" / "User" / "mcp.json", HOME / ".copilot" / "skills", "json", ("Visual Studio Code.app",)),
        ("Agents", "", "agents", "agents", HOME / ".agents", HOME / ".agents" / "skills", "none", ()),
    ]
    records: list[dict[str, Any]] = []
    accepted = {"hub-managed", "plugin-owned", "excluded"}
    for name, command, skill_target_key, mcp_target_key, config, skill_dir, kind, apps in targets:
        config_present = config.exists()
        skill_count, managed_skill_count = effective_skill_counts(
            skill_target_key,
            skill_dir,
            skills,
        )
        coverage = (
            resolve_mcp_coverage(mcp_target_key, HOME, HUB)
            if kind != "none"
            else {}
        )
        record = {
            "name": name,
            "cli_present": bool(command and shutil.which(command)),
            "application_present": application_present(apps),
            "config_present": config_present,
            "skills": skill_count,
            "managed_skills": managed_skill_count,
            "mcp": (
                {
                    "configured": sum(
                        value["state"] in accepted
                        for value in coverage.values()
                    ),
                    "expected": len(coverage),
                }
                if kind != "none"
                else {"configured": 0, "expected": 0}
            ),
            "mcp_ownership": coverage,
            "models": model_names(config, kind) if kind in {"json", "toml"} else [],
            "capabilities": capability_labels(name, config),
        }
        if name == "Copilot / VS Code":
            record["profiles"] = vscode_profiles(shared)
        records.append(record)
    hermes_home = Path(
        os.environ.get("HERMES_HOME", HOME / ".hermes")
    ).expanduser()
    hermes_config = hermes_home / "config.yaml"
    if not hermes_config.exists():
        return records
    hermes_registered = (
        hermes_skills_registered(
            hermes_config.read_text(encoding="utf-8")
        )
    )
    hermes_skills = HOME / ".agents" / "skills"
    records.append(
        {
            "name": "Hermes",
            "cli_present": bool(shutil.which("hermes")),
            "application_present": False,
            "config_present": True,
            "skills": (
                count_skills(hermes_skills, skills)
                if hermes_registered
                else {"configured": 0, "expected": len(skills)}
            ),
            "managed_skills": (
                count_managed_skills(hermes_skills, skills)
                if hermes_registered
                else {"configured": 0, "expected": len(skills)}
            ),
            "mcp": {"configured": 0, "expected": 0},
            "models": [],
            "capabilities": (
                ["external Skills"] if hermes_registered else []
            ),
        }
    )
    return records


def rule_findings() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    targets = [
        HOME / ".codex" / "AGENTS.md",
        HOME / ".claude" / "CLAUDE.md",
        HOME / ".gemini" / "GEMINI.md",
    ]
    for rule in sorted((HUB / "rules").glob("*.md")) if (HUB / "rules").exists() else []:
        content = rule.read_text(encoding="utf-8").strip()
        begin = f"<!-- agent-sync:begin:{rule.stem} -->"
        end = f"<!-- agent-sync:end:{rule.stem} -->"
        for target in targets:
            if not target.exists():
                findings.append(
                    make_finding(
                        f"Missing rule target {target.name}: {rule.name}",
                        scope="managed",
                        owner="hub",
                    )
                )
                continue
            text = target.read_text(encoding="utf-8")
            if text.count(begin) != 1 or text.count(end) != 1:
                findings.append(
                    make_finding(
                        f"Missing or duplicate synced rule in {target.name}: {rule.name}",
                        scope="managed",
                        owner="hub",
                    )
                )
                continue
            block = text.split(begin, 1)[1].split(end, 1)[0]
            if content not in block:
                findings.append(
                    make_finding(
                        f"Drifted synced rule in {target.name}: {rule.name}",
                        scope="managed",
                        owner="hub",
                    )
                )
    return findings


def effective_mcp_findings() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    accepted = {"hub-managed", "plugin-owned", "excluded"}
    for record in target_records([], read_shared_mcp()):
        for name, ownership in record.get("mcp_ownership", {}).items():
            state = ownership["state"]
            if state not in accepted:
                findings.append(
                    make_finding(
                        (
                            f"{record['name']} shared MCP {name} is {state}."
                        ),
                        scope="managed",
                        owner="hub",
                    )
                )
    return findings


def build_report(runtime: bool = False) -> dict[str, Any]:
    skills = read_manifest_skills()
    shared = read_shared_mcp()
    agents = target_records(skills, shared)
    findings = (
        policy_file_findings()
        + rule_findings()
        + mcp_usability_findings()
        + configured_mcp_findings()
        + sensitive_config_findings()
        + effective_mcp_findings()
        + plugin_scope_findings()
        + codex_orchestration_findings()
        + skill_dependency_findings()
        + hook_scope_findings()
        + mcp_scope_findings()
    )
    runtime_checked: list[str] = []
    if runtime:
        runtime_findings, runtime_checked = runtime_mcp_findings()
        findings.extend(runtime_findings)
    for agent in agents:
        local_owner = {
            "Codex / ChatGPT": "codex",
            "Claude": "claude",
            "Cursor": "cursor",
            "Gemini CLI": "gemini",
            "Gemini global": "gemini-global",
            "Antigravity App": "antigravity-app",
            "Antigravity CLI": "antigravity-cli",
            "Antigravity IDE": "antigravity-ide",
            "OpenCode": "opencode",
            "Copilot / VS Code": "vscode",
            "Hermes": "hermes",
        }.get(agent["name"], agent["name"].casefold())
        if agent["name"] != "Agents" and not agent["config_present"]:
            findings.append(
                make_finding(
                    f"{agent['name']} is supported but has no local configuration.",
                    scope="local",
                    owner=local_owner,
                )
            )
        if agent["skills"]["expected"] and agent["skills"]["configured"] != agent["skills"]["expected"]:
            findings.append(
                make_finding(
                    f"{agent['name']} skill coverage is incomplete.",
                    scope="managed",
                    owner="hub",
                )
            )
        if (
            agent["managed_skills"]["expected"]
            and agent["managed_skills"]["configured"]
            != agent["managed_skills"]["expected"]
            and agent["skills"]["configured"] == agent["skills"]["expected"]
        ):
            findings.append(
                make_finding(
                    (
                        f"{agent['name']} skill ownership is not Hub-managed."
                    ),
                    scope="managed",
                    owner="hub",
                )
            )
        if agent["mcp"]["expected"] and agent["mcp"]["configured"] != agent["mcp"]["expected"]:
            findings.append(
                make_finding(
                    f"{agent['name']} shared MCP coverage is incomplete or command-managed.",
                    scope="managed",
                    owner="hub",
                )
            )
        for profile in agent.get("profiles", []):
            if profile["mcp"]["configured"] != profile["mcp"]["expected"]:
                findings.append(
                    make_finding(
                        f"VS Code profile {profile['id']} shared MCP coverage is incomplete.",
                        scope="managed",
                        owner="hub",
                    )
                )
    return {
        "overall": "ATTENTION" if findings else "HEALTHY",
        "agents": agents,
        "findings": findings,
        "runtime_checked": runtime_checked,
    }


def marker(value: bool) -> str:
    return "✓" if value else "—"


def render(report: dict[str, Any]) -> str:
    lines = ["Agent Sync Doctor", "─" * 90]
    lines.append(
        f"{'Agent':<22} {'CLI':<5} {'Config':<8} {'Skills':<8} "
        f"{'Managed':<8} {'MCP':<8} Capabilities"
    )
    for agent in report["agents"]:
        skills = agent["skills"]
        managed = agent["managed_skills"]
        mcp = agent["mcp"]
        lines.append(
            f"{agent['name']:<22} {marker(agent['cli_present']):<5} {marker(agent['config_present']):<8} "
            f"{skills['configured']}/{skills['expected']:<6} "
            f"{managed['configured']}/{managed['expected']:<6} "
            f"{mcp['configured']}/{mcp['expected']:<6} "
            f"{' · '.join(agent['capabilities']) or '—'}"
        )
        if agent["models"]:
            lines.append(f"{'':22} models: {', '.join(agent['models'])}")
        for profile in agent.get("profiles", []):
            lines.append(f"{'':22} profile {profile['id']}: {profile['mcp']['configured']}/{profile['mcp']['expected']} MCP")
    lines.extend(["", "Findings"])
    if report["findings"]:
        lines.extend(f"  ! {finding['message']}" for finding in report["findings"])
    else:
        lines.append("  ✓ No local configuration issues found.")
    lines.extend(["", f"Overall: {report['overall']}", "Run: agent-sync fix --dry-run"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_report(runtime=args.runtime)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 1 if args.strict and report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
