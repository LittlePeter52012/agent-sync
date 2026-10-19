#!/bin/bash
# test-suite.sh — full evaluation (bash 3.2+ compatible)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"
PY="${PYTHON:-python3}"
FAIL=0
PASS=0

ok() { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

echo "╔══════════════════════════════════════════════╗"
echo "║     agent-sync evaluation suite              ║"
echo "╚══════════════════════════════════════════════╝"
echo "  Tool: $SYNC_HOME"
echo "  Hub:  $HUB_ROOT"
echo ""

echo "[1] CLI"
if [ -x "$SYNC_HOME/bin/agent-sync" ]; then ok "agent-sync entrypoint exists"; else bad "agent-sync entrypoint exists"; fi
if "$SYNC_HOME/bin/agent-sync" help >/dev/null 2>&1; then ok "agent-sync help"; else bad "agent-sync help"; fi
if [ -f "$HUB_ROOT/manifest.yaml" ]; then ok "hub manifest exists"; else bad "hub manifest exists"; fi

SKILLS=$(read_skill_list | tr '\n' ' ')
SKILL_COUNT=$(read_skill_list | wc -l | tr -d ' ')
if [ "$SKILL_COUNT" -eq 0 ]; then
  bad "manifest lists skills"
else
  ok "manifest lists $SKILL_COUNT skills"
fi

echo ""
echo "[2] Skills coverage"
check_skills_dir() {
  local tool="$1" target_key="$2" base="$3"
  local n=0 s suppressed
  suppressed=$(
    "$PY" "$SCRIPT_DIR/capability_resolver.py" \
      --home "$HOME" \
      --hub "$HUB_ROOT" \
      suppressed-skills "$target_key"
  )
  for s in $SKILLS; do
    if printf '%s\n' "$suppressed" | grep -Fqx "$s" \
      || [ -f "$base/$s/SKILL.md" ]; then
      n=$((n+1))
    fi
  done
  if [ "$n" -eq "$SKILL_COUNT" ]; then ok "$tool skills $n/$SKILL_COUNT"
  else bad "$tool skills $n/$SKILL_COUNT"; fi
}
check_skills_dir "Claude Code" "claude" "$HOME/.claude/skills"
check_skills_dir "Cursor" "cursor" "$HOME/.cursor/skills"
check_skills_dir "Codex" "codex" "$HOME/.codex/skills"
check_skills_dir "Gemini CLI" "agents" "$HOME/.agents/skills"
check_skills_dir "Gemini global" "gemini-global" "$HOME/.gemini/config/skills"
check_skills_dir "Antigravity App" "antigravity-app" "$HOME/.gemini/antigravity/skills"
check_skills_dir "Antigravity CLI" "antigravity-cli" "$HOME/.gemini/antigravity-cli/skills"
check_skills_dir "Antigravity IDE" "antigravity-ide" "$HOME/.gemini/antigravity-ide/skills"
check_skills_dir "OpenCode" "opencode" "$HOME/.config/opencode/skills"
check_skills_dir "Copilot" "copilot" "$HOME/.copilot/skills"
check_skills_dir "Agents" "agents" "$HOME/.agents/skills"
if [ -f "${HERMES_HOME:-$HOME/.hermes}/config.yaml" ]; then
  if "$PY" "$SCRIPT_DIR/sync_hermes.py" --check >/dev/null; then
    ok "Hermes external Skills"
  else
    bad "Hermes external Skills"
  fi
fi

echo ""
echo "[3] Symlinks point at hub"
check_links() {
  local tool="$1" target_key="$2" base="$3"
  local broken=0 s suppressed
  suppressed=$(
    "$PY" "$SCRIPT_DIR/capability_resolver.py" \
      --home "$HOME" \
      --hub "$HUB_ROOT" \
      suppressed-skills "$target_key"
  )
  for s in $SKILLS; do
    if printf '%s\n' "$suppressed" | grep -Fqx "$s"; then
      if skill_is_hub_managed "$base/$s" "$HUB_ROOT/skills/$s"; then
        broken=$((broken+1))
      fi
      continue
    fi
    if [ ! -f "$base/$s/SKILL.md" ]; then broken=$((broken+1)); continue; fi
    skill_is_hub_managed "$base/$s" "$HUB_ROOT/skills/$s" \
      || broken=$((broken+1))
  done
  if [ "$broken" -eq 0 ]; then ok "$tool symlinks"; else bad "$tool has $broken bad links"; fi
}
check_links "Claude Code" "claude" "$HOME/.claude/skills"
check_links "Cursor" "cursor" "$HOME/.cursor/skills"
check_links "Codex" "codex" "$HOME/.codex/skills"
check_links "Gemini CLI" "agents" "$HOME/.agents/skills"
check_links "Gemini global" "gemini-global" "$HOME/.gemini/config/skills"
check_links "Antigravity App" "antigravity-app" "$HOME/.gemini/antigravity/skills"
check_links "Antigravity CLI" "antigravity-cli" "$HOME/.gemini/antigravity-cli/skills"
check_links "Antigravity IDE" "antigravity-ide" "$HOME/.gemini/antigravity-ide/skills"
check_links "OpenCode" "opencode" "$HOME/.config/opencode/skills"
check_links "Copilot" "copilot" "$HOME/.copilot/skills"
check_links "Agents" "agents" "$HOME/.agents/skills"

echo ""
echo "[4] MCP (if hub defines shared servers)"
if [ -f "$HUB_ROOT/mcp/shared-servers.json" ]; then
  if "$PY" - "$HUB_ROOT" "$SCRIPT_DIR" <<'PY'
import sys
from pathlib import Path
hub = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from config_inventory import SATISFIED_MCP_STATES, resolve_mcp_coverage

home = Path.home()
checks = [
    ("Gemini CLI", "gemini"),
    ("Gemini global", "gemini-global"),
    ("Antigravity App", "antigravity-app"),
    ("Antigravity CLI", "antigravity-cli"),
    ("Antigravity IDE", "antigravity-ide"),
    ("Cursor", "cursor"),
    ("Claude", "claude"),
    ("VSCode/Copilot", "vscode"),
    ("OpenCode", "opencode"),
    ("Codex", "codex"),
]
fail = 0
accepted = SATISFIED_MCP_STATES
for name, target in checks:
    coverage = resolve_mcp_coverage(target, home, hub)
    have = sum(item["state"] in accepted for item in coverage.values())
    status = "PASS" if have == len(coverage) else "FAIL"
    if status == "FAIL":
        fail += 1
    print(f"  {status}  {name} MCP {have}/{len(coverage)}")
sys.exit(fail)
PY
  then PASS=$((PASS+10)); else FAIL=$((FAIL+10)); fi
else
  ok "no shared MCP in hub (skip)"
fi

TMP_MCP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/agent-sync-mcp.XXXXXX")
trap 'rm -rf "$TMP_MCP_DIR"' EXIT
cat > "$TMP_MCP_DIR/shared.json" <<'JSON'
{
  "mcpServers": {
    "miro-mcp": {
      "type": "http",
      "url": "https://mcp.miro.com"
    }
  }
}
JSON
if "$PY" "$SCRIPT_DIR/merge-mcp.py" "$TMP_MCP_DIR/shared.json" "$TMP_MCP_DIR/cursor.json" >/dev/null \
  && grep -q '"url": "https://mcp.miro.com"' "$TMP_MCP_DIR/cursor.json"; then
  ok "HTTP MCP merges into JSON tools"
else
  bad "HTTP MCP merges into JSON tools"
fi
if "$PY" "$SCRIPT_DIR/merge-mcp-codex.py" "$TMP_MCP_DIR/shared.json" "$TMP_MCP_DIR/config.toml" >/dev/null \
  && grep -q 'url = "https://mcp.miro.com"' "$TMP_MCP_DIR/config.toml"; then
  ok "HTTP MCP merges into Codex"
else
  bad "HTTP MCP merges into Codex"
fi
if CLAUDE_BIN=claude "$PY" "$SCRIPT_DIR/sync-mcp-claude.py" "$TMP_MCP_DIR/shared.json" --dry-run \
  | grep -q "claude mcp add --scope user --transport http miro-mcp https://mcp.miro.com"; then
  ok "HTTP MCP maps to Claude CLI"
else
  bad "HTTP MCP maps to Claude CLI"
fi

echo ""
echo "[5] Tool package privacy (agent-sync repo must stay clean)"
if bash "$SCRIPT_DIR/privacy-audit.sh" >/dev/null 2>&1; then
  ok "privacy audit passes"
else
  bad "privacy audit passes"
fi

echo ""
echo "[6] Update & version"
if [ -f "$SYNC_HOME/VERSION" ]; then ok "VERSION file exists"; else bad "VERSION file exists"; fi
if bash "$SCRIPT_DIR/update-tool.sh" --check >/dev/null 2>&1; then
  ok "update --check runs"
else
  rc=$?
  if [ "$rc" -eq 2 ]; then ok "update --check runs (updates available)"
  else bad "update --check runs"; fi
fi
echo ""
echo "[7] Idempotency"
if "$SYNC_HOME/bin/agent-sync" all >/tmp/agent-sync-retest.log 2>&1; then ok "second agent-sync all"; else bad "second agent-sync all"; fi
if "$SYNC_HOME/bin/agent-sync" verify >/dev/null 2>&1; then ok "verify still passes"; else bad "verify still passes"; fi

echo ""
echo "════════════════════════════════════════"
echo "  PASS=$PASS  FAIL=$FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo "  RESULT: PASS"
  exit 0
fi
echo "  RESULT: FAIL"
exit 1
