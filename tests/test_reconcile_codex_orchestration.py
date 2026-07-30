import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RECONCILE = REPO / "scripts" / "reconcile-codex-orchestration.py"


class ReconcileCodexOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        (self.home / ".codex").mkdir(parents=True)
        (self.hub / "policies").mkdir(parents=True)
        (self.hub / "policies" / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "plugins": {
                        "codex": {"forbidden": ["omo@sisyphuslabs"]},
                    },
                    "orchestration": {
                        "codex": {
                            "agents": {
                                "enabled": True,
                                "max_concurrent_threads_per_session": 3,
                                "default_subagent_model": "gpt-5.6-terra",
                                "default_subagent_reasoning_effort": "low",
                            },
                            "forbidden_hook_prefixes": [
                                "omo@sisyphuslabs:",
                            ],
                            "remove_agent_keys": ["max_threads"],
                            "forbidden_tables": ["features.multi_agent_v2"],
                            "forbidden_marketplaces": ["sisyphuslabs"],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.config = self.home / ".codex" / "config.toml"
        self.config.write_text(
            'model = "gpt-5.6-sol"\n'
            'secret = "TOKEN_SHOULD_STAY_PRIVATE"\n'
            '\n[plugins."omo@sisyphuslabs"]\n'
            "enabled = true\n"
            "\n[agents]\n"
            "enabled = true\n"
            "max_concurrent_threads_per_session = 12\n"
            'default_subagent_model = "gpt-5.6-sol"\n'
            'default_subagent_reasoning_effort = "xhigh"\n'
            "max_threads = 1000\n"
            "\n[features.multi_agent_v2]\n"
            "enabled = false\n"
            "max_concurrent_threads_per_session = 1000\n"
            "\n[marketplaces.sisyphuslabs]\n"
            'source_type = "local"\n'
            '\n[hooks.state."omo@sisyphuslabs:hooks/ultrawork.json:user_prompt_submit:0:0"]\n'
            'trusted_hash = "sha256:stale"\n'
            '\n[hooks.state."/safe/hooks.json:stop:0:0"]\n'
            'trusted_hash = "sha256:safe"\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def run_script(self, *args):
        return subprocess.run(
            ["python3", str(RECONCILE), *args],
            cwd=REPO,
            env=os.environ
            | {
                "HOME": str(self.home),
                "AGENT_HUB_ROOT": str(self.hub),
            },
            text=True,
            capture_output=True,
        )

    def test_dry_run_reports_changes_without_mutating_or_leaking_secrets(self):
        before = self.config.read_bytes()

        result = self.run_script("--dry-run")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn("would update Codex agent defaults", result.stdout)
        self.assertIn("would remove forbidden Codex plugin state", result.stdout)
        self.assertIn("would remove forbidden Codex hook state", result.stdout)
        self.assertIn("would remove forbidden Codex orchestration table", result.stdout)
        self.assertIn("would remove forbidden Codex marketplace", result.stdout)
        self.assertNotIn("TOKEN_SHOULD_STAY_PRIVATE", result.stdout)

    def test_apply_is_minimal_and_idempotent(self):
        first = self.run_script()

        self.assertEqual(first.returncode, 0, first.stderr)
        text = self.config.read_text(encoding="utf-8")
        self.assertIn('secret = "TOKEN_SHOULD_STAY_PRIVATE"', text)
        self.assertNotIn('[plugins."omo@sisyphuslabs"]', text)
        self.assertNotIn("omo@sisyphuslabs:hooks/ultrawork.json", text)
        self.assertNotIn("max_threads = 1000", text)
        self.assertNotIn("[features.multi_agent_v2]", text)
        self.assertNotIn("[marketplaces.sisyphuslabs]", text)
        self.assertIn('[hooks.state."/safe/hooks.json:stop:0:0"]', text)
        self.assertIn("max_concurrent_threads_per_session = 3", text)
        self.assertIn('default_subagent_model = "gpt-5.6-terra"', text)
        self.assertIn('default_subagent_reasoning_effort = "low"', text)

        before = self.config.read_bytes()
        second = self.run_script()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn("already converged", second.stdout)

    def test_product_blank_line_formatting_is_semantically_idempotent(self):
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        text = self.config.read_text(encoding="utf-8")
        text = text.replace(
            "max_concurrent_threads_per_session = 3\n",
            "max_concurrent_threads_per_session = 3\n\n\n",
        )
        self.config.write_text(text, encoding="utf-8")
        before = self.config.read_bytes()

        dry_run = self.run_script("--dry-run")

        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn("already converged", dry_run.stdout)


if __name__ == "__main__":
    unittest.main()
