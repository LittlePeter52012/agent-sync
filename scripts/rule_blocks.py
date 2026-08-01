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


def top_level_managed_blocks(
    text: str,
    known_names: set[str],
) -> list[ManagedRuleBlock]:
    """Return complete known blocks, treating foreign markers as block data."""
    blocks: list[ManagedRuleBlock] = []
    active_name: str | None = None
    active_start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        marker_line = line
        if marker_line.endswith("\r\n"):
            marker_line = marker_line[:-2]
        elif marker_line.endswith(("\n", "\r")):
            marker_line = marker_line[:-1]
        match = MARKER_RE.fullmatch(marker_line)
        if match:
            kind, name = match.groups()
            if active_name is None and kind == "begin" and name in known_names:
                active_name = name
                active_start = offset
            elif active_name == name and kind == "end":
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
        offset += len(line)
    return blocks
