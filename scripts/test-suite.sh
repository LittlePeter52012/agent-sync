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
if command -v agent-sync >/dev/null 2>&1; then ok "agent-sync on PATH"; else bad "agent-sync on PATH"; fi
if agent-sync help >/dev/null 2>&1; then ok "agent-sync help"; else bad "agent-sync help"; fi
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
  local tool="$1" base="$2"
  local n=0 s
  for s in $SKILLS; do
    [ -f "$base/$s/SKILL.md" ] && n=$((n+1))
  done
  if [ "$n" -eq "$SKILL_COUNT" ]; then ok "$tool skills $n/$SKILL_COUNT"
  else bad "$tool skills $n/$SKILL_COUNT"; fi
}
check_skills_dir "Claude Code" "$HOME/.claude/skills"
check_skills_dir "Cursor" "$HOME/.cursor/skills"
check_skills_dir "Codex" "$HOME/.codex/skills"
check_skills_dir "Antigravity" "$HOME/.gemini/config/skills"
check_skills_dir "OpenCode" "$HOME/.config/opencode/skills"
check_skills_dir "Copilot" "$HOME/.copilot/skills"
check_skills_dir "Agents" "$HOME/.agents/skills"

echo ""
echo "[3] Symlinks point at hub"
check_links() {
  local tool="$1" base="$2"
  local broken=0 s target
  for s in $SKILLS; do
    if [ ! -f "$base/$s/SKILL.md" ]; then broken=$((broken+1)); continue; fi
    if [ -L "$base/$s" ]; then
      target=$(readlink "$base/$s" 2>/dev/null || true)
      case "$target" in
        "$HUB_ROOT/skills"/*) ;;
        *) broken=$((broken+1)) ;;
      esac
    fi
  done
  if [ "$broken" -eq 0 ]; then ok "$tool symlinks"; else bad "$tool has $broken bad links"; fi
}
check_links "Claude Code" "$HOME/.claude/skills"
check_links "Cursor" "$HOME/.cursor/skills"
check_links "Codex" "$HOME/.codex/skills"
check_links "Antigravity" "$HOME/.gemini/config/skills"
check_links "OpenCode" "$HOME/.config/opencode/skills"
check_links "Copilot" "$HOME/.copilot/skills"
check_links "Agents" "$HOME/.agents/skills"

echo ""
echo "[4] MCP (if hub defines shared servers)"
if [ -f "$HUB_ROOT/mcp/shared-servers.json" ]; then
  "$PY" - "$HUB_ROOT" <<'PY'
import json, re, sys
from pathlib import Path
hub = Path(sys.argv[1])
shared = [k.lower() for k in json.loads((hub/"mcp/shared-servers.json").read_text()).get("mcpServers", {})]
home = Path.home()
checks = [
    ("Antigravity", home/".gemini/config/mcp_config.json", "json", "mcpServers"),
    ("Cursor", home/".cursor/mcp.json", "json", "mcpServers"),
    ("VSCode/Copilot", home/"Library/Application Support/Code/User/mcp.json", "json", "servers"),
    ("OpenCode", home/".config/opencode/opencode.json", "json", "mcp"),
    ("Codex", home/".codex/config.toml", "toml", ""),
]
fail = 0
for name, path, kind, key in checks:
    if kind == "json":
        names = {k.lower() for k in json.loads(path.read_text()).get(key, {})}
    else:
        names = {m.group(1).lower() for m in re.finditer(r"^\[mcp_servers\.([^\].]+)\]", path.read_text(), re.M)}
    have = sum(1 for s in shared if s in names)
    status = "PASS" if have == len(shared) else "FAIL"
    if status == "FAIL":
        fail += 1
    print(f"  {status}  {name} MCP {have}/{len(shared)}")
sys.exit(fail)
PY
  if [ $? -eq 0 ]; then PASS=$((PASS+5)); else FAIL=$((FAIL+5)); fi
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
# Only scan the TOOL package, never the personal hub
if grep -RInE 'eyJ[a-zA-Z0-9_-]{20,}|tvly-[a-zA-Z0-9]{8,}|Bearer [A-Za-z0-9+/=]{16,}' \
  "$SYNC_HOME/bin" "$SYNC_HOME/scripts" "$SYNC_HOME/examples" "$SYNC_HOME"/README.md 2>/dev/null \
  | grep -v 'test-suite.sh' | head -5 | grep -q .; then
  bad "tool package may contain tokens"
else
  ok "tool package has no token patterns"
fi
if grep -RInE '/Users/[^/{]|OneDrive-个人|DoctorTANG' \
  "$SYNC_HOME/bin" "$SYNC_HOME/scripts" "$SYNC_HOME/examples" "$SYNC_HOME"/README.md "$SYNC_HOME"/VERSION 2>/dev/null \
  | grep -v 'test-suite.sh' | grep -v 'privacy-audit.sh' | head -5 | grep -q .; then
  bad "tool package contains personal paths"
else
  ok "tool package has no personal paths"
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
if bash "$SCRIPT_DIR/privacy-audit.sh" >/dev/null 2>&1; then ok "privacy audit passes"; else bad "privacy audit passes"; fi

echo ""
echo "[7] Idempotency"
if agent-sync all >/tmp/agent-sync-retest.log 2>&1; then ok "second agent-sync all"; else bad "second agent-sync all"; fi
if agent-sync verify >/dev/null 2>&1; then ok "verify still passes"; else bad "verify still passes"; fi

echo ""
echo "════════════════════════════════════════"
echo "  PASS=$PASS  FAIL=$FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo "  RESULT: PASS"
  exit 0
fi
echo "  RESULT: FAIL"
exit 1
