#!/usr/bin/env python3
"""Render a secret-free repair preview from an Agent Doctor JSON report."""

from __future__ import annotations

import json
import sys
from typing import Any


def render_preview(report: dict[str, Any]) -> str:
    findings = report.get("findings", [])
    if not isinstance(findings, list) or not findings:
        return "No repair actions required."

    lines: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        message = finding.get("message")
        if not isinstance(message, str) or not message:
            continue
        prefix = "Repair candidate" if finding.get("repair") else "Manual review"
        lines.append(f"  - {prefix}: {message}")
    return "\n".join(lines) or "No repair actions required."


def main() -> int:
    try:
        report = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Invalid Agent Doctor report: {exc}", file=sys.stderr)
        return 1
    if not isinstance(report, dict):
        print("Invalid Agent Doctor report: root must be an object", file=sys.stderr)
        return 1
    print(render_preview(report))
    findings = report.get("findings", [])
    has_repair = isinstance(findings, list) and any(
        isinstance(item, dict) and bool(item.get("repair"))
        for item in findings
    )
    return 2 if has_repair else 0


if __name__ == "__main__":
    raise SystemExit(main())
