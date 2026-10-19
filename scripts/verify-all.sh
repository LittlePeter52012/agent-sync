#!/bin/bash
# verify-all.sh — structural verification for hub + tool coverage
# Runtime health is intentionally owned by explicit Doctor callers.
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

TARGETS_FILE=$(mktemp)
SKILLS_FILE=$(mktemp)
trap 'rm -f "$TARGETS_FILE" "$SKILLS_FILE"' EXIT
skill_target_specs > "$TARGETS_FILE"
read_skill_list > "$SKILLS_FILE"

if "$PY" - "$HUB_ROOT" "$SCRIPT_DIR" "$TARGETS_FILE" "$SKILLS_FILE" <<'PY'
import os
import sys
from pathlib import Path

hub = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
target_file = Path(sys.argv[3])
skills_file = Path(sys.argv[4])

from capability_resolver import host_policy_status, resolve_target_policy
from config_inventory import (
    SATISFIED_MCP_STATES,
    _source_specs,
    read_canonical_shared_mcp,
    resolve_mcp_coverage,
)
from host_policy import host_policy_path

home = Path.home()
skills = [
    line.strip().casefold()
    for line in skills_file.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
skill_targets = []
for line in target_file.read_text(encoding="utf-8").splitlines():
    target, label, base = line.split("|", 2)
    skill_targets.append((target, label, Path(base)))

resolutions = {}

def resolution(target):
    if target not in resolutions:
        resolutions[target] = resolve_target_policy(target, home, hub)
    return resolutions[target]

def hub_managed(destination, source):
    return (
        destination.is_symlink()
        and os.path.realpath(destination) == os.path.realpath(source)
    )

failures = 0
print("[host policy]")
override_count, host_issue = host_policy_status(home, hub)
if host_issue:
    print(f"  ❌ {host_issue}")
    failures += 1
elif override_count:
    print(f"  ✅ {override_count} override(s) in {host_policy_path(home)}")
else:
    print("  ✅ none")

print("\n[skills]")
if not skills:
    print("  (no shared Skills — skip)")
else:
    for skill in skills:
        source = hub / "skills" / skill
        source_ok = (source / "SKILL.md").is_file()
        print(f"  {'✅' if source_ok else '❌'} {skill} @ hub")
        failures += not source_ok
        for target, label, base in skill_targets:
            managed = hub_managed(base / skill, source)
            suppressed = skill in resolution(target).suppressed_skills
            if suppressed:
                ok = not managed
                suffix = " (duplicate Hub link)" if not ok else ""
                owner = resolution(target).skill_owners.get(skill)
                kind = "excluded" if owner == "excluded" else "plugin/policy-owned"
                print(
                    f"  {'✅' if ok else '❌'} {skill} "
                    f"{kind} @ {label}{suffix}"
                )
            else:
                ok = managed
                print(f"  {'✅' if ok else '❌'} {skill} Hub-managed @ {base}")
            failures += not ok

print("\n[mcp]")
shared, issue = read_canonical_shared_mcp(hub)
if issue:
    print("  ❌ Canonical shared MCP configuration is invalid or unreadable.")
    failures += 1
else:
    for target, label, _path, _bucket in _source_specs(home):
        coverage = resolve_mcp_coverage(
            target,
            home,
            hub,
            shared=shared,
            resolution=resolution(target),
        )
        count = sum(
            record["state"] in SATISFIED_MCP_STATES
            for record in coverage.values()
        )
        total = len(coverage)
        ok = count == total
        print(f"  {'✅' if ok else '❌'} {label} shared MCP {count}/{total}")
        for name, record in coverage.items():
            if record["state"] not in SATISFIED_MCP_STATES:
                print(f"      - {name}: {record['state']}")
            elif record["state"] == "disabled-on-host":
                print(f"      · {name}: disabled-on-host — {record.get('reason', '')}")
        failures += not ok

raise SystemExit(failures)
PY
then
    :
else
    rc=$?
    FAIL=$((FAIL + rc))
fi

echo ""
echo "[hermes]"
if [ -f "${HERMES_HOME:-$HOME/.hermes}/config.yaml" ]; then
    check "Hermes registers its Hub Skill directory" "$PY" "$SCRIPT_DIR/sync_hermes.py" --check
else
    echo "  (Hermes not configured — skip)"
fi

echo ""
echo "[rules]"
if [ -d "$HUB_ROOT/rules" ] && ls "$HUB_ROOT/rules"/*.md >/dev/null 2>&1; then
    if "$PY" - "$HUB_ROOT" "$SCRIPT_DIR" <<'PY'
import sys
from pathlib import Path

hub = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from rule_blocks import (
    build_managed_block,
    parse_managed_blocks,
    source_marker_issues,
)

home = Path.home()
targets = (
    ("Claude", home / ".claude" / "CLAUDE.md"),
    ("Codex", home / ".codex" / "AGENTS.md"),
    ("Gemini", home / ".gemini" / "GEMINI.md"),
)
failures = 0
rules = sorted((hub / "rules").glob("*.md"))
names = {rule.stem for rule in rules}
rule_data = []
invalid_sources = set()
for rule in rules:
    with rule.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    rule_data.append((rule, content))
    if source_marker_issues(rule.stem, content):
        invalid_sources.add(rule.stem)
        failures += 1
        print(f"  ❌ structural rule error in source {rule.name}")
for label, target in targets:
    if target.exists():
        with target.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        parsed = parse_managed_blocks(text, names)
        if parsed.issues:
            failures += 1
            print(f"  ❌ structural rule error in {label} target")
            continue
    else:
        parsed = None
    for rule, content in rule_data:
        if rule.stem in invalid_sources:
            continue
        ok = False
        if parsed is not None:
            matching = [
                block
                for block in parsed.blocks
                if block.name == rule.stem
            ]
            expected = build_managed_block(
                rule.stem,
                content,
                include_priority=label != "Codex",
            )
            ok = len(matching) == 1 and matching[0].text == expected
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
