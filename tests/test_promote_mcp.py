import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PROMOTE = REPO / "scripts" / "promote-mcp.py"


class PromoteMcpTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        (self.hub / "mcp").mkdir(parents=True)
        (self.hub / "manifest.yaml").write_text("version: 1\nskills: []\n", encoding="utf-8")
        self.canonical = self.hub / "mcp" / "shared-servers.json"
        self.canonical.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "old-command",
                            "args": ["--old"],
                            "env": {"TOKEN": "${TOKEN}"},
                        },
                        "retire-me": {"command": "retired-command"},
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def write_vscode_profile(self, profile_id: str, servers: dict) -> Path:
        profile = (
            self.home
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "profiles"
            / profile_id
        )
        profile.mkdir(parents=True)
        path = profile / "mcp.json"
        path.write_text(json.dumps({"servers": servers}, indent=2) + "\n", encoding="utf-8")
        return path

    def run_promote(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        return subprocess.run(
            ["python3", str(PROMOTE), "--source", "vscode", "--hub", str(self.hub), *args],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_vscode_dry_run_reports_changes_without_writing(self):
        self.write_vscode_profile(
            "paper",
            {
                "shared": {
                    "command": "new-command",
                    "args": ["--new"],
                    "env": {"TOKEN": "SECRET_MUST_NOT_LEAK"},
                },
                "new-server": {"command": "new-server-command"},
            },
        )
        before = self.canonical.read_bytes()

        result = self.run_promote("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.canonical.read_bytes(), before)
        self.assertIn("new-server", result.stdout)
        self.assertIn("retire-me", result.stdout)
        self.assertNotIn("SECRET_MUST_NOT_LEAK", result.stdout + result.stderr)
        self.assertFalse((self.hub / "mcp" / "retired-servers.json").exists())
        self.assertFalse((self.hub / ".sync-backups").exists())

    def test_vscode_profile_ambiguity_requires_explicit_id(self):
        self.write_vscode_profile("paper", {"shared": {"command": "paper-command"}})
        self.write_vscode_profile("coding", {"shared": {"command": "coding-command"}})
        before = self.canonical.read_bytes()

        result = self.run_promote("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vscode:paper", result.stderr)
        self.assertIn("vscode:coding", result.stderr)
        self.assertEqual(self.canonical.read_bytes(), before)

    def test_empty_source_cannot_replace_hub(self):
        self.write_vscode_profile("paper", {})
        before = self.canonical.read_bytes()

        result = self.run_promote("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty", result.stderr.lower())
        self.assertEqual(self.canonical.read_bytes(), before)

    def test_promotion_sanitizes_secrets_preserves_placeholders_and_tracks_retired(self):
        self.write_vscode_profile(
            "paper",
            {
                "shared": {
                    "command": "new-command",
                    "args": ["--new"],
                    "env": {"TOKEN": "SECRET_MUST_NOT_LEAK"},
                },
                "new-server": {
                    "type": "http",
                    "url": "https://example.invalid/mcp",
                    "headers": {"Authorization": "SECRET_MUST_NOT_LEAK"},
                },
            },
        )

        result = self.run_promote("--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        canonical_text = self.canonical.read_text(encoding="utf-8")
        canonical = json.loads(canonical_text)["mcpServers"]
        self.assertEqual(canonical["shared"]["command"], "new-command")
        self.assertEqual(canonical["shared"]["env"]["TOKEN"], "${TOKEN}")
        self.assertEqual(
            canonical["new-server"]["headers"]["Authorization"],
            "${AGENT_SYNC_NEW_SERVER_AUTHORIZATION}",
        )
        self.assertNotIn("SECRET_MUST_NOT_LEAK", canonical_text + result.stdout + result.stderr)
        retired = json.loads((self.hub / "mcp" / "retired-servers.json").read_text())
        self.assertEqual(retired, {"retiredServers": ["retire-me"]})
        backups = list((self.hub / ".sync-backups").glob("*/mcp/shared-servers.json"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
