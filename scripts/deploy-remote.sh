#!/bin/bash
# deploy-remote.sh — push committed tool + private Hub snapshots over SSH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SSH_BIN="${AGENT_SYNC_SSH_BIN:-ssh}"
HOST="${1:-}"
APPLY=false

usage() {
    cat <<'EOF'
Usage: agent-sync deploy HOST [--apply]

Without --apply, run a read-only SSH preflight. With --apply, stream the
committed agent-sync and Agent Hub snapshots to HOST, preserve the previous
remote copies under ~/.local/state/agent-sync/remote-backups/, run
agent-sync all, and report runtime health.
EOF
}

[ -n "$HOST" ] || { usage >&2; exit 1; }
case "$HOST" in
    -*|*[!A-Za-z0-9._@:-]*)
        echo "Invalid SSH host: $HOST" >&2
        exit 1
        ;;
esac
shift
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "Unknown deploy option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

ssh_remote() {
    "$SSH_BIN" \
        -o BatchMode=yes \
        -o ConnectTimeout="${AGENT_SYNC_SSH_TIMEOUT:-10}" \
        "$HOST" "$@"
}

require_committed_repo() {
    local directory="$1" label="$2"
    if ! git -C "$directory" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "$label is not a git repository: $directory" >&2
        exit 1
    fi
    if [ -n "$(git -C "$directory" status --porcelain)" ]; then
        echo "$label has uncommitted changes: $directory" >&2
        exit 1
    fi
}

require_committed_repo "$SYNC_HOME" "agent-sync"
require_committed_repo "$HUB_ROOT" "Agent Hub"

if ! $APPLY; then
    echo "Preflight only — no remote files will be changed."
    ssh_remote '
set -eu
printf "Host: "
hostname
printf "agent-sync: "
if [ -f "$HOME/.local/share/agent-sync/VERSION" ]; then
    cat "$HOME/.local/share/agent-sync/VERSION"
else
    printf "not installed\n"
fi
printf "Hub: "
if [ -f "$HOME/.config/agent-hub/manifest.yaml" ]; then
    printf "present\n"
else
    printf "not installed\n"
fi
'
    echo "Apply with: agent-sync deploy $HOST --apply"
    exit 0
fi

LOCAL_TMP=$(mktemp -d "${TMPDIR:-/tmp}/agent-sync-deploy.XXXXXX")
trap 'rm -rf "$LOCAL_TMP"' EXIT
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
REMOTE_STAGE="/tmp/agent-sync-deploy-$RUN_ID"
TOOL_SHA=$(git -C "$SYNC_HOME" rev-parse HEAD)
HUB_SHA=$(git -C "$HUB_ROOT" rev-parse HEAD)

git -C "$SYNC_HOME" archive --format=tar -o "$LOCAL_TMP/tool.tar" HEAD
git -C "$HUB_ROOT" archive --format=tar -o "$LOCAL_TMP/hub.tar" HEAD

echo "Staging committed agent-sync snapshot on $HOST ..."
ssh_remote \
    "set -eu; umask 077; mkdir -p '$REMOTE_STAGE/tool'; tar -xf - -C '$REMOTE_STAGE/tool'" \
    < "$LOCAL_TMP/tool.tar"

echo "Staging committed Agent Hub snapshot on $HOST ..."
ssh_remote \
    "set -eu; umask 077; mkdir -p '$REMOTE_STAGE/hub'; tar -xf - -C '$REMOTE_STAGE/hub'" \
    < "$LOCAL_TMP/hub.tar"

echo "Activating snapshots and synchronizing remote Agents ..."
ssh_remote \
    "env AGENT_SYNC_REMOTE_STAGE='$REMOTE_STAGE' AGENT_SYNC_REMOTE_RUN='$RUN_ID' AGENT_SYNC_TOOL_SHA='$TOOL_SHA' AGENT_SYNC_HUB_SHA='$HUB_SHA' bash -s" \
    <<'REMOTE_SCRIPT'
set -eu

tool="$HOME/.local/share/agent-sync"
hub="$HOME/.config/agent-hub"
stage="$AGENT_SYNC_REMOTE_STAGE"
backup="$HOME/.local/state/agent-sync/remote-backups/$AGENT_SYNC_REMOTE_RUN"
state="$HOME/.local/state/agent-sync"
had_tool=false
had_hub=false
activation_started=false

rollback() {
    if [ "$activation_started" != true ]; then
        return
    fi
    mkdir -p "$backup"
    [ ! -e "$tool" ] || mv "$tool" "$backup/failed-tool"
    [ ! -e "$hub" ] || mv "$hub" "$backup/failed-hub"
    if [ "$had_tool" = true ]; then
        mkdir -p "$(dirname "$tool")"
        mv "$backup/tool" "$tool"
    fi
    if [ "$had_hub" = true ]; then
        mkdir -p "$(dirname "$hub")"
        mv "$backup/hub" "$hub"
    fi
    printf "Remote activation failed; previous snapshots restored from %s\n" "$backup" >&2
}
trap rollback EXIT HUP INT TERM

test -x "$stage/tool/bin/agent-sync"
test -f "$stage/hub/manifest.yaml"
mkdir -p "$backup" "$(dirname "$tool")" "$(dirname "$hub")" "$state"
activation_started=true
if [ -e "$tool" ]; then
    mv "$tool" "$backup/tool"
    had_tool=true
fi
if [ -e "$hub" ]; then
    mv "$hub" "$backup/hub"
    had_hub=true
fi
mv "$stage/tool" "$tool"
mv "$stage/hub" "$hub"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
if ! "$tool/bin/agent-sync" all; then
    exit 1
fi

activation_started=false
trap - EXIT HUP INT TERM
{
    printf "tool_sha=%s\n" "$AGENT_SYNC_TOOL_SHA"
    printf "hub_sha=%s\n" "$AGENT_SYNC_HUB_SHA"
    printf "deployed_utc=%s\n" "$AGENT_SYNC_REMOTE_RUN"
} > "$state/last-remote-deploy"
rmdir "$stage" 2>/dev/null || true

/bin/zsh -lic '"$HOME/.local/share/agent-sync/bin/agent-sync" doctor --runtime' || true
printf "Backup: %s\n" "$backup"
REMOTE_SCRIPT

echo "Remote deployment completed: $HOST"
echo "  agent-sync: $TOOL_SHA"
echo "  Agent Hub:  $HUB_SHA"
