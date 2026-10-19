#!/usr/bin/env python3
"""Sync shared MCP servers into Claude Code using the official CLI.

The Claude Code CLI owns its MCP storage format, so this script intentionally
uses `claude mcp add` instead of editing private config files directly.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from capability_resolver import mcp_identity_matches


PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value))


def resolve(value: object, donors: dict[str, str]) -> object | None:
    if is_placeholder(value):
        key = PLACEHOLDER_RE.match(value).group(1)  # type: ignore[union-attr]
        return donors.get(key) or os.environ.get(key)
    return value


def donor_maps() -> dict[str, dict[str, str]]:
    """Read only concrete values already configured for the same shared MCP."""
    result: dict[str, dict[str, str]] = {}
    for path in (
        Path.home() / ".cursor" / "mcp.json",
        Path.home() / ".gemini" / "settings.json",
        Path.home() / ".gemini" / "config" / "mcp_config.json",
    ):
        if not path.exists():
            continue
        try:
            servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
        except json.JSONDecodeError:
            continue
        for name, config in servers.items():
            if not isinstance(config, dict):
                continue
            values = result.setdefault(name.lower(), {})
            for key, value in (config.get("env") or {}).items():
                if isinstance(value, str) and not is_placeholder(value):
                    values[str(key)] = value
                    if key == "OPENAPI_MCP_HEADERS":
                        values.setdefault("ANYTYPE_MCP_HEADERS", value)
            for index, value in enumerate(config.get("args") or []):
                if isinstance(value, str) and not is_placeholder(value):
                    values.setdefault(f"ARG_{index}", value)
    return result


def secret_donors_allowed(hub: Path | None) -> bool:
    if hub is None:
        return True
    policy_path = hub / "policies" / "tool-scopes.json"
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    secrets = policy.get("secrets", {}) if isinstance(policy, dict) else {}
    targets = secrets.get("targets", {}) if isinstance(secrets, dict) else {}
    target = targets.get("claude", {}) if isinstance(targets, dict) else {}
    if not isinstance(target, dict) or "allow_secret_donors" not in target:
        return True
    return target.get("allow_secret_donors") is True


def concrete_env(env: dict[str, Any], donors: dict[str, str]) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for k, v in env.items():
        value = resolve(v, donors)
        if value is None and is_placeholder(v):
            value = donors.get(str(k))
        if value is None:
            return None
        if str(value) != "":
            out[str(k)] = str(value)
    return out


def concrete_args(args: list[Any], donors: dict[str, str]) -> list[str] | None:
    out: list[str] = []
    for index, arg in enumerate(args):
        value = resolve(arg, donors)
        if value is None:
            value = donors.get(f"ARG_{index}") if is_placeholder(arg) else None
        if value is None:
            return None
        out.append(str(value))
    return out


def configured_names(claude_bin: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            [claude_bin, "mcp", "list"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except FileNotFoundError:
        return {}

    names: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line and not line.startswith("Checking "):
            configured = line.split(":", 1)[0].strip()
            names[configured.lower()] = configured
    return names


def configured_servers() -> dict[str, tuple[str, dict[str, Any]]]:
    path = Path.home() / ".claude.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    servers = document.get("mcpServers", {}) if isinstance(document, dict) else {}
    if not isinstance(servers, dict):
        return {}
    return {
        name.casefold(): (name, config)
        for name, config in servers.items()
        if isinstance(name, str) and isinstance(config, dict)
    }


def configuration_matches(
    actual: dict[str, Any],
    canonical: dict[str, Any],
) -> bool:
    if not mcp_identity_matches(actual, canonical):
        return False
    actual_env = actual.get("env") or actual.get("environment") or {}
    canonical_env = canonical.get("env") or {}
    if isinstance(canonical_env, dict) and not set(canonical_env).issubset(
        actual_env if isinstance(actual_env, dict) else {}
    ):
        return False
    actual_headers = actual.get("headers") or actual.get("http_headers") or {}
    canonical_headers = canonical.get("headers") or {}
    if isinstance(canonical_headers, dict) and not set(canonical_headers).issubset(
        actual_headers if isinstance(actual_headers, dict) else {}
    ):
        return False
    return actual.get("enabled", True) is not False and actual.get("disabled") is not True


def restore_server(claude_bin: str, scope: str, name: str, config: dict[str, Any]) -> bool:
    """Put back the exact entry removed before a failed replacement add."""
    result = subprocess.run(
        [claude_bin, "mcp", "add-json", "--scope", scope, name, json.dumps(config)],
        text=True,
        stdout=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def command_for(name: str, cfg: dict[str, Any], scope: str, donors: dict[str, str]) -> list[str] | None:
    typ = cfg.get("type", "stdio")
    if typ == "http" or cfg.get("url"):
        url = resolve(cfg.get("url"), donors)
        if not url:
            return None
        cmd = ["mcp", "add", "--scope", scope, "--transport", "http", name, str(url)]
        for key, value in (cfg.get("headers") or {}).items():
            if value is not None and not is_placeholder(value):
                cmd.extend(["--header", f"{key}: {value}"])
        return cmd

    command = resolve(cfg.get("command"), donors)
    if not command:
        return None
    args = concrete_args(cfg.get("args") or [], donors)
    if args is None:
        return None

    cmd = ["mcp", "add", "--scope", scope, name]
    env = concrete_env(cfg.get("env") or {}, donors)
    if env is None:
        return None
    for key, value in env.items():
        cmd.extend(["--env", f"{key}={value}"])
    cmd.extend(["--", str(command), *args])
    return cmd


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    argv = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
    retired_path: Optional[Path] = None
    exclude_path: Optional[Path] = None
    hub_path: Optional[Path] = None
    if "--retired" in argv:
        index = argv.index("--retired")
        if index + 1 >= len(argv):
            print("--retired requires a file", file=sys.stderr)
            return 1
        retired_path = Path(argv[index + 1]).expanduser()
        del argv[index : index + 2]
    if "--exclude-file" in argv:
        index = argv.index("--exclude-file")
        if index + 1 >= len(argv):
            print("--exclude-file requires a file", file=sys.stderr)
            return 1
        exclude_path = Path(argv[index + 1]).expanduser()
        del argv[index : index + 2]
    if "--hub" in argv:
        index = argv.index("--hub")
        if index + 1 >= len(argv):
            print("--hub requires a directory", file=sys.stderr)
            return 1
        hub_path = Path(argv[index + 1]).expanduser()
        del argv[index : index + 2]
    if len(argv) != 1:
        print(
            f"Usage: {sys.argv[0]} <canonical.json> [--retired file] "
            "[--exclude-file file] [--hub directory] [--dry-run]",
            file=sys.stderr,
        )
        return 1

    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    scope = os.environ.get("CLAUDE_MCP_SYNC_SCOPE", "user")
    canonical = json.loads(Path(argv[0]).expanduser().read_text(encoding="utf-8"))
    existing = configured_names(claude_bin) if not dry_run else set()
    actual_servers = configured_servers() if not dry_run else {}
    donors = donor_maps() if secret_donors_allowed(hub_path) else {}
    excluded: set[str] = set()
    if exclude_path and exclude_path.exists():
        excluded = {
            line.strip().casefold()
            for line in exclude_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    retired: list[str] = []
    if retired_path and retired_path.exists():
        retired_doc = json.loads(retired_path.read_text(encoding="utf-8"))
        retired = [str(name) for name in retired_doc.get("retiredServers", [])]
    retired_removed = 0
    for name in retired:
        if not dry_run and name.lower() not in existing:
            continue
        configured_name = existing.get(name.lower(), name)
        remove_cmd = [
            claude_bin,
            "mcp",
            "remove",
            "--scope",
            scope,
            configured_name,
        ]
        if dry_run:
            print("  would run: " + " ".join(shlex.quote(part) for part in remove_cmd))
            retired_removed += 1
        elif subprocess.run(remove_cmd, text=True, check=False).returncode == 0:
            retired_removed += 1

    added = skipped = existing_count = replaced = unchanged = 0
    skipped_names: list[str] = []
    restore_failed: list[str] = []
    for name, cfg in (canonical.get("mcpServers") or {}).items():
        if name.casefold() in excluded:
            if not dry_run and name.casefold() in existing:
                subprocess.run(
                    [
                        claude_bin,
                        "mcp",
                        "remove",
                        "--scope",
                        scope,
                        existing[name.casefold()],
                    ],
                    text=True,
                    check=False,
                )
            continue
        cmd = command_for(name, cfg, scope, donors.get(name.lower(), {}))
        if not cmd:
            skipped += 1
            skipped_names.append(name)
            continue
        snapshot: tuple[str, dict[str, Any]] | None = None
        if name.lower() in existing:
            existing_count += 1
            actual = actual_servers.get(name.casefold())
            if actual and configuration_matches(actual[1], cfg):
                unchanged += 1
                continue
            if actual is None:
                # Without a snapshot a failed add could not be undone, so
                # leave the configured server exactly as it is.
                skipped += 1
                skipped_names.append(name)
                continue
            snapshot = actual
            remove_cmd = [
                claude_bin,
                "mcp",
                "remove",
                "--scope",
                scope,
                existing[name.lower()],
            ]
            result = subprocess.run(remove_cmd, text=True, check=False)
            if result.returncode != 0:
                skipped += 1
                skipped_names.append(name)
                continue
            replaced += 1
        full_cmd = [claude_bin, *cmd]
        if dry_run:
            display = [shlex.quote(part) for part in full_cmd]
            for index, part in enumerate(full_cmd[:-1]):
                if part == "--env":
                    display[index + 1] = "'<redacted env>'"
            print("  would run: " + " ".join(display))
            added += 1
            continue
        result = subprocess.run(full_cmd, text=True, check=False)
        if result.returncode == 0:
            added += 1
        else:
            skipped += 1
            skipped_names.append(name)
            if snapshot is not None:
                replaced -= 1
                if not restore_server(claude_bin, scope, *snapshot):
                    restore_failed.append(snapshot[0])

    print(
        f"  claude mcp: +{added} replaced={replaced} "
        f"retired={retired_removed} existing={existing_count} "
        f"unchanged={unchanged} skipped={skipped}"
    )
    if skipped_names:
        print("  skipped: " + ", ".join(skipped_names))
    claude_config = Path.home() / ".claude.json"
    if not dry_run and claude_config.exists():
        claude_config.chmod(0o600)
    if restore_failed:
        print(
            "  ERROR: could not restore after a failed add: "
            + ", ".join(restore_failed),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
