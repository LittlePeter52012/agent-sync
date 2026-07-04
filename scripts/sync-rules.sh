#!/bin/bash
# sync-rules.sh — inject rule markdown blocks from hub/rules into agent configs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

RULES_DIR="$HUB_ROOT/rules"
MARKER_DEFAULT="[Agent Sync Rule]"

if [ ! -d "$RULES_DIR" ]; then
    echo "[rules] no rules/ directory in hub — skip"
    exit 0
fi

inject_rule_file() {
    local rule_file="$1"
    local target="$2"
    local marker="$3"
    local priority_note="${4:-}"

    [ -f "$target" ] || { echo "[rules] skip missing: $target"; return; }
    [ -f "$rule_file" ] || return

    python3 - "$target" "$rule_file" "$marker" "$priority_note" <<'PY'
import re, sys
from pathlib import Path

target = Path(sys.argv[1])
rule_path = Path(sys.argv[2])
marker = sys.argv[3]
priority = sys.argv[4]

text = target.read_text(encoding="utf-8")
rule = rule_path.read_text(encoding="utf-8").strip()
if not rule.startswith("["):
    # ensure a marker heading exists for idempotent replace
    if not rule.startswith(marker):
        rule = f"{marker}\n{rule}"

pattern = re.compile(
    rf"\n?{re.escape(marker)}.*?(?=\n\[|\Z)",
    re.DOTALL,
)
# also remove legacy Math Router marker if present
text = re.sub(
    r"\n?\[Math Router Rule\].*?(?=\n\[|\Z)",
    "",
    text,
    flags=re.DOTALL,
)
text = pattern.sub("", text).rstrip() + "\n"

if priority:
    text += f"\n{priority.strip()}\n\n"

text += rule + "\n"
target.write_text(text, encoding="utf-8")
print(f"  ✓ injected → {target}")
PY
}

echo "━━━ agent-sync rules sync ━━━"

# Prefer math-router-rule.md marker for backward compatibility
for rule_file in "$RULES_DIR"/*.md; do
    [ -f "$rule_file" ] || continue
    base=$(basename "$rule_file")
    if [ "$base" = "math-router-rule.md" ]; then
        marker="[Math Router Rule]"
    else
        marker="[Agent Sync Rule: ${base%.md}]"
    fi

    PRIORITY=""
    if [ "$base" = "math-router-rule.md" ]; then
        PRIORITY='> **路由优先级**：数学论文相关请求优先走下方 [Math Router Rule]，不要先用通用 skill router。'
    fi

    inject_rule_file "$rule_file" "$HOME/.claude/CLAUDE.md" "$marker" "$PRIORITY"
    inject_rule_file "$rule_file" "$HOME/.codex/AGENTS.md" "$marker" ""
    inject_rule_file "$rule_file" "$HOME/.gemini/GEMINI.md" "$marker" "$PRIORITY"

    # Copilot instructions
    COPILOT_DIR="$HOME/.copilot/instructions"
    mkdir -p "$COPILOT_DIR"
    name="${base%.md}"
    python3 - "$COPILOT_DIR/${name}.instructions.md" "$rule_file" "$name" <<'PY'
from pathlib import Path
import sys
target = Path(sys.argv[1])
rule = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
name = sys.argv[3]
body = f"""---
name: {name}
description: Agent rule synced by agent-sync from personal hub.
applyTo: "**"
---

{rule}
"""
target.write_text(body, encoding="utf-8")
print(f"  ✓ injected → {target}")
PY
done
