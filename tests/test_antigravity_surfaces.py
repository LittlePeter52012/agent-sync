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
LIB = REPO / "scripts" / "lib.sh"


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
            json.dumps({"retiredServers": ["todoist"]}),
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
        gemini_settings = self.home / ".gemini" / "settings.json"
        gemini_settings.parent.mkdir(parents=True)
        gemini_settings.write_text(
            json.dumps(
                {
                    "hooks": {"PreCompress": [{"command": "/bin/sh"}]},
                    "mcpServers": {
                        "local-only": {"command": "/bin/sh"},
                        "shared": {
                            "command": "/bin/sh",
                            "env": {"CANONICAL_SETTING": "kept"},
                        },
                        "SHARED": {
                            "command": "/bin/sh",
                            "env": {"ALIAS_ONLY_SETTING": "kept"},
                        },
                        "todoist": {"command": "/bin/sh"},
                    },
                }
            ),
            encoding="utf-8",
        )
        gemini_settings.chmod(0o644)
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

        gemini_after = json.loads(gemini_settings.read_text(encoding="utf-8"))
        self.assertEqual(
            set(gemini_after["mcpServers"]),
            {"shared", "local-only"},
        )
        self.assertEqual(
            gemini_after["mcpServers"]["shared"]["env"]["CANONICAL_SETTING"],
            "kept",
        )
        self.assertEqual(
            gemini_after["mcpServers"]["shared"]["env"]["ALIAS_ONLY_SETTING"],
            "kept",
        )
        self.assertEqual(
            gemini_after["hooks"],
            {"PreCompress": [{"command": "/bin/sh"}]},
        )
        self.assertEqual(gemini_settings.stat().st_mode & 0o777, 0o644)
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
        (self.home / ".claude.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )

        listed = subprocess.run(
            ["bash", str(LIST_SYNC)],
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        for label in (
            "Gemini CLI",
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

    def test_verify_rejects_skill_copy_that_is_not_hub_managed(self):
        subprocess.run(
            ["bash", str(SYNC_SKILLS)],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )
        stale = self.home / ".codex" / "skills" / "shared-skill"
        stale.unlink()
        stale.mkdir()
        (stale / "SKILL.md").write_text("# stale copy\n", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(VERIFY)],
            env=self.env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hub-managed", result.stdout)

    def test_hub_managed_check_accepts_symlinked_hub_root(self):
        linked_hub = Path(self.tempdir.name) / "linked-hub"
        linked_hub.symlink_to(self.hub, target_is_directory=True)
        destination = self.home / ".codex" / "skills" / "shared-skill"
        destination.parent.mkdir(parents=True)
        destination.symlink_to(linked_hub / "skills" / "shared-skill")

        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; skill_is_hub_managed "$2" "$3"',
                "bash",
                str(LIB),
                str(destination),
                str(linked_hub / "skills" / "shared-skill"),
            ],
            env=self.env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_force_sync_backs_up_same_named_skill_per_target(self):
        stale_roots = (
            self.home / ".gemini" / "config" / "skills",
            self.home / ".gemini" / "antigravity" / "skills",
        )
        for index, root in enumerate(stale_roots):
            skill = root / "shared-skill"
            skill.mkdir(parents=True)
            (skill / "marker.txt").write_text(
                f"copy-{index}\n",
                encoding="utf-8",
            )

        subprocess.run(
            ["bash", str(SYNC_SKILLS), "--force"],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

        markers = sorted(
            path.read_text(encoding="utf-8").strip()
            for path in (self.hub / ".sync-backups").rglob("marker.txt")
        )
        backup_roots = {
            path.parents[1].name
            for path in (self.hub / ".sync-backups").rglob("marker.txt")
        }
        self.assertEqual(markers, ["copy-0", "copy-1"])
        self.assertEqual(len(backup_roots), 2)


if __name__ == "__main__":
    unittest.main()
