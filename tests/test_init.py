import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "bin" / "agent-sync"


class InitHubTests(unittest.TestCase):
    def test_init_creates_an_inert_private_hub(self):
        """Catch init accidentally activating public demonstration resources."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hub = root / "hub"
            home.mkdir()
            env = os.environ | {
                "HOME": str(home),
                "AGENT_HUB_ROOT": str(hub),
            }

            result = subprocess.run(
                [str(ENTRYPOINT), "init"],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

            manifest = (hub / "manifest.yaml").read_text(encoding="utf-8")
            shared = json.loads(
                (hub / "mcp" / "shared-servers.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("skills: []", manifest)
            self.assertEqual(shared, {"mcpServers": {}})
            self.assertEqual(list((hub / "rules").glob("*.md")), [])
            self.assertFalse((hub / "policies" / "tool-scopes.json").exists())
            self.assertFalse((hub / "multica" / "desired-state.json").exists())
            self.assertTrue((hub / "README.md").is_file())
            self.assertIn("No shared capabilities are active", result.stdout)

            self.assertTrue(
                (REPO / "examples" / "skills" / "hello-sync" / "SKILL.md").is_file()
            )
            self.assertTrue(
                (REPO / "examples" / "mcp" / "shared-servers.json").is_file()
            )

    def test_initialized_hub_explains_both_adoption_paths(self):
        """Catch a new Hub that leaves users without a safe next command."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hub = root / "hub"
            home.mkdir()
            env = os.environ | {
                "HOME": str(home),
                "AGENT_HUB_ROOT": str(hub),
            }

            subprocess.run(
                [str(ENTRYPOINT), "init"],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

            guide = (hub / "README.md").read_text(encoding="utf-8")
            self.assertIn("agent-sync sources", guide)
            self.assertIn("agent-sync sync --from TOOL --dry-run", guide)
            self.assertIn("agent-sync all", guide)
            self.assertIn("agent-sync verify --strict --scope managed", guide)

    def test_init_refuses_to_overwrite_a_partial_hub(self):
        """Catch init replacing files in a non-empty unfinished Hub."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hub = root / "hub"
            home.mkdir()
            (hub / "mcp").mkdir(parents=True)
            existing = hub / "mcp" / "shared-servers.json"
            original = '{"mcpServers":{"private-local":{"command":"local"}}}\n'
            existing.write_text(original, encoding="utf-8")
            env = os.environ | {
                "HOME": str(home),
                "AGENT_HUB_ROOT": str(hub),
            }

            result = subprocess.run(
                [str(ENTRYPOINT), "init"],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), original)
            self.assertFalse((hub / "manifest.yaml").exists())


if __name__ == "__main__":
    unittest.main()
