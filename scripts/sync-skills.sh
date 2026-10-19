#!/bin/bash
# sync-skills.sh — symlink whitelist skills from personal hub to all tools
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"
PY="${PYTHON:-python3}"

SOURCE_DIR="$HUB_ROOT/skills"
BACKUP_ROOT="$HUB_ROOT/.sync-backups"
RUN_ID="$(date +%Y%m%d_%H%M%S)_$$"

DRY_RUN=false
FORCE=false
METHOD="symlink"

for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=true ;;
        --force) FORCE=true ;;
        --method=symlink) METHOD="symlink" ;;
        --method=rsync) METHOD="rsync" ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

log() { echo "[skills] $1"; }
warn() { echo "[WARN] $1" >&2; }

skill_present() { [ -L "$1" ] || [ -d "$1" ]; }

is_suppressed() {
    local skill_name="$1" suppressed_file="$2"
    grep -Fqx "$skill_name" "$suppressed_file"
}

remove_suppressed_hub_link() {
    local skill_name="$1" target_dir="$2"
    local source="$SOURCE_DIR/$skill_name"
    local dest="$target_dir/$skill_name"
    if skill_is_hub_managed "$dest" "$source"; then
        [ "$DRY_RUN" = false ] && rm "$dest"
        log "  ⊘ $skill_name @ ${target_dir##*/} (withheld by a product plugin, policy or manifest exclusion)"
    else
        log "  ⊘ $skill_name @ ${target_dir##*/} (withheld by a product plugin, policy or manifest exclusion)"
    fi
}

remove_unlisted_hub_links() {
    # A skill dropped from the manifest keeps its old Hub link in every tool
    # unless we prune it; only links that resolve to the Hub copy are removed.
    local target_dir="$1" listed_file="$2" dest skill_name
    for dest in "$target_dir"/*; do
        [ -L "$dest" ] || continue
        skill_name="${dest##*/}"
        grep -Fqx "$skill_name" "$listed_file" && continue
        skill_is_hub_managed "$dest" "$SOURCE_DIR/$skill_name" || continue
        [ "$DRY_RUN" = false ] && rm "$dest"
        log "  − $skill_name @ ${target_dir##*/} (no longer in manifest)"
    done
}

link_points_into_hub() {
    # True when the link (even a dangling one) targets something under the Hub.
    "$PY" - "$1" "$HUB_ROOT" <<'PY'
import os
import sys

link, hub = sys.argv[1:3]
hub_real = os.path.realpath(hub)
raw = os.readlink(link)
if not os.path.isabs(raw):
    raw = os.path.join(os.path.dirname(link), raw)
candidates = {os.path.realpath(link), os.path.normpath(raw)}
roots = {hub_real, os.path.normpath(hub)}
raise SystemExit(
    0 if any(c == r or c.startswith(r + os.sep) for c in candidates for r in roots) else 1
)
PY
}

backup_existing_dest() {
    local dest="$1" skill_name="$2" target_dir="$3"
    local target_key backup_dir
    target_key="${target_dir#"$HOME"/}"
    target_key=$(printf '%s' "$target_key" | tr '/ ' '__')
    backup_dir="$BACKUP_ROOT/$RUN_ID/$target_key"
    if [ "$DRY_RUN" = true ]; then
        log "  (dry-run) backup $dest → $backup_dir/$skill_name"
        return
    fi
    mkdir -p "$backup_dir"
    mv "$dest" "$backup_dir/$skill_name"
    log "  backed up → $backup_dir/$skill_name"
}

