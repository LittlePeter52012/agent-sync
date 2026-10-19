"""Read the Skill allowlist and per-target exclusions from a Hub manifest.

The ``targets`` section narrows distribution for one Skill target. It can only
remove allowlisted Skills from that target, never add new ones::

    targets:
      hermes:
        exclude:
          - example-skill

The parser is deliberately strict: anything it does not recognise inside the
section is an error, so a typo can never silently distribute a Skill that was
meant to be excluded.
"""

from __future__ import annotations

import re
from pathlib import Path

# Keys of ``skill_target_specs`` in lib.sh (a test keeps the two in step).
SKILL_TARGETS = frozenset(
    {
        "gemini-global",
        "antigravity-app",
        "antigravity-cli",
        "antigravity-ide",
        "claude",
        "cursor",
        "codex",
        "opencode",
        "copilot",
        "agents",
        "hermes",
    }
)

_TARGET_LINE = re.compile(r"  ([a-z0-9][a-z0-9-]*):")
_EXCLUDE_LINE = re.compile(r"    exclude:(?P<empty> \[\])?")
_SKILL_LINE = re.compile(r"      - ([a-z0-9][a-z0-9-]*)")


class ManifestError(ValueError):
    """The manifest ``targets`` section does not follow the documented schema."""


def read_manifest_skills(manifest: Path) -> list[str]:
    """Allowlisted Skill names, read the same way as ``read_skill_list``."""
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    skills: list[str] = []
    in_skills = False
    for line in lines:
        if line.startswith(("skills:", "math_skills:")):
            in_skills = True
        elif in_skills and line.startswith("  - "):
            skills.append(line[4:].strip())
        elif in_skills and line and not line.startswith((" ", "#")):
            break
    return skills


def read_target_exclusions(manifest: Path) -> dict[str, tuple[str, ...]]:
    """``{target: excluded Skill names}`` from the ``targets`` section."""
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    exclusions: dict[str, list[str]] = {}
    seen_section = False
    in_section = False
    target: str | None = None
    has_exclude = False
    in_exclude = False
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            in_section = False
            if line.startswith("targets:"):
                if line.rstrip() != "targets:":
                    raise ManifestError(
                        f"line {number}: targets must be a block mapping"
                    )
                if seen_section:
                    raise ManifestError(f"line {number}: duplicate targets section")
                seen_section = in_section = True
                target = None
            continue
        if not in_section:
            continue
        line = line.rstrip()
        target_match = _TARGET_LINE.fullmatch(line)
        if target_match:
            target = target_match.group(1)
            if target not in SKILL_TARGETS:
                raise ManifestError(f"line {number}: unknown target '{target}'")
            if target in exclusions:
                raise ManifestError(f"line {number}: duplicate target '{target}'")
            exclusions[target] = []
            has_exclude = in_exclude = False
            continue
        exclude_match = _EXCLUDE_LINE.fullmatch(line)
        if target is not None and exclude_match:
            if has_exclude:
                raise ManifestError(
                    f"line {number}: duplicate exclude list for '{target}'"
                )
            has_exclude = True
            in_exclude = exclude_match.group("empty") is None
            continue
        skill_match = _SKILL_LINE.fullmatch(line)
        if target is not None and in_exclude and skill_match:
            name = skill_match.group(1)
            if name in exclusions[target]:
                raise ManifestError(
                    f"line {number}: '{name}' is excluded twice for '{target}'"
                )
            exclusions[target].append(name)
            continue
        raise ManifestError(f"line {number}: unsupported entry in targets section")
    return {name: tuple(skills) for name, skills in exclusions.items()}


def unlisted_exclusions(manifest: Path) -> list[str]:
    """``target:skill`` pairs that exclude a Skill the allowlist does not name."""
    listed = set(read_manifest_skills(manifest))
    return [
        f"{target}:{skill}"
        for target, skills in sorted(read_target_exclusions(manifest).items())
        for skill in skills
        if skill not in listed
    ]
