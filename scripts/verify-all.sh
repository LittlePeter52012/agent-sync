#!/bin/bash
# verify-all.sh — structural verification for hub + tool coverage
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"
PY="${PYTHON:-python3}"
FAIL=0

check() {
    local label="$1"; shift
    if "$@"; then
        echo "  ✅ $label"
    else
        echo "  ❌ $label"
        FAIL=$((FAIL + 1))
    fi
}

echo "━━━ agent-sync verify ━━━"
echo "  Hub: $HUB_ROOT"
echo ""

SKILLS=()
while IFS= read -r s; do
    [ -n "$s" ] && SKILLS+=("$s")
done < <(read_skill_list)

echo "[skills]"
for skill in "${SKILLS[@]}"; do
    check "$skill @ hub" test -f "$HUB_ROOT/skills/$skill/SKILL.md"
    while IFS= read -r base; do
        label=$(basename "$(dirname "$base")")/$(basename "$base")
        check "$skill @ $base" test -f "$base/$skill/SKILL.md"
    done < <(skill_targets)
done

echo ""
echo "[mcp]"
if [ -f "$HUB_ROOT/mcp/shared-servers.json" ]; then
    check "Antigravity mcp file" test -f "$HOME/.gemini/config/mcp_config.json"
    check "Cursor mcp file" test -f "$HOME/.cursor/mcp.json"
    check "Claude mcp file" test -f "$HOME/.claude.json"
    check "VSCode/Copilot mcp file" test -f "$HOME/Library/Application Support/Code/User/mcp.json"
    check "OpenCode config" test -f "$HOME/.config/opencode/opencode.json"
    check "Codex config" test -f "$HOME/.codex/config.toml"
else
    echo "  (no mcp/shared-servers.json — skip)"
fi

echo ""
echo "[rules]"
if [ -d "$HUB_ROOT/rules" ] && ls "$HUB_ROOT/rules"/*.md >/dev/null 2>&1; then
    check "Claude CLAUDE.md exists" test -f "$HOME/.claude/CLAUDE.md"
    check "Codex AGENTS.md exists" test -f "$HOME/.codex/AGENTS.md"
    check "Antigravity GEMINI.md exists" test -f "$HOME/.gemini/GEMINI.md"
else
    echo "  (no rules — skip)"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "全部通过 ✅"
else
    echo "$FAIL 项失败 ❌"
    exit 1
fi
