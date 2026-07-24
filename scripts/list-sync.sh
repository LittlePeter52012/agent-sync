#!/bin/bash
# list-sync.sh — show whitelist and coverage matrix
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"
PY="${PYTHON:-python3}"

echo "━━━ agent-sync 同步清单 ━━━"
echo "  Tool: $SYNC_HOME"
echo "  Hub:  $HUB_ROOT"
echo ""
echo "【重要】不会自动同步你在某个工具里新装的东西。"
echo "       只有 hub 白名单 + 执行 agent-sync 后才会分发。"
echo ""

echo "── 已纳入同步的技能（白名单）──"
while IFS= read -r skill; do
    [ -n "$skill" ] || continue
    if [ -d "$HUB_ROOT/skills/$skill" ]; then
        echo "  ✅ $skill"
    else
        echo "  ⚠️  $skill  (manifest 有，hub 缺文件)"
    fi
done < <(read_skill_list)

echo ""
echo "── 各工具覆盖矩阵 ──"
"$PY" - "$HUB_ROOT" <<'PY'
import json, re, sys
from pathlib import Path

hub = Path(sys.argv[1])
manifest = (hub / "manifest.yaml").read_text()
skills = []
mode = None
for line in manifest.splitlines():
    if line.startswith("skills:") or line.startswith("math_skills:"):
        mode = "skills"
        continue
    if mode == "skills":
        if line.startswith("  - "):
            skills.append(line[4:].strip())
        elif line and not line.startswith(" "):
            break

shared = []
mcp_path = hub / "mcp" / "shared-servers.json"
if mcp_path.exists():
    shared = list(json.loads(mcp_path.read_text()).get("mcpServers", {}).keys())

skill_targets = {
    "Gemini global": Path.home()/".gemini/config/skills",
    "Antigravity App": Path.home()/".gemini/antigravity/skills",
    "Antigravity CLI": Path.home()/".gemini/antigravity-cli/skills",
    "Antigravity IDE": Path.home()/".gemini/antigravity-ide/skills",
    "Cursor": Path.home()/".cursor/skills",
    "Claude": Path.home()/".claude/skills",
    "Codex": Path.home()/".codex/skills",
    "OpenCode": Path.home()/".config/opencode/skills",
    "Copilot": Path.home()/".copilot/skills",
    "Agents": Path.home()/".agents/skills",
}

def skill_ok(base: Path) -> str:
    if not skills:
        return "0/0"
    ok = sum(1 for s in skills if (base/s/"SKILL.md").exists())
    return f"{ok}/{len(skills)}"

def mcp_names_json(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    d = json.loads(path.read_text())
    return {k.lower() for k in (d.get(key) or {})}

def mcp_names_toml(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {m.group(1).lower() for m in re.finditer(r"^\[mcp_servers\.([^\].]+)\]", path.read_text(), re.M)}

print(f"{'工具':<20} {'Skills':<10} {'共享MCP':<10}")
print("-" * 44)
for name, base in skill_targets.items():
    print(f"{name:<20} {skill_ok(base):<10} {'—':<10}")

if shared:
    shared_l = [s.lower() for s in shared]
    mcp_targets = {
        "Gemini global": (Path.home()/".gemini/config/mcp_config.json", "json", "mcpServers"),
        "Antigravity App": (Path.home()/".gemini/antigravity/mcp_config.json", "json", "mcpServers"),
        "Antigravity CLI": (Path.home()/".gemini/antigravity-cli/mcp_config.json", "json", "mcpServers"),
        "Antigravity IDE": (Path.home()/".gemini/antigravity-ide/mcp_config.json", "json", "mcpServers"),
        "Cursor": (Path.home()/".cursor/mcp.json", "json", "mcpServers"),
        "Claude": (Path.home()/".claude.json", "json", "mcpServers"),
        "VSCode/Copilot": (Path.home()/"Library/Application Support/Code/User/mcp.json", "json", "servers"),
        "OpenCode": (Path.home()/".config/opencode/opencode.json", "json", "mcp"),
        "Codex": (Path.home()/".codex/config.toml", "toml", ""),
    }
    for name, (path, kind, key) in mcp_targets.items():
        names = mcp_names_json(path, key) if kind == "json" else mcp_names_toml(path)
        have = sum(1 for s in shared_l if s in names)
        print(f"{name:<20} {'—':<10} {have}/{len(shared)}")
PY

echo ""
echo "── 如何新增到同步 ──"
echo "  技能: 放入 \$AGENT_HUB_ROOT/skills/<name>/ + manifest.yaml → agent-sync skills"
echo "  MCP:  编辑 \$AGENT_HUB_ROOT/mcp/shared-servers.json → agent-sync mcp"
