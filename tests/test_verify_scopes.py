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
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }

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


if __name__ == "__main__":
    unittest.main()
