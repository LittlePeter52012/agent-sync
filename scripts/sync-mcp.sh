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
        mkdir -p "$root/mcp/$server" || return 1
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

python3 "$CAPABILITY_RESOLVER" \
    --home "$HOME" \
    --hub "$HUB_ROOT" \
    validate

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

# One failing target must not stop the others: each target runs as a unit
# that stops at its own first failure, and the failures are reported at the end.
FAILED_TARGETS=()
EXCLUDED_FILE=""
trap 'rm -f "${EXCLUDED_FILE:-}"' EXIT

new_excluded_file() {
    rm -f "${EXCLUDED_FILE:-}"
    EXCLUDED_FILE=$(mktemp)
}

sync_json_target() {
    local target_key="$1" label="$2" target="$3"
    new_excluded_file || return 1
    python3 "$CAPABILITY_RESOLVER" \
        --home "$HOME" \
        --hub "$HUB_ROOT" \
        suppressed-mcp "$target_key" > "$EXCLUDED_FILE" || return 1
    if [ "${PRUNE_ALIASES:-1}" = 1 ]; then
        python3 "$PRUNE_ALIASES_PY" "$CANONICAL" "$target" || return 1
    fi
    python3 "$PRUNE_PY" "$RETIRED" "$target" || return 1
    python3 "$MERGE_PY" \
        "$CANONICAL" "$target" \
        --exclude-file "$EXCLUDED_FILE" \
        --hub "$HUB_ROOT" \
        --target-key "$target_key" || return 1
    case "$label" in
        "Antigravity App"|"Antigravity CLI"|"Antigravity IDE")
            prepare_antigravity_cache "$target" || return 1
            ;;
    esac
}

run_target() {
    local label="$1"
    shift
    if ! "$@"; then
        echo "  ✗ $label failed; continuing with the remaining targets" >&2
        FAILED_TARGETS+=("$label")
    fi
}

echo "━━━ agent-sync MCP merge ━━━"
echo "  Canonical: $CANONICAL"
echo ""

for entry in "${TARGETS[@]}"; do
    target_key="${entry%%|*}"
    remainder="${entry#*|}"
    label="${remainder%%|*}"
    target="${remainder#*|}"
    echo "[mcp] → $label ($target)"
    PRUNE_ALIASES=1 run_target "$label" sync_json_target "$target_key" "$label" "$target"
done

VSCODE_PROFILES="$HOME/Library/Application Support/Code/User/profiles"
if [ -d "$VSCODE_PROFILES" ]; then
    while IFS= read -r profile; do
        target="$profile/mcp.json"
        echo "[mcp] → VSCode profile ($(basename "$profile")) ($target)"
        PRUNE_ALIASES=0 run_target "VSCode profile $(basename "$profile")" \
            sync_json_target "vscode:$(basename "$profile")" "VSCode profile" "$target"
    done < <(find "$VSCODE_PROFILES" -mindepth 1 -maxdepth 1 -type d | sort)
fi

sync_codex() {
    new_excluded_file || return 1
    python3 "$CAPABILITY_RESOLVER" \
        --home "$HOME" \
        --hub "$HUB_ROOT" \
        suppressed-mcp codex > "$EXCLUDED_FILE" || return 1
    python3 "$PRUNE_PY" "$RETIRED" "$HOME/.codex/config.toml" || return 1
    python3 "$MERGE_CODEX" \
        "$CANONICAL" "$HOME/.codex/config.toml" \
        --exclude-file "$EXCLUDED_FILE" || return 1
}

sync_claude() {
    new_excluded_file || return 1
    python3 "$CAPABILITY_RESOLVER" \
        --home "$HOME" \
        --hub "$HUB_ROOT" \
        suppressed-mcp claude > "$EXCLUDED_FILE" || return 1
    python3 "$MERGE_CLAUDE" \
        "$CANONICAL" \
        --retired "$RETIRED" \
        --exclude-file "$EXCLUDED_FILE" \
        --hub "$HUB_ROOT" || return 1
}

echo "[mcp] → Codex ($HOME/.codex/config.toml)"
run_target "Codex" sync_codex

echo "[mcp] → Claude Code (claude mcp, user scope)"
run_target "Claude Code" sync_claude

echo ""
if [ "${#FAILED_TARGETS[@]}" -gt 0 ]; then
    echo "  ✗ MCP sync failed for ${#FAILED_TARGETS[@]} target(s):" >&2
    for label in "${FAILED_TARGETS[@]}"; do
        echo "    - $label" >&2
    done
    exit 1
fi
echo "  Shared MCP structure converged; local secrets and tool-only servers preserved."
