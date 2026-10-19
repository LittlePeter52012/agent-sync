#!/usr/bin/env python3
"""Reconcile bounded Codex orchestration settings from the private Agent Hub."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from atomic_file import atomic_write_text


class ReconcileError(RuntimeError):
    pass


SECTION_RE = re.compile(r"(?ms)^(?P<header>\[[^\n]+\]\n)(?P<body>.*?)(?=^\[|\Z)")
PLUGIN_SECTION_RE = re.compile(r'^\[plugins\."([^"]+)"\]\n$')
HOOK_SECTION_RE = re.compile(r'^\[hooks\.state\."([^"]+)"\]\n$')
SUPPORTED_AGENT_KEYS = (
    "enabled",
    "max_concurrent_threads_per_session",
    "default_subagent_model",
    "default_subagent_reasoning_effort",
    "interrupt_message",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report drift without changing the Codex configuration.",
    )
    return parser.parse_args()


def require_text_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ReconcileError(f"{context} must be a list of names")
    return value


def load_policy(
    hub: Path,
) -> tuple[
    dict[str, Any],
    set[str],
    tuple[str, ...],
    set[str],
    set[str],
    set[str],
]:
    path = hub / "policies" / "tool-scopes.json"
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReconcileError(f"Missing orchestration policy: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReconcileError("Cannot read a valid orchestration policy") from exc
    try:
        codex = root["orchestration"]["codex"]
        agents = codex["agents"]
    except (KeyError, TypeError) as exc:
        raise ReconcileError("Missing orchestration.codex.agents policy") from exc
    if not isinstance(agents, dict) or not agents:
        raise ReconcileError("orchestration.codex.agents must be a non-empty object")
    unknown = sorted(set(agents) - set(SUPPORTED_AGENT_KEYS))
    if unknown:
        raise ReconcileError(f"Unsupported Codex agent policy keys: {unknown}")

    plugins = require_text_list(
        root.get("plugins", {}).get("codex", {}).get("forbidden", []),
        "plugins.codex.forbidden",
    )
    prefixes = require_text_list(
        codex.get("forbidden_hook_prefixes", []),
        "orchestration.codex.forbidden_hook_prefixes",
    )
    remove_agent_keys = require_text_list(
        codex.get("remove_agent_keys", []),
        "orchestration.codex.remove_agent_keys",
    )
    forbidden_tables = require_text_list(
        codex.get("forbidden_tables", []),
        "orchestration.codex.forbidden_tables",
    )
    forbidden_marketplaces = require_text_list(
        codex.get("forbidden_marketplaces", []),
        "orchestration.codex.forbidden_marketplaces",
    )
    return (
        agents,
        set(plugins),
        tuple(prefixes),
        set(remove_agent_keys),
        set(forbidden_tables),
        set(forbidden_marketplaces),
    )


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise ReconcileError(f"Unsupported Codex agent policy value: {value!r}")


def update_agents_body(
    body: str,
    agents: dict[str, Any],
    remove_agent_keys: set[str],
) -> str:
    managed = set(agents) | remove_agent_keys
    kept: list[str] = []
    assignment = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in body.splitlines(keepends=True):
        match = assignment.match(line)
        if match and match.group(1) in managed:
            continue
        kept.append(line)
    desired = "".join(
        f"{key} = {toml_value(agents[key])}\n"
        for key in SUPPORTED_AGENT_KEYS
        if key in agents
    )
    remainder = "".join(kept).lstrip("\n")
    return desired + (f"\n{remainder}" if remainder else "")


def normalized_section_body(body: str) -> tuple[str, ...]:
    """Compare TOML section content while ignoring product blank-line style."""
    return tuple(
        line.rstrip()
        for line in body.splitlines()
        if line.strip()
    )


def reconcile(
    original: str,
    agents: dict[str, Any],
    forbidden_plugins: set[str],
    forbidden_hook_prefixes: tuple[str, ...],
    remove_agent_keys: set[str],
    forbidden_tables: set[str],
    forbidden_marketplaces: set[str],
) -> tuple[str, list[str]]:
    changes: list[str] = []
    parts: list[str] = []
    cursor = 0
    found_agents = False
    for match in SECTION_RE.finditer(original):
        parts.append(original[cursor : match.start()])
        header = match.group("header")
        body = match.group("body")
        plugin_match = PLUGIN_SECTION_RE.match(header)
        hook_match = HOOK_SECTION_RE.match(header)
        table_name = header[1:-2]
        marketplace = (
            table_name[len("marketplaces.") :]
            if table_name.startswith("marketplaces.")
            else None
        )
        if plugin_match and plugin_match.group(1) in forbidden_plugins:
            changes.append("remove forbidden Codex plugin state")
        elif marketplace in forbidden_marketplaces:
            changes.append("remove forbidden Codex marketplace")
        elif hook_match and any(
            hook_match.group(1).startswith(prefix)
            for prefix in forbidden_hook_prefixes
        ):
            changes.append("remove forbidden Codex hook state")
        elif table_name in forbidden_tables:
            changes.append("remove forbidden Codex orchestration table")
        elif header == "[agents]\n":
            found_agents = True
            updated = update_agents_body(body, agents, remove_agent_keys)
            if normalized_section_body(updated) == normalized_section_body(body):
                parts.append(header + body)
            else:
                parts.append(header + updated)
                changes.append("update Codex agent defaults")
        else:
            parts.append(header + body)
        cursor = match.end()
    parts.append(original[cursor:])
    updated = "".join(parts).rstrip() + "\n"
    if not found_agents:
        block = update_agents_body("", agents, remove_agent_keys)
        updated = updated.rstrip() + f"\n\n[agents]\n{block}"
        changes.append("update Codex agent defaults")

    return updated, list(dict.fromkeys(changes))


def atomic_write(path: Path, text: str) -> None:
    # Follows a symlinked config, keeps the file mode, fsyncs before replace.
    atomic_write_text(path, text, default_mode=0o600)


def main() -> int:
    args = parse_args()
    home = Path.home()
    hub = Path(os.environ.get("AGENT_HUB_ROOT", home / ".config" / "agent-hub"))
    config_path = home / ".codex" / "config.toml"
    try:
        (
            agents,
            plugins,
            prefixes,
            remove_agent_keys,
            forbidden_tables,
            forbidden_marketplaces,
        ) = load_policy(hub)
        original = config_path.read_text(encoding="utf-8")
        updated, changes = reconcile(
            original,
            agents,
            plugins,
            prefixes,
            remove_agent_keys,
            forbidden_tables,
            forbidden_marketplaces,
        )
    except (OSError, UnicodeError, ReconcileError) as exc:
        print(f"[ERROR] {exc}")
        return 1

    if not changes:
        print("Codex orchestration policy is already converged.")
        return 0
    if args.dry_run:
        for change in changes:
            print(f"would {change}")
        return 2
    atomic_write(config_path, updated)
    for change in changes:
        print(change)
    print("Codex orchestration policy converged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
