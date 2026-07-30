#!/bin/bash
# verify-all.sh — structural verification for hub + tool coverage
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"
PY="${PYTHON:-python3}"
FAIL=0

check() {
    local label="$1"; shift
    if "$@"; then
        echo "  ✅ $label"
    else
        echo "  ❌ $label"
        FAIL=$((FAIL + 1))
    fi
}

echo "━━━ agent-sync verify ━━━"
echo "  Hub: $HUB_ROOT"
echo ""

SKILLS=()
SKILL_COUNT=0
while IFS= read -r s; do
    if [ -n "$s" ]; then
        SKILLS+=("$s")
        SKILL_COUNT=$((SKILL_COUNT + 1))
    fi
done < <(read_skill_list)

echo "[skills]"
if [ "$SKILL_COUNT" -eq 0 ]; then
    echo "  (no shared Skills — skip)"
else
    for skill in "${SKILLS[@]}"; do
        check "$skill @ hub" test -f "$HUB_ROOT/skills/$skill/SKILL.md"
        while IFS='|' read -r target_key target_label base; do
            suppressed=$(
                "$PY" "$SCRIPT_DIR/capability_resolver.py" \
                    --home "$HOME" \
                    --hub "$HUB_ROOT" \
                    suppressed-skills "$target_key"
            )
            if printf '%s\n' "$suppressed" | grep -Fqx "$skill"; then
                if skill_is_hub_managed \
                    "$base/$skill" \
                    "$HUB_ROOT/skills/$skill"; then
                    echo "  ❌ $skill plugin/policy-owned @ $target_label (duplicate Hub link)"
                    FAIL=$((FAIL + 1))
                else
                    echo "  ✅ $skill plugin/policy-owned @ $target_label"
                fi
            else
                check "$skill Hub-managed @ $base" \
                    skill_is_hub_managed \
                    "$base/$skill" \
                    "$HUB_ROOT/skills/$skill"
            fi
        done < <(skill_target_specs)
    done
fi

echo ""
echo "[hermes]"
if [ -f "${HERMES_HOME:-$HOME/.hermes}/config.yaml" ]; then
    check "Hermes uses ~/.agents/skills" "$PY" "$SCRIPT_DIR/sync_hermes.py" --check
else
    echo "  (Hermes not configured — skip)"
fi

echo ""
echo "[mcp]"
if [ -f "$HUB_ROOT/mcp/shared-servers.json" ]; then
    if "$PY" - "$HUB_ROOT" "$SCRIPT_DIR" <<'PY'
import sys
from pathlib import Path

hub = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from config_inventory import _source_specs, resolve_mcp_coverage

home = Path.home()

failures = 0
accepted = {"hub-managed", "plugin-owned", "excluded"}
for target, label, _path, _bucket in _source_specs(home):
    coverage = resolve_mcp_coverage(target, home, hub)
    count = sum(record["state"] in accepted for record in coverage.values())
    total = len(coverage)
    marker = "✅" if count == total else "❌"
    print(f"  {marker} {label} shared MCP {count}/{total}")
    for name, record in coverage.items():
        if record["state"] not in accepted:
            print(f"      - {name}: {record['state']}")
    failures += count != total
sys.exit(failures)
PY
    then
        :
    else
        rc=$?
        FAIL=$((FAIL + rc))
    fi
else
    echo "  (no mcp/shared-servers.json — skip)"
fi

echo ""
echo "[rules]"
if [ -d "$HUB_ROOT/rules" ] && ls "$HUB_ROOT/rules"/*.md >/dev/null 2>&1; then
    if "$PY" - "$HUB_ROOT" <<'PY'
import sys
from pathlib import Path

hub = Path(sys.argv[1])
home = Path.home()
targets = (
    ("Claude", home / ".claude" / "CLAUDE.md"),
    ("Codex", home / ".codex" / "AGENTS.md"),
    ("Gemini", home / ".gemini" / "GEMINI.md"),
)
failures = 0
for rule in sorted((hub / "rules").glob("*.md")):
    content = rule.read_text(encoding="utf-8").strip()
    begin = f"<!-- agent-sync:begin:{rule.stem} -->"
    end = f"<!-- agent-sync:end:{rule.stem} -->"
    for label, target in targets:
        ok = False
        if target.exists():
            text = target.read_text(encoding="utf-8")
            if text.count(begin) == 1 and text.count(end) == 1:
                block = text.split(begin, 1)[1].split(end, 1)[0]
                ok = content in block
        print(
            f"  {'✅' if ok else '❌'} {label} managed rule {rule.name}"
        )
        failures += not ok
raise SystemExit(failures)
PY
    then
        :
    else
        rc=$?
        FAIL=$((FAIL + rc))
    fi
else
    echo "  (no rules — skip)"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "全部通过 ✅"
else
    echo "$FAIL 项失败 ❌"
    exit 1
fi
