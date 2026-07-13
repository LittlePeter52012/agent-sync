#!/usr/bin/env python3
"""Report local agent capabilities and agent-sync coverage without secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any


HOME = Path.home()
HUB = Path(os.environ.get("AGENT_HUB_ROOT", HOME / ".config" / "agent-hub"))
PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


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
            findings.append(
                {
                    "severity": "attention",
                    "message": f"Missing MCP executable for {server_name}: {command}",
                }
            )
    return findings


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
    elif name == "Gemini / Antigravity" and (HOME / ".gemini" / "extensions").exists():
        labels.append("extensions")
    elif name == "Claude" and (HOME / ".claude" / "plugins").exists():
        labels.append("plugins")
    return labels


def target_records(skills: list[str], shared: list[str]) -> list[dict[str, Any]]:
    targets = [
        ("Codex / ChatGPT", "codex", HOME / ".codex" / "config.toml", HOME / ".codex" / "skills", "toml", codex_mcp_keys, ("ChatGPT.app",)),
        ("Claude", "claude", HOME / ".claude.json", HOME / ".claude" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Claude.app",)),
        ("Cursor", "cursor", HOME / ".cursor" / "mcp.json", HOME / ".cursor" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Cursor.app",)),
        ("Gemini / Antigravity", "gemini", HOME / ".gemini" / "config" / "mcp_config.json", HOME / ".gemini" / "config" / "skills", "json", lambda p: json_object_keys(p, "mcpServers"), ("Gemini.app", "Antigravity.app", "Antigravity IDE.app")),
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
            "mcp": count_mcp(mcp_reader(config), shared) if kind != "none" else {"configured": 0, "expected": 0},
            "models": model_names(config, kind) if kind in {"json", "toml"} else [],
            "capabilities": capability_labels(name, config),
        }
        if name == "Copilot / VS Code":
            record["profiles"] = vscode_profiles(shared)
        records.append(record)
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


def build_report() -> dict[str, Any]:
    skills = read_manifest_skills()
    shared = read_shared_mcp()
    agents = target_records(skills, shared)
    findings = rule_findings() + mcp_usability_findings()
    for agent in agents:
        if agent["name"] != "Agents" and not agent["config_present"]:
            findings.append({"severity": "attention", "message": f"{agent['name']} is supported but has no local configuration."})
        if agent["skills"]["expected"] and agent["skills"]["configured"] != agent["skills"]["expected"]:
            findings.append({"severity": "attention", "message": f"{agent['name']} skill coverage is incomplete."})
        if agent["mcp"]["expected"] and agent["mcp"]["configured"] != agent["mcp"]["expected"]:
            findings.append({"severity": "attention", "message": f"{agent['name']} shared MCP coverage is incomplete or command-managed."})
        for profile in agent.get("profiles", []):
            if profile["mcp"]["configured"] != profile["mcp"]["expected"]:
                findings.append({"severity": "attention", "message": f"VS Code profile {profile['id']} shared MCP coverage is incomplete."})
    return {"overall": "ATTENTION" if findings else "HEALTHY", "agents": agents, "findings": findings}


def marker(value: bool) -> str:
    return "✓" if value else "—"


def render(report: dict[str, Any]) -> str:
    lines = ["Agent Sync Doctor", "─" * 78]
    lines.append(f"{'Agent':<22} {'CLI':<5} {'Config':<8} {'Skills':<8} {'MCP':<8} Capabilities")
    for agent in report["agents"]:
        skills = agent["skills"]
        mcp = agent["mcp"]
        lines.append(
            f"{agent['name']:<22} {marker(agent['cli_present']):<5} {marker(agent['config_present']):<8} "
            f"{skills['configured']}/{skills['expected']:<6} {mcp['configured']}/{mcp['expected']:<6} "
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
    args = parser.parse_args()
    report = build_report()
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
