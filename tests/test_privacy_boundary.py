import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "bin" / "agent-sync"
AUDIT = REPO / "scripts" / "privacy-audit.sh"
DESIGN = REPO / "docs" / "superpowers" / "specs" / "2026-07-13-source-promotion-sync-design.md"


class PrivacyBoundaryTests(unittest.TestCase):
    def test_push_refuses_raw_token_before_committing_hub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hub = root / "hub"
            (hub / "mcp").mkdir(parents=True)
            (hub / "manifest.yaml").write_text("version: 1\nskills: []\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(hub)], check=True)
            subprocess.run(["git", "-C", str(hub), "config", "user.name", "Test User"], check=True)
            subprocess.run(
                ["git", "-C", str(hub), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(hub), "add", "manifest.yaml"], check=True)
            subprocess.run(
                ["git", "-C", str(hub), "commit", "-q", "-m", "initial"], check=True
            )
            secret_file = hub / "mcp" / "shared-servers.json"
            secret_file.write_text(
                '{"mcpServers":{"unsafe":{"env":{"TOKEN":"sk-aaaaaaaaaaaaaaaaaaaaaaaa"}}}}\n',
                encoding="utf-8",
            )
            before = subprocess.run(
                ["git", "-C", str(hub), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()

            result = subprocess.run(
                [str(ENTRYPOINT), "push", "-m", "unsafe"],
                env=os.environ
                | {
                    "HOME": str(home),
                    "AGENT_HUB_ROOT": str(hub),
                    "AGENT_SYNC_HOME": str(REPO),
                },
                cwd=REPO,
                text=True,
                capture_output=True,
            )

            after = subprocess.run(
                ["git", "-C", str(hub), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(after, before)
            self.assertIn("privacy", (result.stdout + result.stderr).lower())

    def test_audit_discovers_repository_names_instead_of_hardcoding_owner(self):
        text = AUDIT.read_text(encoding="utf-8")

        self.assertNotIn("LittlePeter52012/agent-sync", text)
        self.assertNotIn("LittlePeter52012/agent-hub", text)

    def test_design_declares_public_generic_diagram_and_private_hub_data(self):
        text = DESIGN.read_text(encoding="utf-8")

        self.assertIn("公开 README", text)
        self.assertIn("通用流程图", text)
        self.assertIn("私人 Hub", text)
        self.assertNotIn("流程图是独立的私人或本地文件", text)


if __name__ == "__main__":
    unittest.main()
