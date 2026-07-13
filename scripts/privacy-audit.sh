#!/bin/bash
# privacy-audit.sh — scan tool + hub for leaked secrets / PII
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

FAIL=0
WARN=0

ok()   { echo "  OK    $1"; }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN  $1"; WARN=$((WARN+1)); }

TOKEN_RE='eyJ[a-zA-Z0-9_-]{20,}|sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|gho_[a-zA-Z0-9]{20,}|tvly-[a-zA-Z0-9]{8,}|Bearer [A-Za-z0-9+/=]{20,}'
CURRENT_USER=$(basename "$HOME")
PII_RE='/Users/[^/{]+|OneDrive-[^/ ]+'
HISTORY_PII_RE="$PII_RE"
[ -n "$CURRENT_USER" ] && HISTORY_PII_RE="$HISTORY_PII_RE|$CURRENT_USER"
# Runtime check for home path leaks (not stored as literal in source)
HOME_LEAK=$(printf '%s' "$HOME" | sed 's/[\/&]/\\&/g')

github_slug() {
    local url="${1:-}" slug=""
    case "$url" in
        git@github.com:*) slug="${url#git@github.com:}" ;;
        https://github.com/*) slug="${url#https://github.com/}" ;;
        http://github.com/*) slug="${url#http://github.com/}" ;;
    esac
    slug="${slug%.git}"
    printf '%s' "$slug"
}

echo "╔══════════════════════════════════════════════╗"
echo "║     agent-sync privacy audit                 ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

echo "[1] Public tool package ($SYNC_HOME)"
if grep -RInE "$TOKEN_RE" "$SYNC_HOME/bin" "$SYNC_HOME/scripts" "$SYNC_HOME/examples" \
    "$SYNC_HOME/README.md" "$SYNC_HOME/VERSION" 2>/dev/null \
    | grep -v 'privacy-audit.sh' | grep -v 'test-suite.sh' | head -3 | grep -q .; then
    bad "tool contains token-like strings"
else
    ok "tool has no tokens"
fi

if grep -RInE "$PII_RE" "$SYNC_HOME/bin" "$SYNC_HOME/scripts" "$SYNC_HOME/examples" \
    "$SYNC_HOME/README.md" 2>/dev/null \
    | grep -v 'privacy-audit.sh' | grep -v 'test-suite.sh' | head -3 | grep -q .; then
    bad "tool contains personal paths/names"
else
    ok "tool has no personal paths"
fi

# Scan for current user's home path in tracked tool files (dynamic, not hardcoded)
if [ -n "$HOME_LEAK" ] && grep -RInF "$HOME" "$SYNC_HOME/bin" "$SYNC_HOME/scripts" "$SYNC_HOME/examples" \
    "$SYNC_HOME/README.md" "$SYNC_HOME/VERSION" 2>/dev/null \
    | grep -v 'privacy-audit.sh' | grep -v 'test-suite.sh' | head -3 | grep -q .; then
    bad "tool contains current home path"
else
    ok "tool has no home path leaks"
fi

if git -C "$SYNC_HOME" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git -C "$SYNC_HOME" grep -nIE "$HISTORY_PII_RE|$TOKEN_RE" "$(git -C "$SYNC_HOME" rev-list --all)" -- . \
        ':(exclude)scripts/privacy-audit.sh' ':(exclude)scripts/test-suite.sh' 2>/dev/null \
        | head -3 | grep -q .; then
        bad "tool git history may contain secrets/PII"
    else
        ok "tool git history clean"
    fi
fi

echo ""
echo "[2] Personal hub ($HUB_ROOT)"
if [ ! -d "$HUB_ROOT" ]; then
    warn "hub not found — skip"
else
    if grep -RInE "$TOKEN_RE" "$HUB_ROOT" 2>/dev/null \
        | grep -v '.git/' | grep -v 'privacy-audit' | grep -v '\${' | head -3 | grep -q .; then
        bad "hub contains raw tokens (use \${ENV} placeholders)"
    else
        ok "hub uses placeholders (no raw tokens in Hub files)"
    fi

    if git -C "$HUB_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        hub_slug=$(github_slug "$(git -C "$HUB_ROOT" remote get-url origin 2>/dev/null || true)")
        if [ -n "$hub_slug" ] && command -v gh >/dev/null 2>&1; then
            vis=$(gh repo view "$hub_slug" --json isPrivate -q .isPrivate 2>/dev/null || echo "unknown")
            if [ "$vis" = "false" ]; then
                bad "hub GitHub repo is PUBLIC — must be private"
            elif [ "$vis" = "true" ]; then
                ok "hub GitHub repo is private"
            else
                warn "could not verify hub repo visibility"
            fi
        else
            warn "hub GitHub remote or gh CLI unavailable — visibility not verified"
        fi
    fi
fi

echo ""
echo "[3] Remote public repo check"
if command -v gh >/dev/null 2>&1; then
    tool_slug=$(github_slug "$(git -C "$SYNC_HOME" remote get-url origin 2>/dev/null || true)")
    if [ -n "$tool_slug" ]; then
        pub=$(gh repo view "$tool_slug" --json isPrivate -q .isPrivate 2>/dev/null || echo "unknown")
        if [ "$pub" = "false" ]; then
            ok "agent-sync GitHub repo is public (expected)"
        elif [ "$pub" = "true" ]; then
            warn "agent-sync GitHub repo is private"
        else
            warn "could not verify agent-sync repo visibility"
        fi
    else
        warn "agent-sync GitHub remote is unavailable"
    fi
else
    warn "gh CLI not available — skip remote check"
fi

echo ""
echo "════════════════════════════════════════"
echo "  FAIL=$FAIL  WARN=$WARN"
if [ "$FAIL" -eq 0 ]; then
    echo "  RESULT: PASS (privacy OK)"
    exit 0
fi
echo "  RESULT: FAIL — fix issues before publishing"
exit 1
