import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "scripts" / "config_inventory.py"
ENTRYPOINT = REPO / "bin" / "agent-sync"


def load_inventory_module():
    spec = importlib.util.spec_from_file_location("config_inventory", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ConfigInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        (self.hub / "mcp").mkdir(parents=True)
        (self.hub / "manifest.yaml").write_text("skills: []\n", encoding="utf-8")
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        (self.hub / "mcp" / "retired-servers.json").write_text(
            json.dumps({"retiredServers": ["retired-server"]}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_rank_sources_prefers_healthy_source_over_newer_broken_source(self):
        cursor = self.home / ".cursor"
        cursor.mkdir(parents=True)
        cursor_config = cursor / "mcp.json"
        cursor_config.write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        claude_config = self.home / ".claude.json"
        claude_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {"command": "/bin/sh"},
                        "broken": {"command": "definitely-missing-agent-sync-command"},
                    }
                }
            ),
            encoding="utf-8",
        )
        os.utime(cursor_config, (100, 100))
        os.utime(claude_config, (200, 200))

        module = load_inventory_module()
        ranked = module.rank_sources(module.collect_inventory(self.home, self.hub))

        self.assertEqual(ranked[0]["tool"], "cursor")
        self.assertTrue(ranked[0]["ready"])
        claude = next(record for record in ranked if record["tool"] == "claude")
        self.assertFalse(claude["ready"])
        self.assertEqual(claude["missing_commands"], ["broken"])

    def test_rank_sources_prefers_lower_hub_drift_over_newer_tool_only_entries(self):
        cursor = self.home / ".cursor"
        cursor.mkdir(parents=True)
        cursor_config = cursor / "mcp.json"
        cursor_config.write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        claude_config = self.home / ".claude.json"
        claude_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {"command": "/bin/sh"},
                        "tool-only": {"command": "/bin/sh"},
                    }
                }
            ),
            encoding="utf-8",
        )
        os.utime(cursor_config, (100, 100))
        os.utime(claude_config, (200, 200))

        module = load_inventory_module()
        ranked = module.rank_sources(module.collect_inventory(self.home, self.hub))

        self.assertEqual(ranked[0]["tool"], "cursor")
        self.assertEqual(ranked[0]["tool_only_count"], 0)

    def test_rank_sources_prefers_top_level_source_over_profile_replica(self):
        cursor = self.home / ".cursor"
        cursor.mkdir(parents=True)
        cursor_config = cursor / "mcp.json"
        cursor_config.write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        profile = (
            self.home
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "profiles"
            / "paper"
        )
        profile.mkdir(parents=True)
        profile_config = profile / "mcp.json"
        profile_config.write_text(
            json.dumps({"servers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        os.utime(cursor_config, (100, 100))
        os.utime(profile_config, (200, 200))

        module = load_inventory_module()
        ranked = module.rank_sources(module.collect_inventory(self.home, self.hub))

        self.assertEqual(ranked[0]["tool"], "cursor")

    def test_inventory_marks_retired_residue_unready(self):
        opencode = self.home / ".config" / "opencode"
        opencode.mkdir(parents=True)
        (opencode / "opencode.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "shared": {"type": "local", "command": ["/bin/sh"]},
                        "retired-server": {
                            "type": "remote",
                            "url": "https://example.invalid/mcp",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        module = load_inventory_module()
        record = next(
            item
            for item in module.collect_inventory(self.home, self.hub)
            if item["tool"] == "opencode"
        )

        self.assertEqual(record["retired"], ["retired-server"])
        self.assertFalse(record["ready"])

    def test_inventory_marks_non_object_json_invalid(self):
        cursor = self.home / ".cursor"
        cursor.mkdir(parents=True)
        (cursor / "mcp.json").write_text("[]\n", encoding="utf-8")

        module = load_inventory_module()
        record = next(
            item
            for item in module.collect_inventory(self.home, self.hub)
            if item["tool"] == "cursor"
        )

        self.assertTrue(record["parse_error"])
        self.assertFalse(record["ready"])

    def test_trace_reports_locations_without_configuration_values(self):
        cursor = self.home / ".cursor"
        cursor.mkdir(parents=True)
        secret = "TOKEN_SHOULD_NOT_APPEAR"
        (cursor / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "/bin/sh",
                            "env": {"TOKEN": secret},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        module = load_inventory_module()
        trace = module.trace_mcp("shared", self.home, self.hub)
        rendered = json.dumps(trace)

        self.assertEqual(trace["hub_status"], "shared")
        self.assertEqual(trace["locations"], ["cursor"])
        self.assertNotIn(secret, rendered)

    def test_inventory_ignores_disabled_codex_mcp(self):
        codex = self.home / ".codex"
        codex.mkdir(parents=True)
        (codex / "config.toml").write_text(
            '[mcp_servers.disabled]\n'
            'command = "definitely-missing-agent-sync-command"\n'
            "enabled = false\n"
            '[mcp_servers.enabled]\n'
            'command = "/bin/sh"\n'
            "enabled = true\n",
            encoding="utf-8",
        )

        module = load_inventory_module()
        record = next(
            item
            for item in module.collect_inventory(self.home, self.hub)
            if item["tool"] == "codex"
        )

        self.assertEqual(record["mcp_names"], ["enabled"])
        self.assertEqual(record["missing_commands"], [])
        self.assertTrue(record["ready"])

    def test_sources_and_trace_entrypoints_emit_safe_json(self):
        cursor = self.home / ".cursor"
        cursor.mkdir(parents=True)
        (cursor / "mcp.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
        }

        sources = subprocess.run(
            [str(ENTRYPOINT), "sources", "--json"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        trace = subprocess.run(
            [str(ENTRYPOINT), "trace", "mcp", "shared", "--json"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(json.loads(sources.stdout)["recommended"], "cursor")
        self.assertEqual(json.loads(trace.stdout)["hub_status"], "shared")


if __name__ == "__main__":
    unittest.main()
