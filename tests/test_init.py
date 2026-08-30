import json
import os
import stat
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
            self.assertEqual(stat.S_IMODE(hub.stat().st_mode), 0o700)
            for path in hub.rglob("*"):
                self.assertEqual(
                    stat.S_IMODE(path.stat().st_mode) & 0o077,
                    0,
                    str(path),
                )

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

    def test_init_refuses_a_symlink_hub_without_touching_its_target(self):
        """Catch symlink traversal bypassing the non-empty Hub guard."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            target = root / "private-hub"
            hub = root / "hub-link"
            home.mkdir()
            (target / "mcp").mkdir(parents=True)
            existing = target / "mcp" / "shared-servers.json"
            original = '{"mcpServers":{"private-local":{"command":"local"}}}\n'
            existing.write_text(original, encoding="utf-8")
            hub.symlink_to(target, target_is_directory=True)
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
            self.assertFalse((target / "manifest.yaml").exists())

    def test_init_refuses_a_manifest_only_partial_hub(self):
        """Catch an interrupted init being reported as a complete Hub."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hub = root / "hub"
            home.mkdir()
            hub.mkdir()
            manifest = hub / "manifest.yaml"
            original = "version: 1\nskills: []\n"
            manifest.write_text(original, encoding="utf-8")
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
            self.assertEqual(manifest.read_text(encoding="utf-8"), original)
            self.assertFalse((hub / "mcp" / "shared-servers.json").exists())

    def test_init_is_a_noop_for_a_complete_existing_hub(self):
        """Catch structure validation accidentally rejecting a valid Hub."""
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
            sentinel = hub / "skills" / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")

            result = subprocess.run(
                [str(ENTRYPOINT), "init"],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Hub already exists", result.stdout)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
