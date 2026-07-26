#!/usr/bin/env python3
"""Register Agent Sync's shared Skill directory with Hermes."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


HOME = Path.home()
HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", HOME / ".hermes")
).expanduser()
CONFIG = HERMES_HOME / "config.yaml"
DISPLAY_PATH = "~/.agents/skills"
TARGET = (HOME / ".agents" / "skills").resolve()


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


def is_registered(text: str) -> bool:
    lines = text.splitlines(keepends=True)
    _, section_end, external_index = find_sections(lines)
    if external_index is None:
        return False
    return TARGET in {
        entry
        for value in external_dir_entries(lines, external_index, section_end)
        if (entry := normalized_path(value)) is not None
    }


def updated_text(text: str) -> str:
    if is_registered(text):
        return text

    lines = text.splitlines(keepends=True)
    skills_index, section_end, external_index = find_sections(lines)
    if skills_index is None:
        prefix = "" if not text or text.endswith("\n") else "\n"
        return text + prefix + (
            "skills:\n"
            "  external_dirs:\n"
            f"    - {DISPLAY_PATH}\n"
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
            f"{list_indent}- {DISPLAY_PATH}\n",
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
            f"{list_indent}- {DISPLAY_PATH}\n",
        ]
        return "".join(lines)
    if inline in {"null", "~"}:
        lines[external_index : external_index + 1] = [
            f"{key_indent}external_dirs:"
            + (f" {comment}" if comment else "")
            + "\n",
            f"{list_indent}- {DISPLAY_PATH}\n",
        ]
        return "".join(lines)
    if inline:
        lines[external_index : external_index + 1] = [
            f"{key_indent}external_dirs:"
            + (f" {comment}" if comment else "")
            + "\n",
            f"{list_indent}- {inline}\n",
            f"{list_indent}- {DISPLAY_PATH}\n",
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
        f"{existing_indent or list_indent}- {DISPLAY_PATH}\n"
    ]
    return "".join(lines)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.chmod(mode)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not CONFIG.exists():
        print("[hermes] not configured — skip")
        return 0

    original = CONFIG.read_text(encoding="utf-8")
    if is_registered(original):
        print("[hermes] ✓ shared Skill directory registered")
        return 0
    if args.check:
        print("[hermes] shared Skill directory is not registered")
        return 2

    atomic_write(CONFIG, updated_text(original))
    print("[hermes] + registered ~/.agents/skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
