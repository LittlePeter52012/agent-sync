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

from config_inventory import collect_inventory
from sync_hermes import is_registered as hermes_skills_registered


HOME = Path.home()
HUB = Path(os.environ.get("AGENT_HUB_ROOT", HOME / ".config" / "agent-hub"))
PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
HOOK_PARSE_ERROR = "<hook-parse-error>"


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
                    {
                        "severity": "attention",
                        "message": f"Unresolved Hub placeholder for {server_name}: {variable}",
                    }
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
                {
                    "severity": "attention",
                    "message": f"Missing MCP executable for {server_name}: {command_name}",
                }
            )
    return findings


def configured_mcp_findings() -> list[dict[str, str]]:
    """Audit every configured MCP, including tool-only and retired entries."""
    findings: list[dict[str, str]] = []
    for record in collect_inventory(HOME, HUB):
        if record["parse_error"]:
            findings.append(
                {
                    "severity": "attention",
                    "message": f"{record['label']} MCP configuration is invalid.",
                }
            )
        for name in record["missing_commands"]:
            findings.append(
                {
                    "severity": "attention",
                    "message": f"Missing MCP executable for {record['label']}/{name}.",
                }
            )
        for name in record["retired"]:
            findings.append(
                {
                    "severity": "attention",
                    "message": f"Retired MCP still configured in {record['label']}: {name}",
                }
            )
        for names in record.get("duplicate_names", []):
            findings.append(
                {
                    "severity": "attention",
                    "message": (
                        f"Duplicate MCP aliases in {record['label']}: "
                        f"{', '.join(names)}"
                    ),
                }
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
    return plugins


def plugin_scope_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [
            {
                "severity": "attention",
                "message": "Tool scope policy is invalid JSON.",
            }
        ]
    policies = data.get("plugins", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            {
                "severity": "attention",
                "message": "Tool scope policy has an invalid plugins section.",
            }
        ]
    inventories = {
        "opencode": opencode_plugins(),
        "codex": codex_plugins(),
    }
    labels = {"opencode": "OpenCode", "codex": "Codex / ChatGPT"}
    findings: list[dict[str, str]] = []
    for tool, policy in policies.items():
        if tool not in inventories or not isinstance(policy, dict):
            continue
        installed = inventories[tool]
        for plugin in policy.get("required", []):
            if isinstance(plugin, str) and normalize_plugin_spec(plugin) not in installed:
                findings.append(
                    {
                        "severity": "attention",
                        "message": f"Required plugin missing from {labels[tool]}: {plugin}",
                    }
                )
        for plugin in policy.get("forbidden", []):
            if not isinstance(plugin, str):
                continue
            normalized = normalize_plugin_spec(plugin)
            if plugin in installed or normalized in installed:
                findings.append(
                    {
                        "severity": "attention",
                        "message": f"Forbidden plugin configured in {labels[tool]}: {plugin}",
                    }
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
                    {
                        "severity": "attention",
                        "message": (
                            "Codex agent setting differs from Hub policy: "
                            f"{key} (configured {configured}, expected {expected})."
                        ),
                    }
                )

    remove_agent_keys = orchestration.get("remove_agent_keys", [])
    if isinstance(remove_agent_keys, list):
        for key in remove_agent_keys:
            if isinstance(key, str) and key in configured_agents:
                findings.append(
                    {
                        "severity": "attention",
                        "message": (
                            "Forbidden Codex agent legacy setting is present: "
                            f"{key}."
                        ),
                    }
                )

    table_names = set(re.findall(r"(?m)^\[([^\]]+)\]\s*$", config_text))
    forbidden_tables = orchestration.get("forbidden_tables", [])
    if isinstance(forbidden_tables, list):
        for name in forbidden_tables:
            if isinstance(name, str) and name in table_names:
                findings.append(
                    {
                        "severity": "attention",
                        "message": (
                            "Forbidden Codex orchestration table is present: "
                            f"{name}."
                        ),
                    }
                )

    forbidden_marketplaces = orchestration.get("forbidden_marketplaces", [])
    if isinstance(forbidden_marketplaces, list):
        for name in forbidden_marketplaces:
            table = f"marketplaces.{name}"
            if isinstance(name, str) and table in table_names:
                findings.append(
                    {
                        "severity": "attention",
                        "message": (
                            "Forbidden Codex marketplace is configured: "
                            f"{name}."
                        ),
                    }
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
                    {
                        "severity": "attention",
                        "message": (
                            "Forbidden Codex hook state is still trusted: "
                            f"{prefix}"
                        ),
                    }
                )
    return findings


def skill_dependency_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    policies = data.get("skills", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            {
                "severity": "attention",
                "message": "Tool scope policy has an invalid skills section.",
            }
        ]

    manifest_skills = set(read_manifest_skills())
    findings: list[dict[str, str]] = []
    for skill, policy in policies.items():
        if skill not in manifest_skills:
            display_name = skill.replace(chr(92), "/").rsplit("/", 1)[-1]
            findings.append(
                {
                    "severity": "attention",
                    "message": (
                        "Skill dependency policy names unlisted Hub Skill: "
                        f"{display_name}"
                    ),
                }
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
                {
                    "severity": "attention",
                    "message": (
                        "Skill dependency policy has invalid required_commands "
                        f"for {skill}"
                    ),
                }
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
                    {
                        "severity": "attention",
                        "message": (
                            f"Required CLI missing for Skill {skill}: "
                            f"{command.replace(chr(92), '/').rsplit('/', 1)[-1]}"
                        ),
                    }
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
    except (OSError, json.JSONDecodeError):
        return []
    policies = data.get("hooks", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            {
                "severity": "attention",
                "message": "Tool scope policy has an invalid hooks section.",
            }
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
                {
                    "severity": "attention",
                    "message": (
                        "Hook scope policy has an unsupported tool: "
                        f"{tool}"
                    ),
                }
            )
            continue
        required = (
            policy.get("required_events")
            if isinstance(policy, dict)
            else None
        )
        if not isinstance(required, dict):
            findings.append(
                {
                    "severity": "attention",
                    "message": (
                        "Hook scope policy has invalid required_events "
                        f"for {tool}."
                    ),
                }
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
                    {
                        "severity": "attention",
                        "message": (
                            "Hook scope policy has an invalid event entry "
                            f"for {tool}."
                        ),
                    }
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
                        {
                            "severity": "attention",
                            "message": (
                                f"{label} {event} lifecycle hook is missing."
                            ),
                        }
                    )
                    continue
                missing_executable = hook_command_executable(matched)
                if missing_executable == HOOK_PARSE_ERROR:
                    findings.append(
                        {
                            "severity": "attention",
                            "message": (
                                f"{label} {event} lifecycle hook command "
                                "is malformed."
                            ),
                        }
                    )
                elif missing_executable:
                    findings.append(
                        {
                            "severity": "attention",
                            "message": (
                                f"{label} {event} lifecycle hook executable "
                                f"is missing: {missing_executable}"
                            ),
                        }
                    )
    return findings


def mcp_scope_findings() -> list[dict[str, str]]:
    path = HUB / "policies" / "tool-scopes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    policies = data.get("mcp", {}) if isinstance(data, dict) else {}
    if not isinstance(policies, dict):
        return [
            {
                "severity": "attention",
                "message": "Tool scope policy has an invalid mcp section.",
            }
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
                {
                    "severity": "attention",
                    "message": f"Tool scope policy has an unsupported MCP tool: {tool}",
                }
            )
            continue
        if not isinstance(policy, dict):
            findings.append(
                {
                    "severity": "attention",
                    "message": f"Tool scope policy has an invalid MCP entry for {tool}.",
                }
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
                    {
                        "severity": "attention",
                        "message": f"Tool scope policy has invalid {key} for {tool}.",
                    }
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
                {
                    "severity": "attention",
                    "message": (
                        f"Invalid tool-only MCP policy for {tool}: "
                        "required_tool_only must be a subset of allowed_tool_only."
                    ),
                }
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
                    {
                        "severity": "attention",
                        "message": (
                            f"Unexpected tool-only MCP in {labels[tool]}: "
                            f"{actual[name]}"
                        ),
                    }
                )
        for name in sorted(set(required) - set(actual)):
            findings.append(
                {
                    "severity": "attention",
                    "message": (
                        f"Required tool-only MCP missing from {labels[tool]}: "
                        f"{required[name]}"
                    ),
                }
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


def runtime_mcp_findings() -> tuple[list[dict[str, str]], list[str]]:
    """Run bounded, read-only CLI probes and keep only sanitized outcomes."""
    probes = [
        ("OpenCode", "opencode", ["mcp", "list"], 30),
        ("Claude", "claude", ["mcp", "list"], 90),
    ]
    findings: list[dict[str, str]] = []
    checked: list[str] = []
    common_timeout = os.environ.get("AGENT_SYNC_RUNTIME_TIMEOUT")
    for label, executable, arguments, default_timeout in probes:
        command = shutil.which(executable)
        if not command:
            continue
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
                {
                    "severity": "attention",
                    "message": f"Runtime MCP probe timed out for {label}.",
                }
            )
            continue
        except OSError:
            findings.append(
                {
                    "severity": "attention",
                    "message": f"Runtime MCP probe could not start for {label}.",
                }
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
            findings.append(
                {
                    "severity": "attention",
                    "message": f"Runtime MCP connection failed in {label}: {name}",
                }
            )
        if result.returncode and not failed:
            findings.append(
                {
                    "severity": "attention",
                    "message": f"Runtime MCP probe failed for {label}.",
                }
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
        profiles.append({"id": profile.name, "mcp": count_mcp(json_object_keys(config, "servers"), shared)})
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
        ("Codex / ChatGPT", "codex", HOME / ".codex" / "config.toml", HOME / ".codex" / "skills", "toml", codex_mcp_keys, ("ChatGPT.app",)),
        ("Claude", "claude", HOME / ".claude.json", HOME / ".claude" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Claude.app",)),
        ("Cursor", "cursor", HOME / ".cursor" / "mcp.json", HOME / ".cursor" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Cursor.app",)),
        ("Gemini CLI", "gemini", HOME / ".gemini" / "settings.json", HOME / ".agents" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ()),
        ("Gemini global", "", HOME / ".gemini" / "config" / "mcp_config.json", HOME / ".gemini" / "config" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Gemini.app",)),
        ("Antigravity App", "", HOME / ".gemini" / "antigravity" / "mcp_config.json", HOME / ".gemini" / "antigravity" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Antigravity.app",)),
        ("Antigravity CLI", "agy", HOME / ".gemini" / "antigravity-cli" / "mcp_config.json", HOME / ".gemini" / "antigravity-cli" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ()),
        ("Antigravity IDE", "", HOME / ".gemini" / "antigravity-ide" / "mcp_config.json", HOME / ".gemini" / "antigravity-ide" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Antigravity IDE.app",)),
        ("OpenCode", "opencode", HOME / ".config" / "opencode" / "opencode.json", HOME / ".config" / "opencode" / "skills", "json", lambda p: json_object_keys(p, "mcp"), ()),
        ("Copilot / VS Code", "copilot", HOME / "Library" / "Application Support" / "Code" / "User" / "mcp.json", HOME / ".copilot" / "skills", "json", lambda p: json_object_keys(p, "servers"), ("Visual Studio Code.app",)),
        ("Agents", "", HOME / ".agents", HOME / ".agents" / "skills", "none", lambda p: set(), ()),
    ]
    records: list[dict[str, Any]] = []
    for name, command, config, skill_dir, kind, mcp_reader, apps in targets:
        config_present = config.exists()
        record = {
            "name": name,
            "cli_present": bool(command and shutil.which(command)),
            "application_present": application_present(apps),
            "config_present": config_present,
            "skills": count_skills(skill_dir, skills),
            "managed_skills": count_managed_skills(skill_dir, skills),
            "mcp": count_mcp(mcp_reader(config), shared) if kind != "none" else {"configured": 0, "expected": 0},
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
    targets = [HOME / ".codex" / "AGENTS.md", HOME / ".claude" / "CLAUDE.md", HOME / ".gemini" / "GEMINI.md"]
    for rule in sorted((HUB / "rules").glob("*.md")) if (HUB / "rules").exists() else []:
        content = rule.read_text(encoding="utf-8").strip()
        for target in targets:
            if target.exists() and target.read_text(encoding="utf-8").count(content) > 1:
                findings.append({"severity": "attention", "message": f"Duplicate synced rule in {target.name}: {rule.name}"})
    return findings


def build_report(runtime: bool = False) -> dict[str, Any]:
    skills = read_manifest_skills()
    shared = read_shared_mcp()
    agents = target_records(skills, shared)
    findings = (
        rule_findings()
        + mcp_usability_findings()
        + configured_mcp_findings()
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
        if agent["name"] != "Agents" and not agent["config_present"]:
            findings.append({"severity": "attention", "message": f"{agent['name']} is supported but has no local configuration."})
        if agent["skills"]["expected"] and agent["skills"]["configured"] != agent["skills"]["expected"]:
            findings.append({"severity": "attention", "message": f"{agent['name']} skill coverage is incomplete."})
        if (
            agent["managed_skills"]["expected"]
            and agent["managed_skills"]["configured"]
            != agent["managed_skills"]["expected"]
            and agent["skills"]["configured"] == agent["skills"]["expected"]
        ):
            findings.append(
                {
                    "severity": "attention",
                    "message": (
                        f"{agent['name']} skill ownership is not Hub-managed."
                    ),
                }
            )
        if agent["mcp"]["expected"] and agent["mcp"]["configured"] != agent["mcp"]["expected"]:
            findings.append({"severity": "attention", "message": f"{agent['name']} shared MCP coverage is incomplete or command-managed."})
        for profile in agent.get("profiles", []):
            if profile["mcp"]["configured"] != profile["mcp"]["expected"]:
                findings.append({"severity": "attention", "message": f"VS Code profile {profile['id']} shared MCP coverage is incomplete."})
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
