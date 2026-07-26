#!/bin/bash
# Shared helpers for agent-sync scripts

HUB_ROOT="${AGENT_HUB_ROOT:-$HOME/.config/agent-hub}"
SYNC_HOME="${AGENT_SYNC_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MANIFEST="$HUB_ROOT/manifest.yaml"

read_skill_list() {
    # Prefer skills:, fall back to math_skills: for older hubs
    awk '
      /^skills:|^math_skills:/{f=1; next}
      f && /^  - /{print substr($0,5); next}
      f && /^[^ #]/{exit}
    ' "$MANIFEST"
}

skill_is_hub_managed() {
    local destination="$1" source="$2"
    [ -L "$destination" ] || return 1
    "${PYTHON:-python3}" - "$destination" "$source" <<'PY'
import os
import sys

destination, source = sys.argv[1:3]
raise SystemExit(
    0 if os.path.realpath(destination) == os.path.realpath(source) else 1
)
PY
}

skill_targets() {
    cat <<EOF
$HOME/.gemini/config/skills
$HOME/.gemini/antigravity/skills
$HOME/.gemini/antigravity-cli/skills
$HOME/.gemini/antigravity-ide/skills
$HOME/.claude/skills
$HOME/.cursor/skills
$HOME/.codex/skills
$HOME/.config/opencode/skills
$HOME/.copilot/skills
$HOME/.agents/skills
EOF
}
