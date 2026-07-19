#!/usr/bin/env python3
"""Trace secret-free Agent configuration ownership."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from config_inventory import trace_mcp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="kind", required=True)
    mcp = subparsers.add_parser("mcp", help="trace one MCP server")
    mcp.add_argument("name")
    mcp.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    home = Path.home()
    hub = Path(os.environ.get("AGENT_HUB_ROOT", home / ".config" / "agent-hub"))
    report = trace_mcp(args.name, home, hub)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        locations = ", ".join(report["locations"]) or "none"
        print(f"MCP: {report['name']}")
        print(f"Hub status: {report['hub_status']}")
        print(f"Configured in: {locations}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
