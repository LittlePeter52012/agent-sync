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
        managed = (
            finding.get("scope") in {"managed", "policy"}
            or finding.get("owner") == "hub"
        )
        prefix = "Repair candidate" if managed else "Manual review"
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
