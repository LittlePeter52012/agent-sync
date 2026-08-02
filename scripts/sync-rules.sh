#!/bin/bash
# sync-rules.sh — inject rule markdown blocks from hub/rules into agent configs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

RULES_DIR="$HUB_ROOT/rules"

if [ ! -d "$RULES_DIR" ]; then
    echo "[rules] no rules/ directory in hub — skip"
    exit 0
fi

echo "━━━ agent-sync rules sync ━━━"

python3 - "$SCRIPT_DIR" "$RULES_DIR" "$HOME" <<'PY'
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from rule_blocks import (
    MATH_PRIORITY,
    build_managed_block,
    has_terminal_newline,
    parse_managed_blocks,
    preferred_newline,
    source_marker_issues,
)
from rule_transaction import OutputTransactionError, publish_outputs

rules_dir = Path(sys.argv[2])
home = Path(sys.argv[3])

def read_raw(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()

def trim_trailing_separator(value):
    value = re.sub(r"(?:(?:\r\n|\n|\r)[ \t]*)+$", "", value)
    return value if value.strip() else ""

def trim_leading_separator(value):
    value = re.sub(r"^(?:[ \t]*(?:\r\n|\n|\r))+", "", value)
    return value if value.strip() else ""

def join_without_fragment(before, after):
    before = trim_trailing_separator(before)
    after = trim_leading_separator(after)
    return before + ("\n\n" if before and after else "") + after

def remove_fragment(value, fragment):
    parts = value.split(fragment)
    if len(parts) == 1:
        return value
    cleaned = []
    last = len(parts) - 1
    for index, part in enumerate(parts):
        if index:
            part = trim_leading_separator(part)
        if index < last:
            part = trim_trailing_separator(part)
        if part:
            cleaned.append(part)
    return "\n\n".join(cleaned)

def remove_fragment_to_fixed_point(value, fragment):
    while True:
        cleaned = remove_fragment(value, fragment)
        if cleaned == value:
            return value
        if len(cleaned) >= len(value):
            raise RuntimeError("legacy cleanup did not make progress")
        value = cleaned

rules = [
    (path.stem, read_raw(path))
    for path in sorted(rules_dir.glob("*.md"))
]
names = {name for name, _rule in rules}

if not rules:
    raise SystemExit(0)

target_specs = (
    (home / ".claude" / "CLAUDE.md", True),
    (home / ".codex" / "AGENTS.md", False),
    (home / ".gemini" / "GEMINI.md", True),
)
target_inputs = {}
source_issues = [
    issue
    for name, rule in rules
    for issue in source_marker_issues(name, rule)
]
structural_error = bool(source_issues)
for target, _include_priority in target_specs:
    if not target.is_file():
        target_inputs[target] = None
        continue
    original = read_raw(target)
    parsed = parse_managed_blocks(original, names)
    structural_error = structural_error or bool(parsed.issues)
    target_inputs[target] = (original, parsed)

if structural_error:
    print(
        "ERROR: rule synchronization structural preflight failed; "
        "no files changed.",
        file=sys.stderr,
    )
    raise SystemExit(1)

legacy_candidates = set()
for _name, rule in rules:
    for candidate in (rule, rule.replace("\r\n", "\n").replace("\r", "\n")):
        candidate = candidate.strip(" \t\r\n")
        if candidate:
            legacy_candidates.add(candidate)
legacy_candidates = sorted(
    legacy_candidates,
    key=lambda value: (-len(value.encode("utf-8")), value),
)

outputs = []
for target, include_priority in target_specs:
    target_input = target_inputs[target]
    if target_input is None:
        continue
    original, parsed = target_input
    text = original
    for managed in reversed(parsed.blocks):
        text = join_without_fragment(
            text[:managed.start],
            text[managed.end:],
        )
    for candidate in legacy_candidates:
        text = remove_fragment_to_fixed_point(text, candidate)
    if include_priority and "math-router-rule" in names:
        text = remove_fragment_to_fixed_point(text, MATH_PRIORITY)
    text = trim_trailing_separator(text)
    blocks = [
        build_managed_block(name, rule, include_priority)
        for name, rule in rules
    ]
    if text:
        text += preferred_newline(text) * 2
    text += "\n\n".join(blocks) + "\n"
    outputs.append((target, text))

copilot_dir = home / ".copilot" / "instructions"
for name, rule in rules:
    target = copilot_dir / f"{name}.instructions.md"
    body = f"""---
name: {json.dumps(name, ensure_ascii=False)}
description: Agent rule synced by agent-sync from personal hub.
applyTo: "**"
---

""" + rule
    if not has_terminal_newline(body):
        body += preferred_newline(rule)
    outputs.append((target, body))

try:
    publish_outputs(
        (target, text.encode("utf-8"))
        for target, text in outputs
    )
except OutputTransactionError as error:
    if error.recovered:
        detail = "no files changed."
    else:
        detail = "manual recovery required."
    print(
        "ERROR: rule synchronization output transaction failed; " + detail,
        file=sys.stderr,
    )
    raise SystemExit(1)

for target, _text in outputs:
    print(f"  ✓ injected → {target}")
for target, _include_priority in target_specs:
    if target_inputs[target] is None:
        print(f"[rules] skip missing: {target}")
PY
