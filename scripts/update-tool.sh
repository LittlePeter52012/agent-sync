#!/bin/bash
# update-tool.sh — update agent-sync from git remote (and optionally hub)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

CHECK_ONLY=false
WITH_HUB=false
RUN_SYNC=false
FORCE=false

while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=true; shift ;;
        --hub) WITH_HUB=true; shift ;;
        --sync) RUN_SYNC=true; shift ;;
        --force) FORCE=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

read_version() {
    local f="$SYNC_HOME/VERSION"
    if [ -f "$f" ]; then
        tr -d '[:space:]' < "$f"
    else
        echo "unknown"
    fi
}

ensure_git_repo() {
    local dir="$1" label="$2"
    if ! git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "ERROR: $label is not a git repository: $dir" >&2
        exit 1
    fi
}

update_one_repo() {
    local dir="$1" label="$2"
    local local_ref remote_ref

    ensure_git_repo "$dir" "$label"

    if ! git -C "$dir" remote get-url origin >/dev/null 2>&1; then
        echo "WARN: $label has no git remote 'origin' — skip" >&2
        return 0
    fi

    echo "→ $label ($dir)"

    git -C "$dir" fetch origin --quiet 2>/dev/null || {
        echo "  WARN: fetch failed (offline?)" >&2
        return 1
    }

    local_ref=$(git -C "$dir" rev-parse HEAD)
    remote_ref=$(git -C "$dir" rev-parse "@{u}" 2>/dev/null || git -C "$dir" rev-parse "origin/HEAD" 2>/dev/null || true)

    if [ -z "$remote_ref" ]; then
        # Try origin/main or origin/master
        for branch in main master; do
            if git -C "$dir" rev-parse "origin/$branch" >/dev/null 2>&1; then
                remote_ref=$(git -C "$dir" rev-parse "origin/$branch")
                break
            fi
        done
    fi

    if [ -z "$remote_ref" ]; then
        echo "  WARN: cannot determine remote HEAD" >&2
        return 1
    fi

    if [ "$local_ref" = "$remote_ref" ]; then
        echo "  up to date ($(git -C "$dir" log -1 --format='%h %s' HEAD))"
        return 0
    fi

    echo "  update available: $(git -C "$dir" log -1 --format='%h %s' HEAD) → $(git -C "$dir" log -1 --format='%h %s' "$remote_ref")"

    if $CHECK_ONLY; then
        return 2
    fi

    if ! git -C "$dir" diff --quiet || ! git -C "$dir" diff --cached --quiet; then
        if $FORCE; then
            echo "  WARN: local changes present; --force will stash them" >&2
            git -C "$dir" stash push -u -m "agent-sync update $(date +%Y%m%d-%H%M%S)" >/dev/null 2>&1 || true
        else
            echo "  ERROR: local changes in $dir — commit or stash first (or use --force)" >&2
            return 1
        fi
    fi

    local branch
    branch=$(git -C "$dir" symbolic-ref --short HEAD 2>/dev/null || echo "main")
    git -C "$dir" pull --ff-only origin "$branch" 2>/dev/null || \
        git -C "$dir" pull --ff-only origin main 2>/dev/null || \
        git -C "$dir" merge --ff-only "$remote_ref"

    echo "  updated to $(git -C "$dir" log -1 --format='%h %s' HEAD)"
    return 2
}

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/agent-sync"
LAST_CHECK="$CACHE_DIR/last-update-check"
CHECK_INTERVAL_SEC=$((24 * 3600))

should_auto_check() {
    # Called when manifest has auto_update_check: true
    [ -f "$LAST_CHECK" ] || return 0
    local last now
    last=$(cat "$LAST_CHECK" 2>/dev/null || echo 0)
    now=$(date +%s)
    [ $((now - last)) -ge "$CHECK_INTERVAL_SEC" ]
}

mark_checked() {
    mkdir -p "$CACHE_DIR"
    date +%s > "$LAST_CHECK"
}

manifest_auto_check() {
    [ -f "$MANIFEST" ] || return 1
    grep -qE '^auto_update_check:[[:space:]]*true' "$MANIFEST" 2>/dev/null
}

# --- main ---
OLD_VER=$(read_version)
UPDATED=false
NEEDS_UPDATE=false

echo "━━━ agent-sync update ━━━"
echo "Tool version: $OLD_VER"
echo "Tool path:    $SYNC_HOME"
echo ""

if update_one_repo "$SYNC_HOME" "agent-sync tool"; then
    :
else
    rc=$?
    [ "$rc" -eq 2 ] && NEEDS_UPDATE=true
fi

if $WITH_HUB && [ -d "$HUB_ROOT" ] && git -C "$HUB_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo ""
    if update_one_repo "$HUB_ROOT" "personal hub"; then
        :
    else
        rc=$?
        [ "$rc" -eq 2 ] && NEEDS_UPDATE=true
    fi
fi

NEW_VER=$(read_version)
if [ "$OLD_VER" != "$NEW_VER" ]; then
    echo ""
    echo "Version: $OLD_VER → $NEW_VER"
    UPDATED=true
fi

mark_checked

if $CHECK_ONLY; then
    if $NEEDS_UPDATE; then
        echo ""
        echo "Updates available. Run: agent-sync update"
        exit 2
    fi
    echo ""
    echo "Everything up to date."
    exit 0
fi

if $RUN_SYNC && [ -d "$HUB_ROOT" ] && [ -f "$HUB_ROOT/manifest.yaml" ]; then
    echo ""
    echo "→ Re-syncing after update..."
    bash "$SCRIPT_DIR/sync-skills.sh" --method=symlink
    bash "$SCRIPT_DIR/sync-mcp.sh"
    bash "$SCRIPT_DIR/sync-rules.sh"
    bash "$SCRIPT_DIR/verify-all.sh"
fi

echo ""
if $UPDATED || $NEEDS_UPDATE; then
    echo "Update complete."
else
    echo "Already up to date."
fi
