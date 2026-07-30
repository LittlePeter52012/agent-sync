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
    skill_target_specs | cut -d '|' -f 3-
}

skill_target_specs() {
    cat <<EOF
gemini-global|Gemini global|$HOME/.gemini/config/skills
antigravity-app|Antigravity App|$HOME/.gemini/antigravity/skills
antigravity-cli|Antigravity CLI|$HOME/.gemini/antigravity-cli/skills
antigravity-ide|Antigravity IDE|$HOME/.gemini/antigravity-ide/skills
claude|Claude|$HOME/.claude/skills
cursor|Cursor|$HOME/.cursor/skills
codex|Codex|$HOME/.codex/skills
opencode|OpenCode|$HOME/.config/opencode/skills
copilot|Copilot|$HOME/.copilot/skills
agents|Agents|$HOME/.agents/skills
EOF
}
