#!/bin/bash
# sync-mcp.sh — merge shared MCP from personal hub into tool configs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

CANONICAL="$HUB_ROOT/mcp/shared-servers.json"
MERGE_PY="$SYNC_HOME/scripts/merge-mcp.py"
MERGE_CODEX="$SYNC_HOME/scripts/merge-mcp-codex.py"
MERGE_CLAUDE="$SYNC_HOME/scripts/sync-mcp-claude.py"

if [ ! -f "$CANONICAL" ]; then
    echo "Missing $CANONICAL" >&2
    exit 1
fi

TARGETS=(
    "Antigravity|$HOME/.gemini/config/mcp_config.json"
    "Cursor|$HOME/.cursor/mcp.json"
    "VSCode|$HOME/Library/Application Support/Code/User/mcp.json"
    "OpenCode|$HOME/.config/opencode/opencode.json"
)

echo "━━━ agent-sync MCP merge ━━━"
echo "  Canonical: $CANONICAL"
echo ""

for entry in "${TARGETS[@]}"; do
    label="${entry%%|*}"
    target="${entry#*|}"
    echo "[mcp] → $label ($target)"
    python3 "$MERGE_PY" "$CANONICAL" "$target"
done

VSCODE_PROFILES="$HOME/Library/Application Support/Code/User/profiles"
if [ -d "$VSCODE_PROFILES" ]; then
    while IFS= read -r profile; do
        target="$profile/mcp.json"
        echo "[mcp] → VSCode profile ($(basename "$profile")) ($target)"
        python3 "$MERGE_PY" "$CANONICAL" "$target"
    done < <(find "$VSCODE_PROFILES" -mindepth 1 -maxdepth 1 -type d | sort)
fi

echo "[mcp] → Codex ($HOME/.codex/config.toml)"
python3 "$MERGE_CODEX" "$CANONICAL" "$HOME/.codex/config.toml"

echo "[mcp] → Claude Code (claude mcp, user scope)"
python3 "$MERGE_CLAUDE" "$CANONICAL"

echo ""
echo "  Codex: append-only for missing shared servers."
