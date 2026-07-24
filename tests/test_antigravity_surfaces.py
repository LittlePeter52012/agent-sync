import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SYNC_SKILLS = REPO / "scripts" / "sync-skills.sh"
SYNC_MCP = REPO / "scripts" / "sync-mcp.sh"
LIST_SYNC = REPO / "scripts" / "list-sync.sh"
VERIFY = REPO / "scripts" / "verify-all.sh"


class AntigravitySurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home"
        self.hub = root / "hub"

        skill = self.hub / "skills" / "shared-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Shared\n", encoding="utf-8")
        (self.hub / "manifest.yaml").write_text(
            "skills:\n  - shared-skill\n",
            encoding="utf-8",
        )
        (self.hub / "mcp").mkdir()
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        (self.hub / "mcp" / "retired-servers.json").write_text(
            json.dumps({"retiredServers": []}),
            encoding="utf-8",
        )

        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_claude = fake_bin / "claude"
        fake_claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_claude.chmod(0o755)
        self.env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
            "CLAUDE_BIN": str(fake_claude),
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_skills_sync_reaches_all_antigravity_roots(self):
        subprocess.run(
            ["bash", str(SYNC_SKILLS)],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

        for relative in (
            ".gemini/config/skills",
            ".gemini/antigravity/skills",
            ".gemini/antigravity-cli/skills",
            ".gemini/antigravity-ide/skills",
        ):
            target = self.home / relative / "shared-skill"
            self.assertTrue(target.is_symlink(), relative)
            self.assertEqual(
                target.resolve(),
                (self.hub / "skills" / "shared-skill").resolve(),
            )

    def test_mcp_sync_preserves_local_servers_and_prepares_caches(self):
        roots = (
            ".gemini/config",
            ".gemini/antigravity",
            ".gemini/antigravity-cli",
            ".gemini/antigravity-ide",
        )
        for relative in roots:
            root = self.home / relative
            root.mkdir(parents=True)
            (root / "mcp_config.json").write_text(
                json.dumps(
                    {"mcpServers": {"local-only": {"command": "/bin/sh"}}}
                ),
                encoding="utf-8",
            )

        subprocess.run(
            ["bash", str(SYNC_MCP)],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

        for relative in roots:
            root = self.home / relative
            names = json.loads(
                (root / "mcp_config.json").read_text(encoding="utf-8")
            )["mcpServers"]
            self.assertEqual(set(names), {"shared", "local-only"})
        for relative in roots[1:]:
            self.assertTrue((self.home / relative / "mcp" / "shared").is_dir())
            self.assertTrue((self.home / relative / "mcp" / "local-only").is_dir())

    def test_list_and_verify_name_each_antigravity_surface(self):
        subprocess.run(
            ["bash", str(SYNC_SKILLS)],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["bash", str(SYNC_MCP)],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

        listed = subprocess.run(
            ["bash", str(LIST_SYNC)],
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        for label in (
            "Gemini global",
            "Antigravity App",
            "Antigravity CLI",
            "Antigravity IDE",
        ):
            self.assertIn(label, listed)

        verified = subprocess.run(
            ["bash", str(VERIFY)],
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(
            verified.returncode,
            0,
            verified.stdout + verified.stderr,
        )

        (self.home / ".gemini/antigravity-cli/mcp_config.json").write_text(
            '{"mcpServers": {}}\n',
            encoding="utf-8",
        )
        failed = subprocess.run(
            ["bash", str(VERIFY)],
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("Antigravity CLI shared MCP 0/1", failed.stdout)


if __name__ == "__main__":
    unittest.main()
