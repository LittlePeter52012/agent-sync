from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "bin" / "agent-sync"
DOCTOR = REPO / "scripts" / "agent-doctor.py"


class VerifyScopeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        self.home.mkdir()
        (self.hub / "mcp").mkdir(parents=True)
        (self.hub / "manifest.yaml").write_text(
            "skills: []\n",
            encoding="utf-8",
        )
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps({"mcpServers": {}}),
            encoding="utf-8",
        )
        (self.home / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "broken-tool-only": {
                            "command": "definitely-missing-tool-only-command",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_RUNTIME_TIMEOUT": "1",
            "HERMES_HOME": str(root / "hermes"),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }
        self.env.pop("CLAUDE_BIN", None)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_entrypoint(self, *args):
        return subprocess.run(
            [str(ENTRYPOINT), *args],
            cwd=REPO,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def run_doctor(self, *args):
        return subprocess.run(
            ["python3", str(DOCTOR), "--json", *args],
            cwd=REPO,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def exclude_shared_from_targets(self, *managed_targets: str) -> None:
        targets = {
            "codex",
            "claude",
            "cursor",
            "gemini",
            "gemini-global",
            "antigravity-app",
            "antigravity-cli",
            "antigravity-ide",
            "opencode",
            "vscode",
            "vscode:paper",
        }
        policy = self.hub / "policies" / "tool-scopes.json"
        policy.parent.mkdir(exist_ok=True)
        policy.write_text(
            json.dumps(
                {
                    "distribution": {
                        target: {"exclude_mcp": ["shared"]}
                        for target in sorted(targets - set(managed_targets))
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_managed_scope_ignores_tool_only_failure(self):
        result = self.run_entrypoint(
            "verify",
            "--strict",
            "--scope",
            "managed",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("broken-tool-only", result.stdout)

    def test_all_scope_fails_tool_only_failure(self):
        result = self.run_entrypoint("verify", "--strict")
        self.assertEqual(result.returncode, 1)

    def test_explicit_all_scope_fails_tool_only_failure(self):
        result = self.run_entrypoint(
            "verify",
            "--strict",
            "--scope",
            "all",
        )
        self.assertEqual(result.returncode, 1)

    def test_verify_scope_requires_exact_strict_form(self):
        result = self.run_entrypoint(
            "verify",
            "--scope",
            "managed",
            "--strict",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown verify option", result.stderr)

    def test_managed_scope_fails_shared_coverage_drift(self):
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps(
                {"mcpServers": {"shared": {"command": "/bin/echo"}}}
            ),
            encoding="utf-8",
        )
        result = self.run_doctor("--strict", "--scope", "managed")
        self.assertEqual(result.returncode, 1)
        self.assertIn("shared", result.stdout)

    def test_managed_scope_fails_malformed_hub_policy(self):
        (self.hub / "policies").mkdir(exist_ok=True)
        (self.hub / "policies" / "tool-scopes.json").write_text(
            '{"distribution":',
            encoding="utf-8",
        )
        result = self.run_doctor("--strict", "--scope", "managed")
        self.assertEqual(result.returncode, 1)

    def test_managed_scope_ignores_excluded_profile_same_name_executable(self):
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps(
                {"mcpServers": {"shared": {"command": "/bin/sh"}}}
            ),
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
        (profile / "mcp.json").write_text(
            json.dumps(
                {
                    "servers": {
                        "shared": {
                            "command": "definitely-missing-profile-command"
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.exclude_shared_from_targets()

        result = self.run_entrypoint(
            "doctor",
            "--json",
            "--strict",
            "--scope",
            "managed",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finding = next(
            item
            for item in json.loads(result.stdout)["findings"]
            if "VS Code profile paper/shared" in item["message"]
        )
        self.assertEqual(finding["scope"], "local")
        self.assertEqual(finding["owner"], "vscode:paper")

    def test_managed_scope_ignores_plugin_owned_same_name_executable(self):
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps(
                {"mcpServers": {"shared": {"command": "/bin/sh"}}}
            ),
            encoding="utf-8",
        )
        cursor = self.home / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "definitely-missing-cursor-command"
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        plugin = cursor / "plugins" / "local" / "owner"
        plugin.mkdir(parents=True)
        (plugin / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"shared": {"command": "/bin/sh"}}}
            ),
            encoding="utf-8",
        )
        self.exclude_shared_from_targets("cursor")

        result = self.run_entrypoint(
            "doctor",
            "--json",
            "--strict",
            "--scope",
            "managed",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        finding = next(
            item
            for item in report["findings"]
            if "Cursor/shared" in item["message"]
        )
        self.assertEqual(finding["scope"], "local")
        self.assertEqual(finding["owner"], "cursor")
        self.assertFalse(
            any("MCP name collision" in item["message"] for item in report["findings"])
        )

    def test_managed_scope_fails_retired_mcp_residue(self):
        (self.hub / "mcp" / "retired-servers.json").write_text(
            json.dumps({"retiredServers": ["retired"]}),
            encoding="utf-8",
        )
        (self.home / ".claude.json").write_text(
            json.dumps(
                {"mcpServers": {"retired": {"command": "/bin/sh"}}}
            ),
            encoding="utf-8",
        )
        result = self.run_doctor("--strict", "--scope", "managed")
        self.assertEqual(result.returncode, 1)

    def test_sync_stays_structural_while_all_runs_managed_runtime_gate(self):
        cache = self.home / ".cache" / "agent-sync"
        cache.mkdir(parents=True)
        (cache / "last-update-check").write_text(
            f"{int(time.time())}\n",
            encoding="utf-8",
        )
        fake_bin = Path(self.tempdir.name) / "bin"
        fake_bin.mkdir()
        probe_log = Path(self.tempdir.name) / "product-cli.log"
        opencode = fake_bin / "opencode"
        opencode.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$FAKE_PRODUCT_CLI_LOG"\n'
            'printf "x broken-product-runtime failed\\n"\n'
            "exit 1\n",
            encoding="utf-8",
        )
        opencode.chmod(0o755)
        self.env.update(
            {
                "AGENT_SYNC_RUNTIME_TIMEOUT_OPENCODE": "10",
                "FAKE_PRODUCT_CLI_LOG": str(probe_log),
                "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            }
        )

        sync = self.run_entrypoint("sync")

        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        self.assertFalse(probe_log.exists(), sync.stdout + sync.stderr)

        full = self.run_entrypoint("all")

        self.assertEqual(full.returncode, 0, full.stdout + full.stderr)
        self.assertTrue(probe_log.exists(), full.stdout + full.stderr)
        calls = probe_log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(calls)
        self.assertEqual(set(calls), {"mcp list"})
        self.assertIn("broken-product-runtime", full.stdout)

    def test_full_sync_fixture_ignores_ambient_external_overrides(self):
        with tempfile.TemporaryDirectory() as ambient_root:
            ambient = Path(ambient_root)
            hermes_home = ambient / "hermes"
            hermes_home.mkdir()
            hermes_config = hermes_home / "config.yaml"
            original = b"skills:\n  external_dirs: []\n"
            hermes_config.write_bytes(original)

            claude_log = ambient / "claude.log"
            claude = ambient / "claude"
            claude.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$AMBIENT_CLAUDE_LOG"\n',
                encoding="utf-8",
            )
            claude.chmod(0o755)
            env = os.environ | {
                "AMBIENT_CLAUDE_LOG": str(claude_log),
                "CLAUDE_BIN": str(claude),
                "HERMES_HOME": str(hermes_home),
            }

            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "unittest",
                    (
                        "tests.test_verify_scopes.VerifyScopeTests."
                        "test_sync_stays_structural_while_all_runs_managed_runtime_gate"
                    ),
                    "-v",
                ],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stdout + result.stderr,
            )
            self.assertEqual(
                hermes_config.read_bytes(),
                original,
            )
            self.assertFalse(claude_log.exists())


if __name__ == "__main__":
    unittest.main()
