#!/bin/bash
# sync-skills.sh — symlink whitelist skills from personal hub to all tools
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SOURCE_DIR="$HUB_ROOT/skills"
BACKUP_ROOT="$HUB_ROOT/.sync-backups"

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

backup_existing_dest() {
    local dest="$1" skill_name="$2" target_dir="$3"
    local backup_dir="$BACKUP_ROOT/$(date +%Y%m%d_%H%M%S)/${target_dir##*/}"
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
        [ "$DRY_RUN" = false ] && { rm "$dest"; ln -s "$source" "$dest"; }
        log "  ↻ $skill_name @ ${target_dir##*/}"
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

echo "━━━ agent-sync skills ($METHOD) ━━━"
echo "  Hub: $SOURCE_DIR"
[ "$DRY_RUN" = true ] && echo "  [DRY RUN]"
echo ""

while IFS= read -r target_dir; do
    [ -n "$target_dir" ] || continue
    log "→ $target_dir"
    mkdir -p "$target_dir"
    for skill in "${SKILLS[@]}"; do
        if [ "$METHOD" = "rsync" ]; then
            rsync_skill "$skill" "$target_dir"
        else
            link_skill "$skill" "$target_dir"
        fi
    done
    echo ""
done < <(skill_targets)

log "Verification:"
for skill in "${SKILLS[@]}"; do
    printf "  %-35s" "$skill"
    while IFS= read -r target_dir; do
        if skill_present "$target_dir/$skill"; then printf " ✅"; else printf " ❌"; fi
    done < <(skill_targets)
    echo
done
