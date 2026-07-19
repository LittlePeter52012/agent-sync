#!/usr/bin/env python3
"""Rank local MCP configurations as promotion candidates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from config_inventory import collect_inventory, rank_sources


def public_record(record: dict[str, object]) -> dict[str, object]:
    return {
        key: record[key]
        for key in (
            "tool",
            "label",
            "config_present",
            "parse_error",
            "mcp_count",
            "shared_count",
            "tool_only_count",
            "retired",
            "missing_commands",
            "ready",
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    home = Path.home()
    hub = Path(os.environ.get("AGENT_HUB_ROOT", home / ".config" / "agent-hub"))
    ranked = rank_sources(collect_inventory(home, hub))
    recommended = next(
        (record["tool"] for record in ranked if record["ready"]),
        None,
    )
    report = {
        "recommended": recommended,
        "sources": [public_record(record) for record in ranked],
    }
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("Agent Sync MCP Sources")
    print("─" * 78)
    print(f"{'Source':<24} {'Ready':<7} {'MCP':<5} {'Shared':<7} {'Local':<6} Findings")
    for record in report["sources"]:
        findings = []
        if record["parse_error"]:
            findings.append("invalid config")
        if record["retired"]:
            findings.append("retired: " + ", ".join(record["retired"]))
        if record["missing_commands"]:
            findings.append("missing commands: " + ", ".join(record["missing_commands"]))
        print(
            f"{record['label']:<24} "
            f"{('yes' if record['ready'] else 'no'):<7} "
            f"{record['mcp_count']:<5} {record['shared_count']:<7} "
            f"{record['tool_only_count']:<6} "
            f"{'; '.join(findings) or '—'}"
        )
    if recommended:
        print(f"\nRecommended preview: agent-sync sync --from {recommended} --dry-run")
    else:
        print("\nNo source is ready for promotion. Run: agent-sync doctor")
    print("Promotion is always explicit; agent-sync never promotes by timestamp alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
