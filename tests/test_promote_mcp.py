import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PROMOTE = REPO / "scripts" / "promote-mcp.py"
PRUNE = REPO / "scripts" / "prune-retired-mcp.py"
MERGE = REPO / "scripts" / "merge-mcp.py"
MERGE_CODEX = REPO / "scripts" / "merge-mcp-codex.py"
SYNC_CLAUDE = REPO / "scripts" / "sync-mcp-claude.py"
SYNC_MCP = REPO / "scripts" / "sync-mcp.sh"


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

    def test_prune_removes_only_retired_names_from_json_and_codex(self):
        retired = self.hub / "mcp" / "retired-servers.json"
        retired.write_text(json.dumps({"retiredServers": ["old-shared"]}), encoding="utf-8")
        cursor = self.home / ".cursor" / "mcp.json"
        cursor.parent.mkdir(parents=True)
        cursor.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "old-shared": {"command": "old"},
                        "tool-only": {"command": "keep"},
                    }
                }
            ),
            encoding="utf-8",
        )
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text(
            '[mcp_servers.old-shared]\ncommand = "old"\n\n'
            '[mcp_servers.old-shared.env]\nTOKEN = "secret"\n\n'
            '[mcp_servers.tool-only]\ncommand = "keep"\n',
            encoding="utf-8",
        )

        for target in (cursor, codex):
            result = subprocess.run(
                ["python3", str(PRUNE), str(retired), str(target)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        cursor_servers = json.loads(cursor.read_text())["mcpServers"]
        self.assertNotIn("old-shared", cursor_servers)
        self.assertIn("tool-only", cursor_servers)
        codex_text = codex.read_text()
        self.assertNotIn("mcp_servers.old-shared", codex_text)
        self.assertIn("mcp_servers.tool-only", codex_text)
        self.assertNotIn("secret", codex_text)

    def test_json_merge_updates_shared_structure_but_preserves_local_secret(self):
        canonical = self.hub / "mcp" / "merge-source.json"
        canonical.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "new-command",
                            "args": ["--new"],
                            "env": {"TOKEN": "${TOKEN}"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        target = self.home / ".cursor" / "mcp.json"
        target.parent.mkdir(parents=True)
        target.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "old-command",
                            "args": ["--old"],
                            "env": {"TOKEN": "local-secret"},
                        },
                        "tool-only": {"command": "keep"},
                    }
                }
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            ["python3", str(MERGE), str(canonical), str(target)],
            env=os.environ | {"HOME": str(self.home)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        servers = json.loads(target.read_text())["mcpServers"]
        self.assertEqual(servers["shared"]["command"], "new-command")
        self.assertEqual(servers["shared"]["args"], ["--new"])
        self.assertEqual(servers["shared"]["env"]["TOKEN"], "local-secret")
        self.assertEqual(servers["tool-only"]["command"], "keep")

    def test_codex_merge_replaces_shared_block_and_preserves_env_and_tool_only(self):
        canonical = self.hub / "mcp" / "merge-codex.json"
        canonical.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "new-command",
                            "args": ["--new"],
                            "env": {"TOKEN": "${TOKEN}"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        target = self.home / ".codex" / "config.toml"
        target.parent.mkdir(parents=True)
        target.write_text(
            '[mcp_servers.shared]\ncommand = "old-command"\nargs = ["--old"]\n\n'
            '[mcp_servers.shared.env]\nTOKEN = "local-secret"\n\n'
            '[mcp_servers.tool-only]\ncommand = "keep"\n',
            encoding="utf-8",
        )

        result = subprocess.run(
            ["python3", str(MERGE_CODEX), str(canonical), str(target)],
            env=os.environ | {"HOME": str(self.home)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        text = target.read_text()
        self.assertIn('command = "new-command"', text)
        self.assertIn('args = ["--new"]', text)
        self.assertIn('TOKEN = "local-secret"', text)
        self.assertIn("[mcp_servers.tool-only]", text)
        self.assertNotIn("old-command", text)

    def test_claude_sync_replaces_existing_shared_server(self):
        canonical = self.hub / "mcp" / "merge-claude.json"
        canonical.write_text(
            json.dumps({"mcpServers": {"shared": {"command": "new-command"}}}),
            encoding="utf-8",
        )
        log = Path(self.tempdir.name) / "claude.log"
        fake_claude = Path(self.tempdir.name) / "claude"
        fake_claude.write_text(
            "#!/bin/sh\n"
            'if [ "$1 $2" = "mcp list" ]; then echo "shared: existing"; exit 0; fi\n'
            f'printf "%s\\n" "$*" >> "{log}"\n',
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)

        result = subprocess.run(
            ["python3", str(SYNC_CLAUDE), str(canonical)],
            env=os.environ | {"HOME": str(self.home), "CLAUDE_BIN": str(fake_claude)},
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text()
        self.assertIn("mcp remove --scope user shared", calls)
        self.assertIn("mcp add --scope user shared -- new-command", calls)

    def test_sync_mcp_prunes_retired_names_before_distribution(self):
        (self.hub / "mcp" / "retired-servers.json").write_text(
            json.dumps({"retiredServers": ["old-shared"]}), encoding="utf-8"
        )
        cursor = self.home / ".cursor" / "mcp.json"
        cursor.parent.mkdir(parents=True)
        cursor.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "old-shared": {"command": "old"},
                        "tool-only": {"command": "keep"},
                    }
                }
            ),
            encoding="utf-8",
        )
        fake_claude = Path(self.tempdir.name) / "claude"
        fake_claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_claude.chmod(0o755)

        result = subprocess.run(
            ["bash", str(SYNC_MCP)],
            env=os.environ
            | {
                "HOME": str(self.home),
                "AGENT_HUB_ROOT": str(self.hub),
                "CLAUDE_BIN": str(fake_claude),
            },
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        servers = json.loads(cursor.read_text())["mcpServers"]
        self.assertNotIn("old-shared", servers)
        self.assertIn("tool-only", servers)
        self.assertIn("shared", servers)


if __name__ == "__main__":
    unittest.main()
