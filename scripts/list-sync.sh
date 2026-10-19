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
"$PY" - "$HUB_ROOT" "$SCRIPT_DIR" <<'PY'
import json, os, sys
from functools import lru_cache
from pathlib import Path

hub = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from capability_resolver import resolve_target_policy
from config_inventory import SATISFIED_MCP_STATES, resolve_mcp_coverage
from sync_hermes import HUB_SKILLS as hermes_hub_skills
from sync_hermes import is_registered as hermes_skills_registered

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
    "Gemini CLI": ("agents", Path.home()/".agents/skills"),
    "Gemini global": ("gemini-global", Path.home()/".gemini/config/skills"),
    "Antigravity App": ("antigravity-app", Path.home()/".gemini/antigravity/skills"),
    "Antigravity CLI": ("antigravity-cli", Path.home()/".gemini/antigravity-cli/skills"),
    "Antigravity IDE": ("antigravity-ide", Path.home()/".gemini/antigravity-ide/skills"),
    "Cursor": ("cursor", Path.home()/".cursor/skills"),
    "Claude": ("claude", Path.home()/".claude/skills"),
    "Codex": ("codex", Path.home()/".codex/skills"),
    "OpenCode": ("opencode", Path.home()/".config/opencode/skills"),
    "Copilot": ("copilot", Path.home()/".copilot/skills"),
    "Agents": ("agents", Path.home()/".agents/skills"),
}
hermes_home = Path(
    os.environ.get("HERMES_HOME", str(Path.home()/".hermes"))
).expanduser()
hermes_config = hermes_home/"config.yaml"
if (
    hermes_config.exists()
    and hermes_skills_registered(hermes_config.read_text())
):
    skill_targets["Hermes"] = ("hermes", hermes_hub_skills)

@lru_cache(maxsize=None)
def target_resolution(target: str):
    return resolve_target_policy(target, Path.home(), hub)

def suppressed_skills(target: str) -> set[str]:
    return set(target_resolution(target).suppressed_skills)

def skill_ok(target: str, base: Path) -> str:
    if not skills:
        return "0/0"
    suppressed = suppressed_skills(target)
    ok = sum(
        1
        for skill in skills
        if skill.casefold() in suppressed or (base/skill/"SKILL.md").exists()
    )
    return f"{ok}/{len(skills)}"

def skill_managed(target: str, base: Path) -> str:
    if not skills:
        return "0/0"
    suppressed = suppressed_skills(target)
    ok = 0
    expected = 0
    for skill in skills:
        if skill.casefold() in suppressed:
            continue
        expected += 1
        destination = base / skill
        source = hub / "skills" / skill
        if destination.is_symlink() and os.path.realpath(destination) == os.path.realpath(source):
            ok += 1
    return f"{ok}/{expected}"

print(f"{'工具':<20} {'Skills':<10} {'Hub受管':<10} {'共享MCP':<10}")
print("-" * 56)
for name, (target, base) in skill_targets.items():
    print(f"{name:<20} {skill_ok(target, base):<10} {skill_managed(target, base):<10} {'—':<10}")

if shared:
    mcp_targets = {
        "Gemini CLI": "gemini",
        "Gemini global": "gemini-global",
        "Antigravity App": "antigravity-app",
        "Antigravity CLI": "antigravity-cli",
        "Antigravity IDE": "antigravity-ide",
        "Cursor": "cursor",
        "Claude": "claude",
        "VSCode/Copilot": "vscode",
        "OpenCode": "opencode",
        "Codex": "codex",
    }
    for name, target in mcp_targets.items():
        coverage = resolve_mcp_coverage(
            target,
            Path.home(),
            hub,
            resolution=target_resolution(target),
        )
        have = sum(
            value["state"] in SATISFIED_MCP_STATES
            for value in coverage.values()
        )
        print(f"{name:<20} {'—':<10} {'—':<10} {have}/{len(coverage)}")
PY

echo ""
echo "── 如何新增到同步 ──"
echo "  技能: 放入 \$AGENT_HUB_ROOT/skills/<name>/ + manifest.yaml → agent-sync skills"
echo "  MCP:  编辑 \$AGENT_HUB_ROOT/mcp/shared-servers.json → agent-sync mcp"
