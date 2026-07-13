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
SKILL_COUNT=0
while IFS= read -r s; do
    if [ -n "$s" ]; then
        SKILLS+=("$s")
        SKILL_COUNT=$((SKILL_COUNT + 1))
    fi
done < <(read_skill_list)

echo "[skills]"
if [ "$SKILL_COUNT" -eq 0 ]; then
    echo "  (no shared Skills — skip)"
else
    for skill in "${SKILLS[@]}"; do
        check "$skill @ hub" test -f "$HUB_ROOT/skills/$skill/SKILL.md"
        while IFS= read -r base; do
            label=$(basename "$(dirname "$base")")/$(basename "$base")
            check "$skill @ $base" test -f "$base/$skill/SKILL.md"
        done < <(skill_targets)
    done
fi

echo ""
echo "[mcp]"
if [ -f "$HUB_ROOT/mcp/shared-servers.json" ]; then
    if "$PY" - "$HUB_ROOT" <<'PY'
import json, re, sys
from pathlib import Path

hub = Path(sys.argv[1])
home = Path.home()
shared = {
    name.lower()
    for name in json.loads((hub / "mcp/shared-servers.json").read_text()).get("mcpServers", {})
}

def json_names(path, key):
    if not path.exists():
        return set()
    try:
        value = json.loads(path.read_text()).get(key, {})
    except json.JSONDecodeError:
        return set()
    return {name.lower() for name in value} if isinstance(value, dict) else set()

def codex_names(path):
    if not path.exists():
        return set()
    return {
        match.group(1).lower()
        for match in re.finditer(r"^\[mcp_servers\.([^\].]+)\]", path.read_text(), re.M)
    }

targets = [
    ("Antigravity", home / ".gemini/config/mcp_config.json", "json", "mcpServers"),
    ("Cursor", home / ".cursor/mcp.json", "json", "mcpServers"),
    ("Claude", home / ".claude.json", "json", "mcpServers"),
    ("VS Code", home / "Library/Application Support/Code/User/mcp.json", "json", "servers"),
    ("OpenCode", home / ".config/opencode/opencode.json", "json", "mcp"),
    ("Codex", home / ".codex/config.toml", "toml", ""),
]
profiles = home / "Library/Application Support/Code/User/profiles"
if profiles.exists():
    for profile in sorted(path for path in profiles.iterdir() if path.is_dir()):
        targets.append((f"VS Code profile {profile.name}", profile / "mcp.json", "json", "servers"))

failures = 0
for label, path, kind, key in targets:
    names = codex_names(path) if kind == "toml" else json_names(path, key)
    count = len(names & shared)
    marker = "✅" if count == len(shared) else "❌"
    print(f"  {marker} {label} shared MCP {count}/{len(shared)}")
    failures += count != len(shared)
sys.exit(failures)
PY
    then
        :
    else
        rc=$?
        FAIL=$((FAIL + rc))
    fi
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
