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
PRUNE_ALIASES_PY="$SYNC_HOME/scripts/prune-mcp-aliases.py"
CAPABILITY_RESOLVER="$SYNC_HOME/scripts/capability_resolver.py"
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
    "gemini|Gemini CLI|$HOME/.gemini/settings.json"
    "gemini-global|Gemini global|$HOME/.gemini/config/mcp_config.json"
    "antigravity-app|Antigravity App|$HOME/.gemini/antigravity/mcp_config.json"
    "antigravity-cli|Antigravity CLI|$HOME/.gemini/antigravity-cli/mcp_config.json"
    "antigravity-ide|Antigravity IDE|$HOME/.gemini/antigravity-ide/mcp_config.json"
    "cursor|Cursor|$HOME/.cursor/mcp.json"
    "vscode|VSCode|$HOME/Library/Application Support/Code/User/mcp.json"
    "opencode|OpenCode|$HOME/.config/opencode/opencode.json"
)

echo "━━━ agent-sync MCP merge ━━━"
echo "  Canonical: $CANONICAL"
echo ""

for entry in "${TARGETS[@]}"; do
    target_key="${entry%%|*}"
    remainder="${entry#*|}"
    label="${remainder%%|*}"
    target="${remainder#*|}"
    echo "[mcp] → $label ($target)"
    excluded_file=$(mktemp)
    trap 'rm -f "$excluded_file"' EXIT
    python3 "$CAPABILITY_RESOLVER" \
        --home "$HOME" \
        --hub "$HUB_ROOT" \
        suppressed-mcp "$target_key" > "$excluded_file"
    python3 "$PRUNE_ALIASES_PY" "$CANONICAL" "$target"
    python3 "$PRUNE_PY" "$RETIRED" "$target"
    python3 "$MERGE_PY" \
        "$CANONICAL" "$target" \
        --exclude-file "$excluded_file" \
        --hub "$HUB_ROOT" \
        --target-key "$target_key"
    case "$label" in
        "Antigravity App"|"Antigravity CLI"|"Antigravity IDE")
            prepare_antigravity_cache "$target"
            ;;
    esac
    rm -f "$excluded_file"
    trap - EXIT
done

VSCODE_PROFILES="$HOME/Library/Application Support/Code/User/profiles"
if [ -d "$VSCODE_PROFILES" ]; then
    while IFS= read -r profile; do
        target="$profile/mcp.json"
        echo "[mcp] → VSCode profile ($(basename "$profile")) ($target)"
        excluded_file=$(mktemp)
        trap 'rm -f "$excluded_file"' EXIT
        python3 "$CAPABILITY_RESOLVER" \
            --home "$HOME" \
            --hub "$HUB_ROOT" \
            suppressed-mcp "vscode:$(basename "$profile")" > "$excluded_file"
        python3 "$PRUNE_PY" "$RETIRED" "$target"
        python3 "$MERGE_PY" \
            "$CANONICAL" "$target" \
            --exclude-file "$excluded_file" \
            --hub "$HUB_ROOT" \
            --target-key "vscode:$(basename "$profile")"
        rm -f "$excluded_file"
        trap - EXIT
    done < <(find "$VSCODE_PROFILES" -mindepth 1 -maxdepth 1 -type d | sort)
fi

echo "[mcp] → Codex ($HOME/.codex/config.toml)"
CODEX_EXCLUDED=$(mktemp)
trap 'rm -f "$CODEX_EXCLUDED"' EXIT
python3 "$CAPABILITY_RESOLVER" \
    --home "$HOME" \
    --hub "$HUB_ROOT" \
    suppressed-mcp codex > "$CODEX_EXCLUDED"
python3 "$PRUNE_PY" "$RETIRED" "$HOME/.codex/config.toml"
python3 "$MERGE_CODEX" \
    "$CANONICAL" "$HOME/.codex/config.toml" \
    --exclude-file "$CODEX_EXCLUDED"
rm -f "$CODEX_EXCLUDED"
trap - EXIT

echo "[mcp] → Claude Code (claude mcp, user scope)"
CLAUDE_EXCLUDED=$(mktemp)
trap 'rm -f "$CLAUDE_EXCLUDED"' EXIT
python3 "$CAPABILITY_RESOLVER" \
    --home "$HOME" \
    --hub "$HUB_ROOT" \
    suppressed-mcp claude > "$CLAUDE_EXCLUDED"
python3 "$MERGE_CLAUDE" \
    "$CANONICAL" \
    --retired "$RETIRED" \
    --exclude-file "$CLAUDE_EXCLUDED" \
    --hub "$HUB_ROOT"
rm -f "$CLAUDE_EXCLUDED"
trap - EXIT

echo ""
echo "  Shared MCP structure converged; local secrets and tool-only servers preserved."
