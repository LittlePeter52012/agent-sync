import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DOCTOR = REPO / "scripts" / "agent-doctor.py"
ENTRYPOINT = REPO / "bin" / "agent-sync"


class AgentDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home"
        self.hub = Path(self.tempdir.name) / "hub"
        (self.hub / "skills" / "example" ).mkdir(parents=True)
        (self.hub / "skills" / "example" / "SKILL.md").write_text("---\nname: example\n---\n")
        (self.hub / "manifest.yaml").write_text("skills:\n  - example\n")
        (self.hub / "mcp").mkdir()
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "shared-mcp"}}})
        )
        (self.home / ".codex" / "skills" / "example").mkdir(parents=True)
        (self.home / ".codex" / "skills" / "example" / "SKILL.md").write_text("skill")
        (self.home / ".codex" / "config.toml").write_text(
            'model = "safe-model"\n[mcp_servers.shared]\ncommand = "shared-mcp"\n'
            '[mcp_servers.shared.env]\nTOKEN = "TOKEN_SHOULD_NOT_APPEAR"\n'
        )
        (self.home / ".claude" / "skills" / "example").mkdir(parents=True)
        (self.home / ".claude" / "skills" / "example" / "SKILL.md").write_text("skill")

    def tearDown(self):
        self.tempdir.cleanup()

    def run_doctor(self):
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        return subprocess.run(
            ["python3", str(DOCTOR), "--json"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_json_reports_synced_codex_without_secret_values(self):
        result = self.run_doctor()
        report = json.loads(result.stdout)

        codex = next(agent for agent in report["agents"] if agent["name"] == "Codex / ChatGPT")
        self.assertEqual(codex["skills"], {"configured": 1, "expected": 1})
        self.assertEqual(codex["mcp"], {"configured": 1, "expected": 1})
        self.assertIn("safe-model", codex["models"])
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_json_flags_missing_supported_agent_configuration(self):
        report = json.loads(self.run_doctor().stdout)
        cursor = next(agent for agent in report["agents"] if agent["name"] == "Cursor")

        self.assertFalse(cursor["config_present"])
        self.assertTrue(any("Cursor" in finding["message"] for finding in report["findings"]))

    def test_entrypoint_dispatches_doctor(self):
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        result = subprocess.run(
            [str(ENTRYPOINT), "doctor", "--json"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(json.loads(result.stdout)["agents"][0]["name"], "Codex / ChatGPT")


if __name__ == "__main__":
    unittest.main()
