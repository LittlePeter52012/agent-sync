#!/usr/bin/env python3
"""Reconcile an explicit, private Hub allowlist with a Multica workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SyncError(RuntimeError):
    pass


@dataclass
class LocalSkill:
    name: str
    managed: bool
    source: Path | None
    description: str | None
    content: str | None
    files: dict[str, str]


@dataclass
class Snapshot:
    config: dict[str, Any]
    local_skills: dict[str, LocalSkill]
    remote_skills: dict[str, dict[str, Any]]
    remote_agents: dict[str, dict[str, Any]]
    remote_squads: dict[str, dict[str, Any]]
    squad_matches: dict[str, dict[str, Any]]
    changes: list[tuple[str, str, list[str]]]


IGNORED_PARTS = {".git", "__pycache__"}
IGNORED_NAMES = {".DS_Store"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check or explicitly apply private Hub state to Multica."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply allowlisted changes. The default is read-only.",
    )
    return parser.parse_args()


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SyncError(f"Missing Multica desired state: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"Cannot read valid Multica desired state: {path}") from exc
    if not isinstance(value, dict):
        raise SyncError("Multica desired state must be a JSON object")
    return value


def require_list(config: dict[str, Any], key: str) -> list[Any]:
    value = config.get(key, [])
    if not isinstance(value, list):
        raise SyncError(f"{key} must be a list")
    return value


def require_text(item: dict[str, Any], key: str, context: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SyncError(f"{context}.{key} must be a non-empty string")
    return value


def inside_hub(hub: Path, relative: str, context: str) -> Path:
    candidate = (hub / relative).resolve()
    try:
        candidate.relative_to(hub)
    except ValueError as exc:
        raise SyncError(f"{context} must stay inside the private Hub") from exc
    return candidate


def read_local_skill(
    hub: Path, item: dict[str, Any], seen_names: set[str]
) -> LocalSkill:
    name = require_text(item, "name", "skills[]")
    if name in seen_names:
        raise SyncError(f"Duplicate configured Skill name: {name}")
    seen_names.add(name)
    managed = item.get("managed", True)
    if not isinstance(managed, bool):
        raise SyncError(f"Skill '{name}' managed must be true or false")
    description = item.get("description")
    if description is not None and not isinstance(description, str):
        raise SyncError(f"Skill '{name}' description must be a string")
    if not managed:
        return LocalSkill(name, False, None, description, None, {})

    source_text = require_text(item, "source", f"Skill '{name}'")
    source = inside_hub(hub, source_text, f"Skill '{name}' source")
    if not source.is_dir():
        raise SyncError(f"Skill '{name}' source directory does not exist")
    skill_md = source / "SKILL.md"
    if not skill_md.is_file():
        raise SyncError(f"Skill '{name}' source is missing SKILL.md")
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SyncError(f"Skill '{name}' SKILL.md must be readable UTF-8") from exc

    files: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if any(part in IGNORED_PARTS for part in path.relative_to(source).parts):
            continue
        if not path.is_file() or path == skill_md:
            continue
        if path.name in IGNORED_NAMES or path.suffix in {".pyc", ".swp"}:
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(source)
        except ValueError as exc:
            raise SyncError(f"Skill '{name}' contains a file outside its source") from exc
        relative = path.relative_to(source).as_posix()
        try:
            files[relative] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SyncError(
                f"Skill '{name}' supporting file must be readable UTF-8: {relative}"
            ) from exc
    return LocalSkill(name, True, source, description, content, files)


def validate_config(hub: Path, config: dict[str, Any]) -> dict[str, LocalSkill]:
    if config.get("schema_version") != 1:
        raise SyncError("Unsupported Multica desired-state schema_version")
    minimum = config.get("minimum_multica_version")
    if not isinstance(minimum, str) or not re.fullmatch(r"\d+\.\d+\.\d+", minimum):
        raise SyncError("minimum_multica_version must use MAJOR.MINOR.PATCH")

    local_skills: dict[str, LocalSkill] = {}
    seen_names: set[str] = set()
    for raw in require_list(config, "skills"):
        if not isinstance(raw, dict):
            raise SyncError("Each skills entry must be an object")
        skill = read_local_skill(hub, raw, seen_names)
        local_skills[skill.name] = skill

    seen_agents: set[str] = set()
    for raw in require_list(config, "agent_skill_assignments"):
        if not isinstance(raw, dict):
            raise SyncError("Each agent_skill_assignments entry must be an object")
        agent = require_text(raw, "agent", "agent_skill_assignments[]")
        if agent in seen_agents:
            raise SyncError(f"Duplicate configured Agent name: {agent}")
        seen_agents.add(agent)
        skills = raw.get("skills")
        if not isinstance(skills, list) or any(
            not isinstance(name, str) or not name for name in skills
        ):
            raise SyncError(f"Agent '{agent}' skills must be a list of names")
        if len(skills) != len(set(skills)):
            raise SyncError(f"Agent '{agent}' contains duplicate Skill names")
        unknown = sorted(set(skills) - set(local_skills))
        if unknown:
            raise SyncError(f"Agent '{agent}' references undeclared Skills: {unknown}")

    seen_squads: set[str] = set()
    for raw in require_list(config, "squads"):
        if not isinstance(raw, dict):
            raise SyncError("Each squads entry must be an object")
        name = require_text(raw, "name", "squads[]")
        if name in seen_squads:
            raise SyncError(f"Duplicate configured Squad name: {name}")
        seen_squads.add(name)
        require_text(raw, "leader", f"Squad '{name}'")
        require_text(raw, "description", f"Squad '{name}'")
        instructions = require_text(raw, "instructions_file", f"Squad '{name}'")
        instructions_path = inside_hub(
            hub, instructions, f"Squad '{name}' instructions_file"
        )
        if not instructions_path.is_file():
            raise SyncError(f"Squad '{name}' instructions file does not exist")
        try:
            instructions_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SyncError(
                f"Squad '{name}' instructions must be readable UTF-8"
            ) from exc
        previous_name = raw.get("previous_name")
        if previous_name is not None and (
            not isinstance(previous_name, str) or not previous_name.strip()
        ):
            raise SyncError(f"Squad '{name}' previous_name must be a non-empty string")
    return local_skills


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise SyncError("Could not determine Multica CLI version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


class MulticaClient:
    def __init__(self, executable: str):
        self.executable = executable

    def _run(self, args: list[str], operation: str) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self.executable, *args],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise SyncError("Multica CLI is not available") from exc
        if result.returncode != 0:
            raise SyncError(f"Multica CLI failed while {operation}")
        return result

    def version(self) -> tuple[int, int, int]:
        return version_tuple(self._run(["--version"], "checking its version").stdout)

    def json(self, args: list[str], operation: str) -> Any:
        result = self._run(args, operation)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SyncError(f"Multica returned invalid JSON while {operation}") from exc

    def write(self, args: list[str], operation: str) -> None:
        self._run(args, operation)


def unique_by_name(
    records: Any, kind: str, *, allow_empty: bool = True
) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise SyncError(f"Multica {kind} list is not an array")
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            raise SyncError(f"Multica {kind} list contains an invalid entry")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise SyncError(f"Multica {kind} entry is missing a name")
        if name in result:
            raise SyncError(f"Duplicate Multica {kind} name: {name}")
        result[name] = item
    if not allow_empty and not result:
        raise SyncError(f"Multica returned no {kind} records")
    return result


def require_id(item: dict[str, Any], kind: str) -> str:
    identifier = item.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise SyncError(f"Multica {kind} entry is missing an ID")
    return identifier


def file_map(files: Any, skill_name: str) -> dict[str, dict[str, Any]]:
    if not isinstance(files, list):
        raise SyncError(f"Multica Skill '{skill_name}' files are invalid")
    result: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise SyncError(f"Multica Skill '{skill_name}' has an invalid file")
        path = item.get("path")
        if not isinstance(path, str) or not path:
            raise SyncError(f"Multica Skill '{skill_name}' file is missing a path")
        if path in result:
            raise SyncError(f"Duplicate file path in Multica Skill '{skill_name}': {path}")
        result[path] = item
    return result


def normalized_instructions(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.rstrip("\n")


def collect_snapshot(
    hub: Path,
    config: dict[str, Any],
    local_skills: dict[str, LocalSkill],
    client: MulticaClient,
) -> Snapshot:
    minimum = version_tuple(config["minimum_multica_version"])
    if client.version() < minimum:
        raise SyncError(
            f"Multica {config['minimum_multica_version']} or newer is required"
        )

    remote_skills = unique_by_name(
        client.json(["skill", "list", "--output", "json"], "listing Skills"),
        "Skill",
    )
    remote_agents = unique_by_name(
        client.json(["agent", "list", "--output", "json"], "listing Agents"),
        "Agent",
    )
    remote_squads = unique_by_name(
        client.json(["squad", "list", "--output", "json"], "listing Squads"),
        "Squad",
    )
    changes: list[tuple[str, str, list[str]]] = []

    for name, local in local_skills.items():
        remote = remote_skills.get(name)
        if remote is None:
            if not local.managed:
                raise SyncError(f"Unmanaged Multica Skill is missing: {name}")
            changes.append(("Skill", name, ["missing"]))
            continue
        if not local.managed:
            continue
        identifier = require_id(remote, "Skill")
        detail = client.json(
            ["skill", "get", identifier, "--output", "json"],
            f"reading Skill '{name}'",
        )
        if not isinstance(detail, dict):
            raise SyncError(f"Multica Skill '{name}' details are invalid")
        remote_skills[name] = detail
        fields: list[str] = []
        if detail.get("content") != local.content:
            fields.append("content")
        if local.description is not None and detail.get("description") != local.description:
            fields.append("description")
        remote_files = file_map(detail.get("files", []), name)
        remote_contents = {
            path: item.get("content") for path, item in remote_files.items()
        }
        if remote_contents != local.files:
            fields.append("files")
        if fields:
            changes.append(("Skill", name, fields))

    assignment_entries = require_list(config, "agent_skill_assignments")
    for raw in assignment_entries:
        agent_name = raw["agent"]
        remote = remote_agents.get(agent_name)
        if remote is None:
            raise SyncError(f"Multica Agent is missing: {agent_name}")
        identifier = require_id(remote, "Agent")
        detail = client.json(
            ["agent", "get", identifier, "--output", "json"],
            f"reading Agent '{agent_name}'",
        )
        if not isinstance(detail, dict):
            raise SyncError(f"Multica Agent '{agent_name}' details are invalid")
        remote_agents[agent_name] = detail
        remote_skill_names = detail.get("skills", [])
        if not isinstance(remote_skill_names, list):
            raise SyncError(f"Multica Agent '{agent_name}' Skills are invalid")
        actual = []
        for skill in remote_skill_names:
            if not isinstance(skill, dict) or not isinstance(skill.get("name"), str):
                raise SyncError(f"Multica Agent '{agent_name}' has an invalid Skill")
            actual.append(skill["name"])
        if sorted(actual) != sorted(raw["skills"]):
            changes.append(("Agent", agent_name, ["skills"]))

    squad_matches: dict[str, dict[str, Any]] = {}
    for raw in require_list(config, "squads"):
        name = raw["name"]
        aliases = {name}
        if raw.get("previous_name"):
            aliases.add(raw["previous_name"])
        matches = [item for alias in aliases if (item := remote_squads.get(alias))]
        if len(matches) != 1:
            raise SyncError(
                f"Squad '{name}' must resolve to exactly one current or previous name"
            )
        summary = matches[0]
        identifier = require_id(summary, "Squad")
        detail = client.json(
            ["squad", "get", identifier, "--output", "json"],
            f"reading Squad '{name}'",
        )
        if not isinstance(detail, dict):
            raise SyncError(f"Multica Squad '{name}' details are invalid")
        squad_matches[name] = detail
        leader_name = raw["leader"]
        leader = remote_agents.get(leader_name)
        if leader is None:
            raise SyncError(f"Multica Agent is missing: {leader_name}")
        leader_id = require_id(leader, "Agent")
        instructions_path = inside_hub(
            hub, raw["instructions_file"], f"Squad '{name}' instructions_file"
        )
        instructions = instructions_path.read_text(encoding="utf-8")
        fields: list[str] = []
        if detail.get("name") != name:
            fields.append("name")
        if detail.get("leader_id") != leader_id:
            fields.append("leader")
        if detail.get("description") != raw["description"]:
            fields.append("description")
        if normalized_instructions(detail.get("instructions")) != normalized_instructions(
            instructions
        ):
            fields.append("instructions")
        if fields:
            changes.append(("Squad", name, fields))

    return Snapshot(
        config=config,
        local_skills=local_skills,
        remote_skills=remote_skills,
        remote_agents=remote_agents,
        remote_squads=remote_squads,
        squad_matches=squad_matches,
        changes=changes,
    )


def print_changes(changes: list[tuple[str, str, list[str]]], prefix: str) -> None:
    for kind, name, fields in changes:
        print(f"[{prefix}] {kind} '{name}': {', '.join(fields)}")


def apply_skills(snapshot: Snapshot, client: MulticaClient) -> None:
    for name, local in snapshot.local_skills.items():
        if not local.managed:
            continue
        remote = snapshot.remote_skills.get(name)
        if remote is None:
            args = [
                "skill",
                "create",
                "--name",
                name,
                "--content-file",
                str(local.source / "SKILL.md"),
                "--output",
                "json",
            ]
            if local.description is not None:
                args[4:4] = ["--description", local.description]
            client.write(args, f"creating Skill '{name}'")
            print(f"[APPLY] Skill '{name}': create")
            created = refresh_skill_index(client).get(name)
            if created is None:
                raise SyncError(f"Multica Skill is still missing after create: {name}")
            identifier = require_id(created, "Skill")
            for path in local.files:
                client.write(
                    [
                        "skill",
                        "files",
                        "upsert",
                        identifier,
                        "--path",
                        path,
                        "--content-file",
                        str(local.source / path),
                        "--output",
                        "json",
                    ],
                    f"upserting a file in Skill '{name}'",
                )
                print(f"[APPLY] Skill '{name}': upsert file {path}")
            continue

        identifier = require_id(remote, "Skill")
        update_args = ["skill", "update", identifier]
        update_needed = False
        if remote.get("content") != local.content:
            update_args.extend(["--content-file", str(local.source / "SKILL.md")])
            update_needed = True
        if local.description is not None and remote.get("description") != local.description:
            update_args.extend(["--description", local.description])
            update_needed = True
        if update_needed:
            update_args.extend(["--output", "json"])
            client.write(update_args, f"updating Skill '{name}'")
            print(f"[APPLY] Skill '{name}': content")

        remote_files = file_map(remote.get("files", []), name)
        for path, item in remote_files.items():
            if path in local.files:
                continue
            file_id = require_id(item, "Skill file")
            client.write(
                ["skill", "files", "delete", identifier, file_id],
                f"deleting a stale file from Skill '{name}'",
            )
            print(f"[APPLY] Skill '{name}': delete file {path}")
        for path, content in local.files.items():
            remote_file = remote_files.get(path)
            if remote_file is not None and remote_file.get("content") == content:
                continue
            client.write(
                [
                    "skill",
                    "files",
                    "upsert",
                    identifier,
                    "--path",
                    path,
                    "--content-file",
                    str(local.source / path),
                    "--output",
                    "json",
                ],
                f"upserting a file in Skill '{name}'",
            )
            print(f"[APPLY] Skill '{name}': upsert file {path}")


def refresh_skill_index(client: MulticaClient) -> dict[str, dict[str, Any]]:
    return unique_by_name(
        client.json(["skill", "list", "--output", "json"], "refreshing Skills"),
        "Skill",
    )


def apply_agent_skills(
    snapshot: Snapshot,
    client: MulticaClient,
    skill_index: dict[str, dict[str, Any]],
) -> None:
    for raw in require_list(snapshot.config, "agent_skill_assignments"):
        name = raw["agent"]
        agent = snapshot.remote_agents[name]
        actual = sorted(
            item["name"]
            for item in agent.get("skills", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        desired = sorted(raw["skills"])
        if actual == desired:
            continue
        skill_ids = [
            require_id(skill_index[skill_name], "Skill") for skill_name in raw["skills"]
        ]
        client.write(
            [
                "agent",
                "skills",
                "set",
                require_id(agent, "Agent"),
                "--skill-ids",
                ",".join(skill_ids),
                "--output",
                "json",
            ],
            f"setting Skills for Agent '{name}'",
        )
        print(f"[APPLY] Agent '{name}': skills")


def apply_squads(snapshot: Snapshot, hub: Path, client: MulticaClient) -> None:
    for raw in require_list(snapshot.config, "squads"):
        name = raw["name"]
        remote = snapshot.squad_matches[name]
        leader = snapshot.remote_agents[raw["leader"]]
        instructions = inside_hub(
            hub, raw["instructions_file"], f"Squad '{name}' instructions_file"
        ).read_text(encoding="utf-8")
        if (
            remote.get("name") == name
            and remote.get("leader_id") == require_id(leader, "Agent")
            and remote.get("description") == raw["description"]
            and normalized_instructions(remote.get("instructions"))
            == normalized_instructions(instructions)
        ):
            continue
        client.write(
            [
                "squad",
                "update",
                require_id(remote, "Squad"),
                "--name",
                name,
                "--leader",
                raw["leader"],
                "--description",
                raw["description"],
                "--instructions",
                instructions,
                "--output",
                "json",
            ],
            f"updating Squad '{name}'",
        )
        print(f"[APPLY] Squad '{name}': managed fields")


def main() -> int:
    args = parse_args()
    hub = Path(os.environ.get("AGENT_HUB_ROOT", "~/.config/agent-hub")).expanduser()
    try:
        hub = hub.resolve()
        config = load_json_object(hub / "multica" / "desired-state.json")
        local_skills = validate_config(hub, config)
        client = MulticaClient(os.environ.get("MULTICA_BIN", "multica"))
        snapshot = collect_snapshot(hub, config, local_skills, client)
        if not args.apply:
            if snapshot.changes:
                print_changes(snapshot.changes, "DRIFT")
                print("Multica drift detected. Run 'agent-sync multica --apply' to apply.")
                return 2
            print("Multica managed state is converged.")
            return 0

        if not snapshot.changes:
            print("Multica managed state is already converged.")
            return 0
        apply_skills(snapshot, client)
        skill_index = refresh_skill_index(client)
        for name in local_skills:
            if name not in skill_index:
                raise SyncError(f"Multica Skill is still missing after apply: {name}")
        apply_agent_skills(snapshot, client, skill_index)
        apply_squads(snapshot, hub, client)

        final = collect_snapshot(hub, config, local_skills, client)
        if final.changes:
            print_changes(final.changes, "DRIFT")
            raise SyncError("Multica state still has drift after apply")
        print("Multica managed state is converged.")
        return 0
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
