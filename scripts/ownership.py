#!/usr/bin/env python3
"""Render secret-free capability ownership decisions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from capability_resolver import CapabilityDecision, resolve_target_policy


DEFAULT_TARGETS = (
    "cursor",
    "gemini",
    "gemini-global",
    "antigravity-app",
    "antigravity-cli",
    "antigravity-ide",
    "claude",
    "opencode",
    "codex",
    "vscode",
)


def _targets(home: Path) -> list[str]:
    targets = list(DEFAULT_TARGETS)
    profiles = home / "Library" / "Application Support" / "Code" / "User" / "profiles"
    if profiles.exists():
        targets.extend(
            f"vscode:{path.name}"
            for path in sorted(profiles.iterdir())
            if path.is_dir()
        )
    return targets


def _public_decision(
    decision: CapabilityDecision,
) -> dict[str, Any]:
    return {
        "capability": decision.capability,
        "target": decision.target,
        "selection": decision.selection,
        "selection_source": decision.selection_source,
        "desired_owner": decision.desired_owner,
        "effective_owner": decision.effective_owner,
        "state": decision.state,
        "reason": decision.reason,
        "active_providers": list(decision.active_providers),
        "active_competitors": list(decision.active_competitors),
        "suppressed_skills": sorted(decision.suppressed_skills),
        "suppressed_mcp": sorted(decision.suppressed_mcp),
        "fallback_owner": decision.fallback_owner,
        "active_plugin_evidence": list(decision.evidence),
        "execution_location": decision.execution_location,
        "data_destination": decision.data_destination,
    }


def _target_report(target: str, home: Path, hub: Path) -> dict[str, Any]:
    resolution = resolve_target_policy(target, home, hub)
    return {
        "target": target,
        "capabilities": [
            _public_decision(decision)
            for _name, decision in sorted(resolution.capabilities.items())
        ],
        "findings": list(resolution.findings),
        "uncertain": list(resolution.uncertain),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capability", nargs="?")
    parser.add_argument("--target")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    home = Path.home()
    hub = Path(
        os.environ.get("AGENT_HUB_ROOT", home / ".config" / "agent-hub")
    ).expanduser()
    targets = [args.target.casefold()] if args.target else _targets(home)
    reports = [_target_report(target, home, hub) for target in targets]

    if args.capability:
        capability = args.capability.casefold()
        matches = [
            item
            for report in reports
            for item in report["capabilities"]
            if item["capability"] == capability
        ]
        if not matches:
            print(f"Unknown capability: {args.capability}", file=sys.stderr)
            return 2
        payload: Any = matches[0] if len(matches) == 1 else matches
    else:
        payload = reports[0] if len(reports) == 1 else {"targets": reports}

    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if isinstance(payload, dict) and "targets" in payload:
        items = payload["targets"]
    else:
        items = payload if isinstance(payload, list) else [payload]
    for item in items:
        if "capabilities" in item:
            print(f"目标：{item['target']}")
            decisions = item["capabilities"]
        else:
            decisions = [item]
        for decision in decisions:
            owner = decision["effective_owner"] or "不可用"
            print(
                f"  {decision['capability']}: {decision['state']} "
                f"({owner}, selection={decision['selection']})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
