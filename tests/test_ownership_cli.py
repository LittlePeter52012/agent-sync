from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "bin" / "agent-sync"


class OwnershipCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        (self.hub / "mcp").mkdir(parents=True)
        (self.hub / "policies").mkdir()
        (self.hub / "manifest.yaml").write_text(
            "version: 1\nskills:\n  - cloudflare\n",
            encoding="utf-8",
        )
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "cloudflare-local": {
                            "type": "http",
                            "url": "https://mcp.cloudflare.com/mcp?token=TOKEN_SHOULD_NOT_APPEAR",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.hub / "policies" / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "capability_ownership": {
                        "defaults": {"selection": "auto"},
                        "capabilities": {
                            "cloudflare": {
                                "hub": {
                                    "skills": ["cloudflare"],
                                    "mcp": ["cloudflare-local"],
                                },
                                "targets": {
                                    "cursor": {
                                        "official_plugins": ["cloudflare"],
                                        "selection": "auto",
                                    }
                                },
                                "data_boundary": {
                                    "hub": {
                                        "execution_location": "agent-managed",
                                        "data_destination": "provider-cloud",
                                    },
                                    "plugin:cloudflare": {
                                        "execution_location": "product-plugin",
                                        "data_destination": "provider-cloud",
                                    },
                                },
                            }
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        plugin = (
            self.home
            / ".cursor/plugins/cache/cursor-public/cloudflare/rev"
        )
        (plugin / "skills/cloudflare").mkdir(parents=True)
        (plugin / "skills/cloudflare/SKILL.md").write_text(
            "# Cloudflare\n",
            encoding="utf-8",
        )
        (plugin / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "cloudflare-docs": {
                            "type": "http",
                            "url": "https://docs.mcp.cloudflare.com/mcp",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        log = (
            self.home
            / "Library/Application Support/Cursor/logs/current/Cursor Plugins.log"
        )
        log.parent.mkdir(parents=True)
        log.write_text(
            "BackendMarketplaceClient: Adding enabled plugin: "
            "cloudflare from rev at undefined\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ENTRYPOINT), *args],
            cwd=REPO,
            env=os.environ
            | {
                "HOME": str(self.home),
                "AGENT_HUB_ROOT": str(self.hub),
                "AGENT_SYNC_HOME": str(REPO),
            },
            text=True,
            capture_output=True,
        )

    def test_ownership_command_reports_one_target_without_secrets(self):
        result = self.run_cli(
            "ownership",
            "cloudflare",
            "--target",
            "cursor",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["capability"], "cloudflare")
        self.assertEqual(payload["target"], "cursor")
        self.assertEqual(payload["effective_owner"], "plugin:cloudflare")
        self.assertEqual(payload["state"], "plugin-owned")
        self.assertEqual(payload["suppressed_skills"], ["cloudflare"])
        self.assertEqual(payload["suppressed_mcp"], ["cloudflare-local"])
        self.assertEqual(payload["active_plugin_evidence"], ["cursor-log"])
        self.assertEqual(payload["selection_source"], "explicit-target")
        self.assertEqual(payload["active_providers"], ["plugin:cloudflare"])
        self.assertEqual(payload["active_competitors"], [])
        self.assertEqual(payload["execution_location"], "product-plugin")
        self.assertEqual(payload["data_destination"], "provider-cloud")
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)
        self.assertNotIn(str(self.home), result.stdout)

    def test_ownership_command_lists_all_declared_capabilities_for_target(self):
        result = self.run_cli("ownership", "--target", "cursor", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"], "cursor")
        self.assertEqual(
            [item["capability"] for item in payload["capabilities"]],
            ["cloudflare"],
        )

    def test_ownership_command_renders_all_targets_as_text(self):
        result = self.run_cli("ownership")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("目标：cursor", result.stdout)
        self.assertIn(
            "cloudflare: plugin-owned (plugin:cloudflare, selection=auto)",
            result.stdout,
        )
        self.assertIn("目标：codex", result.stdout)
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)
        self.assertNotIn(str(self.home), result.stdout)

    def test_ownership_command_rejects_unknown_capability(self):
        result = self.run_cli(
            "ownership",
            "unknown",
            "--target",
            "cursor",
            "--json",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown capability", result.stderr.lower())
        self.assertNotIn(str(self.home), result.stderr)


if __name__ == "__main__":
    unittest.main()
