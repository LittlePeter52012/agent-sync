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
from rule_blocks import top_level_managed_blocks

rules_dir = Path(sys.argv[2])
home = Path(sys.argv[3])
priority = (
    "> **路由优先级**：数学论文相关请求优先走下方 "
    "[Math Router Rule]，不要先用通用 skill router。"
)

def read_raw(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()

def write_raw(path, text):
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)

def preferred_newline(text):
    match = re.search(r"\r\n|\n|\r", text)
    return match.group(0) if match else "\n"

def has_terminal_newline(text):
    return text.endswith(("\r\n", "\n", "\r"))


def trim_trailing_separator(value):
    value = re.sub(r"(?:\r?\n[ \t]*)+$", "", value)
    return value if value.strip() else ""

def trim_leading_separator(value):
    value = re.sub(r"^(?:[ \t]*\r?\n)+", "", value)
    return value if value.strip() else ""

def join_without_fragment(before, after):
    before = trim_trailing_separator(before)
    after = trim_leading_separator(after)
    return before + ("\n\n" if before and after else "") + after

def remove_matches(value, pattern):
    while match := pattern.search(value):
        value = join_without_fragment(
            value[:match.start()],
            value[match.end():],
        )
    return value

rules = [
    (path.stem, read_raw(path))
    for path in sorted(rules_dir.glob("*.md"))
]
names = {name for name, _rule in rules}

def build_block(name, rule, include_priority):
    newline = preferred_newline(rule)
    block = f"<!-- agent-sync:begin:{name} -->{newline}"
    if include_priority and name == "math-router-rule":
        block += priority + newline + newline
    block += rule
    if not has_terminal_newline(block):
        block += newline
    return block + f"<!-- agent-sync:end:{name} -->"

def sync_aggregate(target, include_priority):
    if not target.is_file():
        print(f"[rules] skip missing: {target}")
        return
    original = read_raw(target)
    text = original
    for managed in reversed(top_level_managed_blocks(original, names)):
        text = join_without_fragment(
            text[:managed.start],
            text[managed.end:],
        )
    for _name, rule in rules:
        legacy_rule = rule.strip(" \t\r\n")
        if legacy_rule:
            text = remove_matches(text, re.compile(re.escape(legacy_rule)))
    if include_priority:
        text = remove_matches(text, re.compile(re.escape(priority)))
    text = trim_trailing_separator(text)
    blocks = [
        build_block(name, rule, include_priority)
        for name, rule in rules
    ]
    if blocks:
        if text:
            text += preferred_newline(text) * 2
        text += "\n\n".join(blocks) + "\n"
    write_raw(target, text)
    print(f"  ✓ injected → {target}")

for target, include_priority in (
    (home / ".claude" / "CLAUDE.md", True),
    (home / ".codex" / "AGENTS.md", False),
    (home / ".gemini" / "GEMINI.md", True),
):
    sync_aggregate(target, include_priority)

if rules:
    copilot_dir = home / ".copilot" / "instructions"
    copilot_dir.mkdir(parents=True, exist_ok=True)
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
        write_raw(target, body)
        print(f"  ✓ injected → {target}")
PY
