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
PRUNE_PY="$SYNC_HOME/scripts/prune-retired-mcp.py"
RETIRED="$HUB_ROOT/mcp/retired-servers.json"

prepare_antigravity_cache() {
    local config="$1"
    local root="${config%/mcp_config.json}"
    local server
    while IFS= read -r server; do
        [ -n "$server" ] || continue
        case "$server" in
            */*|*..*)
                echo "Unsafe MCP server name: $server" >&2
                return 1
                ;;
        esac
        mkdir -p "$root/mcp/$server"
    done < <(python3 - "$config" <<'PY'
import json
import sys
from pathlib import Path

servers = json.loads(Path(sys.argv[1]).read_text()).get("mcpServers", {})
for name, config in sorted(servers.items()):
    if isinstance(config, dict) and config.get("disabled") is not True:
        print(name)
PY
)
}

if [ ! -f "$CANONICAL" ]; then
    echo "Missing $CANONICAL" >&2
    exit 1
fi

TARGETS=(
    "Gemini global|$HOME/.gemini/config/mcp_config.json"
    "Antigravity App|$HOME/.gemini/antigravity/mcp_config.json"
    "Antigravity CLI|$HOME/.gemini/antigravity-cli/mcp_config.json"
    "Antigravity IDE|$HOME/.gemini/antigravity-ide/mcp_config.json"
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
    python3 "$PRUNE_PY" "$RETIRED" "$target"
    python3 "$MERGE_PY" "$CANONICAL" "$target"
    case "$label" in
        "Antigravity App"|"Antigravity CLI"|"Antigravity IDE")
            prepare_antigravity_cache "$target"
            ;;
    esac
done

VSCODE_PROFILES="$HOME/Library/Application Support/Code/User/profiles"
if [ -d "$VSCODE_PROFILES" ]; then
    while IFS= read -r profile; do
        target="$profile/mcp.json"
        echo "[mcp] → VSCode profile ($(basename "$profile")) ($target)"
        python3 "$PRUNE_PY" "$RETIRED" "$target"
        python3 "$MERGE_PY" "$CANONICAL" "$target"
    done < <(find "$VSCODE_PROFILES" -mindepth 1 -maxdepth 1 -type d | sort)
fi

echo "[mcp] → Codex ($HOME/.codex/config.toml)"
python3 "$PRUNE_PY" "$RETIRED" "$HOME/.codex/config.toml"
python3 "$MERGE_CODEX" "$CANONICAL" "$HOME/.codex/config.toml"

echo "[mcp] → Claude Code (claude mcp, user scope)"
python3 "$MERGE_CLAUDE" "$CANONICAL" --retired "$RETIRED"

echo ""
echo "  Shared MCP structure converged; local secrets and tool-only servers preserved."
