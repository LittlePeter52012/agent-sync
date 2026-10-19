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

TOKEN_RE='eyJ[a-zA-Z0-9_-]{20,}|(^|[^A-Za-z0-9])sk-(proj-)?[a-zA-Z0-9_-]{20,}|gh[pousr]_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{20,}|tvly-[a-zA-Z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{30,}|Bearer [A-Za-z0-9._~+/=-]{20,}'
CURRENT_USER=$(basename "$HOME")
PII_RE='/Users/[A-Za-z0-9._-]+/|OneDrive-[^/[:space:]]+'
HISTORY_PII_RE="$PII_RE"
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

scan_tree() {
    local root="$1" mode="$2"
    python3 - "$root" "$mode" "$CURRENT_USER" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
mode = sys.argv[2]
current_user = sys.argv[3]
excluded_parts = {
    ".git",
    ".worktrees",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "_build",
    ".sync-backups",
}
excluded_files = {
    "scripts/privacy-audit.sh",
    "scripts/test-suite.sh",
}
token = re.compile(
    r"eyJ[a-zA-Z0-9_-]{20,}"
    r"|(?<![A-Za-z0-9])sk-(?:proj-)?[a-zA-Z0-9_-]{20,}"
    r"|gh[pousr]_[a-zA-Z0-9]{20,}"
    r"|github_pat_[a-zA-Z0-9_]{20,}"
    r"|tvly-[a-zA-Z0-9_-]{8,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|Bearer [A-Za-z0-9._~+/=-]{20,}"
)
pii = re.compile(
    r"/Users/[A-Za-z0-9._-]+/"
    r"|OneDrive-[^/\s]+"
)
if current_user:
    home_literal = re.compile(rf"/Users/{re.escape(current_user)}/")
else:
    home_literal = re.compile(r"(?!x)x")

found = False
if root.exists():
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in excluded_parts for part in relative.parts):
            continue
        if relative.as_posix() in excluded_files or not path.is_file():
            continue
        try:
            if path.stat().st_size > 5 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if mode == "token":
            matched = token.search(text)
        else:
            matched = pii.search(text) or home_literal.search(text)
        if matched:
            found = True
            break
raise SystemExit(1 if found else 0)
PY
}

echo "╔══════════════════════════════════════════════╗"
echo "║     agent-sync privacy audit                 ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

echo "[1] Public tool package ($SYNC_HOME)"
if ! scan_tree "$SYNC_HOME" token; then
    bad "tool contains token-like strings"
else
    ok "tool has no tokens"
fi

if ! scan_tree "$SYNC_HOME" pii; then
    bad "tool contains personal paths/names"
else
    ok "tool has no personal paths"
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
    if ! scan_tree "$HUB_ROOT" token; then
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
