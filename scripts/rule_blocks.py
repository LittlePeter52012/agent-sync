"""Parse top-level agent-sync managed rule blocks."""

from __future__ import annotations

import re
from typing import NamedTuple


MARKER_RE = re.compile(r"<!-- agent-sync:(begin|end):(.+) -->")


class ManagedRuleBlock(NamedTuple):
    name: str
    start: int
    end: int
    text: str


class StructuralIssue(NamedTuple):
    kind: str
    name: str
    offset: int


class ManagedRuleParse(NamedTuple):
    blocks: list[ManagedRuleBlock]
    issues: list[StructuralIssue]


MATH_PRIORITY = (
    "> **路由优先级**：数学论文相关请求优先走下方 "
    "[Math Router Rule]，不要先用通用 skill router。"
)


def _marker_lines(text: str):
    offset = 0
    for line in text.splitlines(keepends=True):
        marker_line = line
        if marker_line.endswith("\r\n"):
            marker_line = marker_line[:-2]
        elif marker_line.endswith(("\n", "\r")):
            marker_line = marker_line[:-1]
        match = MARKER_RE.fullmatch(marker_line)
        if match:
            yield offset, marker_line, match.groups()
        offset += len(line)


def preferred_newline(text: str) -> str:
    match = re.search(r"\r\n|\n|\r", text)
    return match.group(0) if match else "\n"


def has_terminal_newline(text: str) -> bool:
    return text.endswith(("\r\n", "\n", "\r"))


def build_managed_block(
    name: str,
    source: str,
    include_priority: bool,
) -> str:
    newline = preferred_newline(source)
    block = f"<!-- agent-sync:begin:{name} -->{newline}"
    if include_priority and name == "math-router-rule":
        block += MATH_PRIORITY + newline + newline
    block += source
    if not has_terminal_newline(block):
        block += newline
    return block + f"<!-- agent-sync:end:{name} -->"


def source_marker_issues(name: str, text: str) -> list[StructuralIssue]:
    return [
        StructuralIssue("source-own-marker", marker_name, offset)
        for offset, _line, (_kind, marker_name) in _marker_lines(text)
        if marker_name == name
    ]


def parse_managed_blocks(
    text: str,
    known_names: set[str],
) -> ManagedRuleParse:
    """Parse known top-level blocks and report ambiguous structure."""
    blocks: list[ManagedRuleBlock] = []
    issues: list[StructuralIssue] = []
    active_name: str | None = None
    active_start = 0
    for offset, marker_line, (kind, name) in _marker_lines(text):
        if active_name is None:
            if name not in known_names:
                continue
            if kind == "begin":
                active_name = name
                active_start = offset
            else:
                issues.append(StructuralIssue("stray-end", name, offset))
            continue
        if name != active_name:
            continue
        if kind == "begin":
            issues.append(StructuralIssue("ambiguous-begin", name, offset))
            continue
        end = offset + len(marker_line)
        blocks.append(
            ManagedRuleBlock(
                active_name,
                active_start,
                end,
                text[active_start:end],
            )
        )
        active_name = None
    if active_name is not None:
        issues.append(
            StructuralIssue("unclosed-begin", active_name, active_start)
        )
    return ManagedRuleParse(blocks, issues)


def top_level_managed_blocks(
    text: str,
    known_names: set[str],
) -> list[ManagedRuleBlock]:
    """Compatibility wrapper returning only blocks."""
    return parse_managed_blocks(text, known_names).blocks
