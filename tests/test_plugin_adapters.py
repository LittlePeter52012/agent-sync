from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.plugin_adapters as plugin_adapters
from scripts.plugin_adapters import discover_plugins


class PluginAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def completed(self, command, payload="", returncode=0):
        stdout = json.dumps(payload) if not isinstance(payload, str) else payload
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    def manifest(self, root: Path, name: str = "sample") -> Path:
        skill = root / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        return root

    def disabled_db(self, path: Path, disabled) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
            connection.execute(
                "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
                ("extensionsIdentifiers/disabled", json.dumps(disabled)),
            )
            connection.commit()
        finally:
            connection.close()

    def test_unknown_target_returns_uncertain_without_sources(self):
        result = discover_plugins("unknown-agent", self.home, frozenset({"sample"}))

        self.assertEqual(result.sources, ())
        self.assertTrue(any("unknown-agent" in item for item in result.uncertain))

    def test_adapter_exception_becomes_uncertain_and_keeps_other_targets_usable(self):
        def broken(*args, **kwargs):
            raise RuntimeError("boom")

        def healthy(target, home, declared_ids, runner):
            return plugin_adapters.PluginDiscovery()

        with patch.dict(
            plugin_adapters._ADAPTERS,
            {"broken": broken, "healthy": healthy},
            clear=False,
        ):
            first = discover_plugins("broken", self.home, frozenset({"sample"}))
            second = discover_plugins("healthy", self.home, frozenset({"sample"}))

        self.assertEqual(first.sources, ())
        self.assertIn("broken:plugin-state-uncertain", first.uncertain)
        self.assertEqual(second.uncertain, ())

    def test_empty_declared_ids_skips_product_probe(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            raise AssertionError("runner must not be called")

        result = discover_plugins("claude", self.home, frozenset(), runner=runner)

        self.assertEqual(result.sources, ())
        self.assertEqual(result.uncertain, ())
        self.assertEqual(calls, [])

    def test_claude_uses_enabled_declared_plugin_with_install_path(self):
        root = self.manifest(self.home / "claude-plugin")

        def runner(command, **kwargs):
            self.assertEqual(command, ["claude", "plugin", "list", "--json"])
            return self.completed(
                command,
                [{"id": "sample", "enabled": True, "installPath": str(root)}],
            )

        result = discover_plugins(
            "claude", self.home, frozenset({"sample"}), runner=runner
        )

        self.assertEqual([source.plugin_id for source in result.sources], ["sample"])
        self.assertEqual(result.sources[0].evidence, "claude-cli")
        self.assertEqual(result.uncertain, ())

    def test_claude_ignores_disabled_plugin(self):
        root = self.manifest(self.home / "claude-plugin")

        def runner(command, **kwargs):
            return self.completed(
                command,
                [{"id": "sample", "enabled": False, "installPath": str(root)}],
            )

        result = discover_plugins(
            "claude", self.home, frozenset({"sample"}), runner=runner
        )

        self.assertEqual(result.sources, ())
        self.assertEqual(result.uncertain, ())

    def test_claude_malformed_json_is_uncertain(self):
        def runner(command, **kwargs):
            return self.completed(command, "{")

        result = discover_plugins(
            "claude", self.home, frozenset({"sample"}), runner=runner
        )

        self.assertIn("claude:plugin-state-uncertain", result.uncertain)

    def test_claude_does_not_probe_unmapped_plugins(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return self.completed(command, [])

        discover_plugins("claude", self.home, frozenset(), runner=runner)
        self.assertEqual(calls, [])

    def test_gemini_uses_explicitly_enabled_declared_extension(self):
        root = self.manifest(self.home / "gemini-extension")

        def runner(command, **kwargs):
            self.assertEqual(
                command, ["gemini", "extensions", "list", "--output-format", "json"]
            )
            return self.completed(
                command,
                [{"name": "sample", "enabled": True, "path": str(root)}],
            )

        result = discover_plugins(
            "gemini", self.home, frozenset({"sample"}), runner=runner
        )

        self.assertEqual([source.plugin_id for source in result.sources], ["sample"])
        self.assertEqual(result.sources[0].evidence, "gemini-cli")

    def test_gemini_ignores_explicitly_disabled_extension(self):
        root = self.manifest(self.home / "gemini-extension")

        def runner(command, **kwargs):
            return self.completed(
                command,
                [{"name": "sample", "enabled": False, "path": str(root)}],
            )

        result = discover_plugins(
            "gemini", self.home, frozenset({"sample"}), runner=runner
        )
        self.assertEqual(result.sources, ())

    def test_gemini_blank_output_is_uncertain_for_declared_plugin(self):
        def runner(command, **kwargs):
            return self.completed(command, "")

        result = discover_plugins(
            "gemini", self.home, frozenset({"sample"}), runner=runner
        )
        self.assertIn("gemini:plugin-state-uncertain", result.uncertain)

    def test_gemini_missing_enabled_field_is_uncertain(self):
        root = self.manifest(self.home / "gemini-extension")

        def runner(command, **kwargs):
            return self.completed(command, [{"name": "sample", "path": str(root)}])

        result = discover_plugins(
            "gemini", self.home, frozenset({"sample"}), runner=runner
        )
        self.assertEqual(result.sources, ())
        self.assertIn("gemini:plugin-state-uncertain", result.uncertain)

    def test_opencode_normalizes_versioned_declared_plugin(self):
        config = self.home / ".config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"plugin": ["sample@latest"]}), encoding="utf-8")

        result = discover_plugins("opencode", self.home, frozenset({"sample"}))

        self.assertEqual([source.plugin_id for source in result.sources], ["sample"])

    def test_opencode_preserves_scoped_package_identity(self):
        config = self.home / ".config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"plugin": ["@scope/sample@1.2.3"]}), encoding="utf-8"
        )

        result = discover_plugins(
            "opencode", self.home, frozenset({"@scope/sample"})
        )

        self.assertEqual(
            [source.plugin_id for source in result.sources], ["@scope/sample"]
        )

    def test_opencode_ignores_provider_and_secret_bearing_mcp_fields(self):
        sentinel = "DO-NOT-READ-THIS-SECRET"
        config = self.home / ".config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "plugin": ["sample@latest"],
                    "provider": {"token": sentinel},
                    "mcp": {"sample": {"environment": {"TOKEN": sentinel}}},
                }
            ),
            encoding="utf-8",
        )

        result = discover_plugins("opencode", self.home, frozenset({"sample"}))

        self.assertNotIn(sentinel, repr(result))
        self.assertEqual([source.plugin_id for source in result.sources], ["sample"])

    def test_opencode_malformed_config_is_uncertain(self):
        config = self.home / ".config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text("{", encoding="utf-8")

        result = discover_plugins("opencode", self.home, frozenset({"sample"}))

        self.assertIn("opencode:plugin-state-uncertain", result.uncertain)

    def test_antigravity_app_and_cli_use_separate_plugin_roots(self):
        app = self.home / ".gemini" / "antigravity" / "plugins" / "app"
        cli = self.home / ".gemini" / "antigravity-cli" / "plugins" / "cli"
        app.mkdir(parents=True)
        cli.mkdir(parents=True)
        (app / "plugin.json").write_text(json.dumps({"name": "app-plugin"}))
        (cli / "plugin.json").write_text(json.dumps({"name": "cli-plugin"}))

        app_result = discover_plugins(
            "antigravity-app", self.home, frozenset({"app-plugin", "cli-plugin"})
        )
        cli_result = discover_plugins(
            "antigravity-cli", self.home, frozenset({"app-plugin", "cli-plugin"})
        )

        self.assertEqual([source.plugin_id for source in app_result.sources], ["app-plugin"])
        self.assertEqual([source.plugin_id for source in cli_result.sources], ["cli-plugin"])

    def test_antigravity_disabled_plugin_is_not_active(self):
        root = self.home / ".gemini" / "antigravity" / "plugins" / "sample"
        root.mkdir(parents=True)
        (root / "plugin.json").write_text(
            json.dumps({"name": "sample", "disabled": True}), encoding="utf-8"
        )

        result = discover_plugins(
            "antigravity-app", self.home, frozenset({"sample"})
        )
        self.assertEqual(result.sources, ())

    def test_antigravity_cache_without_plugin_manifest_is_not_active(self):
        (self.home / ".gemini" / "antigravity" / "plugins" / "sample").mkdir(
            parents=True
        )

        result = discover_plugins(
            "antigravity-app", self.home, frozenset({"sample"})
        )
        self.assertEqual(result.sources, ())

    def test_vscode_excludes_declared_disabled_extension(self):
        db = (
            self.home
            / "Library/Application Support/Code/User/globalStorage/state.vscdb"
        )
        self.disabled_db(db, [{"id": "vendor.sample"}])

        def runner(command, **kwargs):
            return self.completed(command, "vendor.sample@1.2.3\n")

        result = discover_plugins(
            "vscode", self.home, frozenset({"vendor.sample"}), runner=runner
        )
        self.assertEqual(result.sources, ())
        self.assertEqual(result.uncertain, ())

    def test_vscode_profile_does_not_inherit_default_plugin_state(self):
        profile_db = (
            self.home
            / "Library/Application Support/Code/User/profiles/research/globalStorage/state.vscdb"
        )
        self.disabled_db(profile_db, ["vendor.sample"])

        def runner(command, **kwargs):
            self.assertIn("research", command)
            return self.completed(command, "vendor.sample@1.2.3\n")

        result = discover_plugins(
            "vscode:research",
            self.home,
            frozenset({"vendor.sample"}),
            runner=runner,
        )
        self.assertEqual(result.sources, ())

    def test_vscode_missing_profile_state_is_uncertain(self):
        result = discover_plugins(
            "vscode:missing", self.home, frozenset({"vendor.sample"})
        )

        self.assertTrue(any("missing" in item for item in result.uncertain))

    def test_antigravity_ide_uses_its_own_cli_and_state_database(self):
        db = (
            self.home
            / "Library/Application Support/Antigravity IDE/User/globalStorage/state.vscdb"
        )
        self.disabled_db(db, [])

        def runner(command, **kwargs):
            self.assertIn("antigravity-ide", command[0])
            return self.completed(command, "vendor.ide@2.0.0\n")

        result = discover_plugins(
            "antigravity-ide",
            self.home,
            frozenset({"vendor.ide", "vendor.vscode"}),
            runner=runner,
        )

        self.assertEqual([source.plugin_id for source in result.sources], ["vendor.ide"])
        self.assertEqual(result.sources[0].evidence, "antigravity-ide-cli")

    def test_extension_state_database_error_is_uncertain(self):
        db = (
            self.home
            / "Library/Application Support/Code/User/globalStorage/state.vscdb"
        )
        db.parent.mkdir(parents=True)
        db.write_text("not a database", encoding="utf-8")

        def runner(command, **kwargs):
            return self.completed(command, "vendor.sample@1.2.3\n")

        result = discover_plugins(
            "vscode", self.home, frozenset({"vendor.sample"}), runner=runner
        )

        self.assertEqual(result.sources, ())
        self.assertIn("vscode:plugin-state-uncertain", result.uncertain)


if __name__ == "__main__":
    unittest.main()