link_skill() {
    local skill_name="$1" target_dir="$2"
    local source="$SOURCE_DIR/$skill_name"
    local dest="$target_dir/$skill_name"

    [ -d "$source" ] || { warn "missing source: $source"; return; }

    if [ -L "$dest" ]; then
        local current_target
        current_target=$(readlink "$dest")
        if [ "$current_target" = "$source" ]; then
            log "  ✓ $skill_name @ ${target_dir##*/}"
            return
        fi
        if link_points_into_hub "$dest"; then
            [ "$DRY_RUN" = false ] && { rm "$dest"; ln -s "$source" "$dest"; }
            log "  ↻ $skill_name @ ${target_dir##*/}"
        else
            # A link the user pointed elsewhere is kept, like a directory is.
            backup_existing_dest "$dest" "$skill_name" "$target_dir"
            [ "$DRY_RUN" = false ] && ln -s "$source" "$dest"
            log "  ⚠ replaced foreign symlink → Hub $skill_name"
        fi
    elif [ -d "$dest" ]; then
        if [ "$FORCE" = true ]; then
            backup_existing_dest "$dest" "$skill_name" "$target_dir"
            [ "$DRY_RUN" = false ] && ln -s "$source" "$dest"
            log "  ⚠ replaced dir → symlink $skill_name"
        else
            warn "  ✗ $skill_name — directory exists (use --force)"
        fi
    else
        [ "$DRY_RUN" = false ] && { mkdir -p "$target_dir"; ln -s "$source" "$dest"; }
        log "  + $skill_name @ ${target_dir##*/}"
    fi
}

rsync_skill() {
    local skill_name="$1" target_dir="$2"
    local source="$SOURCE_DIR/$skill_name/"
    local dest="$target_dir/$skill_name/"

    [ -d "$SOURCE_DIR/$skill_name" ] || return

    if [ -d "$target_dir/$skill_name" ] && [ -L "$target_dir/$skill_name" ]; then
        [ "$FORCE" = true ] || { warn "  ✗ $skill_name is symlink (use --force)"; return; }
        [ "$DRY_RUN" = false ] && rm "$target_dir/$skill_name"
    fi

    if [ "$DRY_RUN" = true ]; then
        log "  (dry-run) rsync $skill_name → ${target_dir##*/}"
    else
        mkdir -p "$dest"
        rsync -a --delete "$source" "$dest"
        log "  ⟳ rsync $skill_name @ ${target_dir##*/}"
    fi
}

SKILLS=()
while IFS= read -r s; do
    [ -n "$s" ] && SKILLS+=("$s")
done < <(read_skill_list)

if [ "${#SKILLS[@]}" -eq 0 ]; then
    echo "No shared Skills listed in $MANIFEST — skip."
    exit 0
fi

"$PY" "$SCRIPT_DIR/capability_resolver.py" \
    --home "$HOME" \
    --hub "$HUB_ROOT" \
    validate

SUPPRESSED_ROOT=$(mktemp -d)
trap 'rm -rf "$SUPPRESSED_ROOT"' EXIT
LISTED_FILE="$SUPPRESSED_ROOT/.listed"
printf '%s\n' "${SKILLS[@]}" > "$LISTED_FILE"

echo "━━━ agent-sync skills ($METHOD) ━━━"
echo "  Hub: $SOURCE_DIR"
[ "$DRY_RUN" = true ] && echo "  [DRY RUN]"
echo ""

while IFS='|' read -r target_key target_label target_dir; do
    [ -n "$target_dir" ] || continue
    log "→ $target_dir"
    [ "$DRY_RUN" = false ] && mkdir -p "$target_dir"
    suppressed_file="$SUPPRESSED_ROOT/${target_key//:/_}"
    "$PY" "$SCRIPT_DIR/capability_resolver.py" \
        --home "$HOME" \
        --hub "$HUB_ROOT" \
        suppressed-skills "$target_key" > "$suppressed_file"
    for skill in "${SKILLS[@]}"; do
        if is_suppressed "$skill" "$suppressed_file"; then
            remove_suppressed_hub_link "$skill" "$target_dir"
        elif [ "$METHOD" = "rsync" ]; then
            rsync_skill "$skill" "$target_dir"
        else
            link_skill "$skill" "$target_dir"
        fi
    done
    remove_unlisted_hub_links "$target_dir" "$LISTED_FILE"
    echo ""
done < <(skill_target_specs)

log "Verification:"
for skill in "${SKILLS[@]}"; do
    printf "  %-35s" "$skill"
    while IFS='|' read -r target_key target_label target_dir; do
        suppressed_file="$SUPPRESSED_ROOT/${target_key//:/_}"
        suppressed=$(cat "$suppressed_file")
        if printf '%s\n' "$suppressed" | grep -Fqx "$skill"; then
            printf " 🧩"
        elif skill_present "$target_dir/$skill"; then
            printf " ✅"
        else
            printf " ❌"
        fi
    done < <(skill_target_specs)
    echo
done
