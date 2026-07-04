#!/bin/bash
# bench-sync.sh — compare symlink vs rsync
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SCRIPT="$SYNC_HOME/scripts/sync-skills.sh"
BENCH_DIR="$HUB_ROOT/.sync-backups/bench-$$"

# pick first skill from manifest
FIRST_SKILL=$(read_skill_list | head -1)
if [ -z "$FIRST_SKILL" ]; then
    echo "No skills in manifest" >&2
    exit 1
fi
SOURCE="$HUB_ROOT/skills/$FIRST_SKILL/SKILL.md"
if [ ! -f "$SOURCE" ]; then
    echo "Missing $SOURCE" >&2
    exit 1
fi

mkdir -p "$BENCH_DIR/symlink-target" "$BENCH_DIR/rsync-target"

echo "━━━ Sync Method Benchmark ━━━"
echo ""

ln -sf "$HUB_ROOT/skills/$FIRST_SKILL" "$BENCH_DIR/symlink-target/$FIRST_SKILL"
SYMLINK_CONTENT=$(shasum -a 256 "$BENCH_DIR/symlink-target/$FIRST_SKILL/SKILL.md" | awk '{print $1}')
SOURCE_HASH=$(shasum -a 256 "$SOURCE" | awk '{print $1}')

rsync -a --delete "$HUB_ROOT/skills/$FIRST_SKILL/" "$BENCH_DIR/rsync-target/$FIRST_SKILL/"
RSYNC_CONTENT=$(shasum -a 256 "$BENCH_DIR/rsync-target/$FIRST_SKILL/SKILL.md" | awk '{print $1}')

echo "| Metric | symlink | rsync |"
echo "|--------|---------|-------|"
echo "| Content match | $([ "$SYMLINK_CONTENT" = "$SOURCE_HASH" ] && echo OK || echo FAIL) | $([ "$RSYNC_CONTENT" = "$SOURCE_HASH" ] && echo OK || echo FAIL) |"
echo "| Edit latency | immediate | needs re-sync |"
echo "| Disk use | links only | full copies × N tools |"
echo ""
echo "Recommended: symlink"
echo "bench dir: $BENCH_DIR"
