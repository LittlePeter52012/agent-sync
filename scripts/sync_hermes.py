#!/usr/bin/env python3
"""Point Hermes at its own Hub Skill directory.

``sync-skills.sh`` links the Hub Skills meant for Hermes (the manifest
allowlist minus ``targets.hermes.exclude``) into ``$HERMES_HOME/hub-skills``.
This script registers that directory in ``skills.external_dirs`` of the
default config and of every profile.

Older versions registered the shared ``~/.agents/skills`` directory instead.
That entry is removed only when Hermes loses nothing by it: each entry there
must be a Hub link now provided through ``hub-skills``, a Hub Skill withheld
from Hermes that Hermes keeps its own copy of, a Hub link already dropped from
the manifest, or a non-Hub entry whose Skills Hermes already has. Otherwise the
entry stays and the script lists what is in the way.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scripts.atomic_file import atomic_write_text
    from scripts.capability_resolver import resolve_target_policy
    from scripts.hub_manifest import read_manifest_skills
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from atomic_file import atomic_write_text
    from capability_resolver import resolve_target_policy
    from hub_manifest import read_manifest_skills


HOME = Path.home()
HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", HOME / ".hermes")
).expanduser()
HUB_ROOT = Path(
    os.environ.get("AGENT_HUB_ROOT", HOME / ".config" / "agent-hub")
).expanduser()
CONFIG = HERMES_HOME / "config.yaml"
HUB_SKILLS = HERMES_HOME / "hub-skills"
SHARED_SKILLS = HOME / ".agents" / "skills"
SHARED_DISPLAY = "~/.agents/skills"
RUN_ID = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"

# Directories Hermes never scans for Skills (agent/skill_utils.py).
EXCLUDED_SKILL_DIRS = frozenset(
    {
        ".git", ".github", ".hub", ".archive", ".curator_backups",
        ".venv", "venv", "node_modules", "site-packages", "__pycache__",
    }
)
SKILL_SUPPORT_DIRS = frozenset({"references", "templates", "assets", "scripts"})


def display_path(path: Path) -> str:
    try:
        return "~/" + path.relative_to(HOME).as_posix()
    except ValueError:
        return str(path)


DISPLAY_PATH = display_path(HUB_SKILLS)
TARGET = HUB_SKILLS.resolve()
SHARED_TARGET = SHARED_SKILLS.resolve()


def config_paths() -> list[Path]:
    paths = [CONFIG] if CONFIG.exists() else []
    profiles = HERMES_HOME / "profiles"
    if profiles.is_dir():
        paths.extend(sorted(profiles.glob("*/config.yaml")))
    return paths


def normalized_path(value: str) -> Path | None:
    value = value.strip()
    if not value:
        return None
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        value = value[1:-1]
    expanded = os.path.expandvars(os.path.expanduser(value))
    try:
        return Path(expanded).resolve()
    except (OSError, RuntimeError):
        return Path(expanded).absolute()


def split_yaml_comment(value: str) -> tuple[str, str]:
    quote = ""
    escaped = False
    depth = 0
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote:
            if character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "[":
            depth += 1
        elif character == "]":
            depth = max(0, depth - 1)
        elif (
            character == "#"
            and depth == 0
            and (index == 0 or value[index - 1].isspace())
        ):
            return value[:index].strip(), value[index:].strip()
    return value.strip(), ""


def parse_flow_list(value: str) -> list[str] | None:
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []

    entries: list[str] = []
    token: list[str] = []
    quote = ""
    escaped = False
    for character in inner:
        if escaped:
            token.append(character)
            escaped = False
            continue
        if quote:
            token.append(character)
            if character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
            token.append(character)
        elif character == ",":
            entry = "".join(token).strip()
            if not entry:
                raise ValueError("empty Hermes external_dirs flow-list entry")
            entries.append(entry)
            token = []
        elif character in {"[", "]", "{", "}"}:
            raise ValueError("nested Hermes external_dirs flow values are unsupported")
        else:
            token.append(character)
    if quote:
        raise ValueError("unterminated quote in Hermes external_dirs")
    entry = "".join(token).strip()
    if not entry:
        raise ValueError("empty Hermes external_dirs flow-list entry")
    entries.append(entry)
    return entries


def external_dir_entries(lines: list[str], start: int, end: int) -> list[str]:
    match = re.match(
        r"^(\s+)external_dirs:\s*(.*?)\s*$",
        lines[start].rstrip("\n"),
    )
    if not match:
        return []
    key_indent = len(match.group(1))
    inline, _ = split_yaml_comment(match.group(2))
    flow_entries = parse_flow_list(inline)
    if flow_entries is not None:
        return flow_entries
    if inline and inline not in {"null", "~"}:
        return [inline]
    entries: list[str] = []
    for line in lines[start + 1 : end]:
        stripped = line.lstrip()
        indentation = len(line) - len(stripped)
        if indentation > key_indent and stripped.startswith("- "):
            entries.append(stripped[2:].strip())
        elif line.strip() and indentation <= key_indent:
            break
    return entries


def find_sections(lines: list[str]) -> tuple[int | None, int, int | None]:
    skills_index = None
    for index, line in enumerate(lines):
        if re.match(r"^skills:\s*(?:#.*)?$", line.rstrip("\n")):
            skills_index = index
            break
    if skills_index is None:
        return None, len(lines), None

    section_end = len(lines)
    for index in range(skills_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            section_end = index
            break

    external_index = None
    for index in range(skills_index + 1, section_end):
        if re.match(r"^\s+external_dirs:", lines[index]):
            external_index = index
            break
    return skills_index, section_end, external_index


def registered_dirs(text: str) -> list[Path]:
    lines = text.splitlines(keepends=True)
    _, section_end, external_index = find_sections(lines)
    if external_index is None:
        return []
    return [
        entry
        for value in external_dir_entries(lines, external_index, section_end)
        if (entry := normalized_path(value)) is not None
    ]


def is_registered(text: str, target: Path = TARGET) -> bool:
    return target in registered_dirs(text)


def updated_text(
    text: str,
    display: str = DISPLAY_PATH,
    target: Path = TARGET,
) -> str:
    if is_registered(text, target):
        return text

    lines = text.splitlines(keepends=True)
    skills_index, section_end, external_index = find_sections(lines)
    if skills_index is None:
        prefix = "" if not text or text.endswith("\n") else "\n"
        return text + prefix + (
            "skills:\n"
            "  external_dirs:\n"
            f"    - {display}\n"
        )

    if external_index is None:
        child_indent = "  "
        for line in lines[skills_index + 1 : section_end]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indentation = line[: len(line) - len(line.lstrip())]
            if indentation:
                child_indent = indentation
                break
        list_indent = child_indent + "  "
        lines[skills_index + 1 : skills_index + 1] = [
            f"{child_indent}external_dirs:\n",
            f"{list_indent}- {display}\n",
        ]
        return "".join(lines)

    external_line = lines[external_index]
    match = re.match(
        r"^(\s+)(external_dirs:)\s*(.*?)\s*(\n?)$",
        external_line,
    )
    assert match is not None
    key_indent = match.group(1)
    list_indent = key_indent + "  "
    inline, comment = split_yaml_comment(match.group(3))
    flow_entries = parse_flow_list(inline)
    if flow_entries is not None:
        lines[external_index : external_index + 1] = [
            f"{key_indent}external_dirs:"
            + (f" {comment}" if comment else "")
            + "\n",
            *[f"{list_indent}- {entry}\n" for entry in flow_entries],
            f"{list_indent}- {display}\n",
        ]
        return "".join(lines)
    if inline in {"null", "~"}:
        lines[external_index : external_index + 1] = [
            f"{key_indent}external_dirs:"
            + (f" {comment}" if comment else "")
            + "\n",
            f"{list_indent}- {display}\n",
        ]
        return "".join(lines)
    if inline:
        lines[external_index : external_index + 1] = [
            f"{key_indent}external_dirs:"
            + (f" {comment}" if comment else "")
            + "\n",
            f"{list_indent}- {inline}\n",
            f"{list_indent}- {display}\n",
        ]
        return "".join(lines)

    insertion = external_index + 1
    existing_indent = None
    while insertion < section_end:
        line = lines[insertion]
        stripped = line.lstrip()
        indentation = len(line) - len(stripped)
        if indentation > len(key_indent) and stripped.startswith("- "):
            if existing_indent is None:
                existing_indent = line[:indentation]
            insertion += 1
            continue
        break
    lines[insertion:insertion] = [
        f"{existing_indent or list_indent}- {display}\n"
    ]
    return "".join(lines)


def without_entry(text: str, target: Path) -> str:
    """``text`` with every ``external_dirs`` entry that resolves to ``target`` removed."""
    lines = text.splitlines(keepends=True)
    _, section_end, external_index = find_sections(lines)
    if external_index is None:
        return text
    match = re.match(
        r"^(\s+)(external_dirs:)\s*(.*?)\s*(\n?)$",
        lines[external_index],
    )
    assert match is not None
    key_indent = match.group(1)
    list_indent = key_indent + "  "
    inline, comment = split_yaml_comment(match.group(3))
    suffix = f" {comment}" if comment else ""

    def kept(value: str) -> bool:
        return normalized_path(value) != target

    if inline and inline not in {"null", "~"}:
        flow_entries = parse_flow_list(inline)
        entries = flow_entries if flow_entries is not None else [inline]
        remaining = [entry for entry in entries if kept(entry)]
        if len(remaining) == len(entries):
            return text
        lines[external_index : external_index + 1] = (
            [f"{key_indent}external_dirs:{suffix}\n"]
            + [f"{list_indent}- {entry}\n" for entry in remaining]
            if remaining
            else [f"{key_indent}external_dirs: []{suffix}\n"]
        )
        return "".join(lines)

    index = external_index + 1
    removed = False
    remaining_items = 0
    while index < section_end:
        line = lines[index]
        stripped = line.lstrip()
        indentation = len(line) - len(stripped)
        if indentation > len(key_indent) and stripped.startswith("- "):
            if not kept(stripped[2:].strip()):
                del lines[index]
                section_end -= 1
                removed = True
                continue
            remaining_items += 1
        elif line.strip() and indentation <= len(key_indent):
            break
        index += 1
    if removed and not remaining_items:
        lines[external_index] = f"{key_indent}external_dirs: []{suffix}\n"
    return "".join(lines) if removed else text


def _declared_name(skill_md: Path) -> str | None:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    frontmatter = re.match(r"\A---\r?\n(.*?)^---", text, re.DOTALL | re.MULTILINE)
    if frontmatter is None:
        return None
    name = re.search(
        r"^name:[ \t]*['\"]?([^'\"\r\n]+?)['\"]?[ \t]*\r?$",
        frontmatter.group(1),
        re.MULTILINE,
    )
    return name.group(1) if name else None


def skill_names(root: Path) -> list[tuple[str, frozenset[str]]]:
    """Every Skill Hermes would find under ``root``: (folder, names it answers to)."""
    found: list[tuple[str, frozenset[str]]] = []
    if not root.is_dir():
        return found
    seen: set[str] = set()
    for current, dirs, files in os.walk(root, followlinks=True):
        real = os.path.realpath(current)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        has_skill = "SKILL.md" in files
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in EXCLUDED_SKILL_DIRS
            and not (has_skill and name in SKILL_SUPPORT_DIRS)
        )
        if has_skill:
            folder = Path(current).name
            declared = _declared_name(Path(current) / "SKILL.md")
            found.append((folder, frozenset({folder, declared} - {None})))
    return found


def _names_under(roots: list[Path]) -> set[str]:
    return {
        name
        for root in roots
        for _folder, names in skill_names(root)
        for name in names
    }


def _hub_managed(link: Path, name: str) -> bool:
    source = HUB_ROOT / "skills" / name
    return link.is_symlink() and os.path.realpath(link) == os.path.realpath(source)


@dataclass
class DropReport:
    """What Hermes would stop seeing if ``~/.agents/skills`` left its config."""

    provided: list[str] = field(default_factory=list)
    own_copies: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    withheld_visible: list[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.lost

    def summary(self) -> str:
        parts = [f"{len(self.provided)} now via {DISPLAY_PATH}"]
        if self.own_copies:
            parts.append(f"{len(self.own_copies)} Hermes already has")
        if self.stale:
            parts.append(f"{len(self.stale)} no longer in the manifest")
        return ", ".join(parts)


def drop_report(config: Path, text: str) -> DropReport:
    report = DropReport()
    resolution = resolve_target_policy("hermes", HOME, HUB_ROOT)
    if resolution.findings:
        report.lost.extend(
            f"(Hub policy is invalid: {finding})" for finding in resolution.findings
        )
        return report
    withheld = {
        skill: resolution.skill_owners.get(skill, "policy")
        for skill in resolution.suppressed_skills
    }
    listed = set(read_manifest_skills(HUB_ROOT / "manifest.yaml"))
    remaining_dirs = [config.parent / "skills"] + [
        entry for entry in registered_dirs(text)
        if entry not in {TARGET, SHARED_TARGET}
    ]
    own = _names_under(remaining_dirs)
    available = own | _names_under([HUB_SKILLS])
    if not SHARED_SKILLS.is_dir():
        return report
    for entry in sorted(SHARED_SKILLS.iterdir(), key=lambda path: path.name):
        name = entry.name
        if _hub_managed(entry, name):
            if _hub_managed(HUB_SKILLS / name, name):
                report.provided.append(name)
            elif name.casefold() in withheld:
                report.withheld_visible.append(name)
                if name in own:
                    report.own_copies.append(name)
                else:
                    owner = withheld[name.casefold()]
                    reason = "excluded for Hermes" if owner == "excluded" else f"owned by {owner}"
                    report.lost.append(f"{name} ({reason}; Hermes has no own copy)")
            elif name not in listed:
                report.stale.append(name)
            else:
                report.lost.append(f"{name} (not linked in {DISPLAY_PATH} yet)")
            continue
        for folder, names in skill_names(entry):
            label = f"{folder} (from {SHARED_DISPLAY}/{name})"
            if names & available:
                report.own_copies.append(label)
            else:
                report.lost.append(label)
    return report


def _label(config: Path) -> str:
    try:
        return str(config.relative_to(HERMES_HOME))
    except ValueError:
        return str(config)


def plan(config: Path) -> tuple[str, str, list[str], list[str]]:
    """(original, updated, actions, warnings) for one Hermes config."""
    original = config.read_text(encoding="utf-8")
    updated = original
    actions: list[str] = []
    warnings: list[str] = []
    if not is_registered(updated):
        updated = updated_text(updated)
        actions.append(f"registered {DISPLAY_PATH}")
    if is_registered(updated, SHARED_TARGET):
        report = drop_report(config, updated)
        if report.safe:
            updated = without_entry(updated, SHARED_TARGET)
            actions.append(f"removed {SHARED_DISPLAY} ({report.summary()})")
        else:
            warnings.append(
                f"kept {SHARED_DISPLAY}; Hermes would lose: " + ", ".join(report.lost)
            )
            if report.withheld_visible:
                warnings.append(
                    "while it stays, Hermes still sees Hub Skills withheld from it: "
                    + ", ".join(report.withheld_visible)
                )
    return original, updated, actions, warnings


def backup(config: Path) -> Path:
    """Copy ``config`` under the Hub's ``.sync-backups`` before it is replaced."""
    try:
        relative = config.relative_to(HERMES_HOME)
    except ValueError:
        relative = Path(config.name)
    destination = HUB_ROOT / ".sync-backups" / RUN_ID / "hermes" / relative
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(config, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift, write nothing")
    mode.add_argument("--dry-run", action="store_true", help="show planned edits, write nothing")
    args = parser.parse_args()

    configs = config_paths()
    if not configs:
        print("[hermes] not configured — skip")
        return 0

    drifted: list[tuple[Path, str, list[str]]] = []
    for config in configs:
        original, updated, actions, warnings = plan(config)
        for warning in warnings:
            print(f"[hermes] ⚠ {_label(config)}: {warning}", file=sys.stderr)
        if updated != original:
            drifted.append((config, updated, actions))

    if not drifted:
        print(f"[hermes] ✓ {DISPLAY_PATH} registered")
        return 0
    if args.check:
        labels = ", ".join(_label(config) for config, _, _ in drifted)
        print(f"[hermes] Hermes Skill directories need an update: {labels}")
        return 2

    for config, updated, actions in drifted:
        if args.dry_run:
            for action in actions:
                print(f"[hermes] (dry-run) {_label(config)}: {action}")
            continue
        saved = backup(config)
        atomic_write_text(config, updated)
        for action in actions:
            print(f"[hermes] {_label(config)}: {action}")
        print(f"[hermes]   backup → {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
