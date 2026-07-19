import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional


REPO = Path(__file__).resolve().parents[1]
PROMOTE = REPO / "scripts" / "promote-mcp.py"
PRUNE = REPO / "scripts" / "prune-retired-mcp.py"
MERGE = REPO / "scripts" / "merge-mcp.py"
MERGE_CODEX = REPO / "scripts" / "merge-mcp-codex.py"
SYNC_CLAUDE = REPO / "scripts" / "sync-mcp-claude.py"
SYNC_MCP = REPO / "scripts" / "sync-mcp.sh"
ENTRYPOINT = REPO / "bin" / "agent-sync"
VERIFY = REPO / "scripts" / "verify-all.sh"


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

    def run_promote_source(
        self, source: str, *args: str
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        return subprocess.run(
            [
                "python3",
                str(PROMOTE),
                "--source",
                source,
                "--hub",
                str(self.hub),
                *args,
            ],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
        )

    def run_agent_sync(
        self, *args: str, hub: Optional[Path] = None
    ) -> subprocess.CompletedProcess[str]:
        selected_hub = hub or self.hub
        fake_claude = Path(self.tempdir.name) / "cli-claude"
        fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "path = Path(os.environ['HOME']) / '.claude.json'\n"
            "data = json.loads(path.read_text()) if path.exists() else {'mcpServers': {}}\n"
            "servers = data.setdefault('mcpServers', {})\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['mcp', 'list']:\n"
            "    [print(name + ': configured') for name in servers]\n"
            "elif args[:2] == ['mcp', 'remove']:\n"
            "    servers.pop(args[-1], None)\n"
            "elif args[:2] == ['mcp', 'add']:\n"
            "    if '--transport' in args:\n"
            "        name = args[args.index('--transport') + 2]\n"
            "    else:\n"
            "        name = args[args.index('--scope') + 2]\n"
            "    servers[name] = {'command': 'managed-by-fake-cli'}\n"
            "path.write_text(json.dumps(data))\n",
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)
        claude_config = self.home / ".claude.json"
        if not claude_config.exists():
            claude_config.parent.mkdir(parents=True, exist_ok=True)
            claude_config.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(selected_hub),
            "CLAUDE_BIN": str(fake_claude),
            "TOKEN": "local-test-token",
        }
        return subprocess.run(
            [str(ENTRYPOINT), *args],
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
            '[features]\nweb_search = true\n\n'
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
        self.assertIn("[features]", codex_text)
        self.assertIn("web_search = true", codex_text)
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

    def test_json_merge_replaces_stale_absolute_command_when_command_changes(self):
        canonical = self.hub / "mcp" / "merge-source.json"
        canonical.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "new-command",
                            "args": [],
                            "env": {"TOKEN": "${TOKEN}"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        target = self.home / ".config" / "opencode" / "opencode.json"
        target.parent.mkdir(parents=True)
        target.write_text(
            json.dumps(
                {
                    "plugin": [],
                    "model": "test",
                    "mcp": {
                        "shared": {
                            "type": "local",
                            "command": ["/usr/local/bin/old-command", "--old"],
                            "environment": {"TOKEN": "local-secret"},
                        }
                    },
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
        shared = json.loads(target.read_text())["mcp"]["shared"]
        self.assertEqual(shared["command"], ["new-command"])
        self.assertEqual(shared["environment"]["TOKEN"], "local-secret")

    def test_json_merge_preserves_absolute_path_when_command_identity_matches(self):
        canonical = self.hub / "mcp" / "merge-source.json"
        canonical.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "same-command",
                            "args": ["--new"],
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
                            "command": "/usr/local/bin/same-command",
                            "args": ["--old"],
                        }
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
        shared = json.loads(target.read_text())["mcpServers"]["shared"]
        self.assertEqual(shared["command"], "/usr/local/bin/same-command")
        self.assertEqual(shared["args"], ["--new"])

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
            '[features]\nweb_search = true\n\n'
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
        self.assertIn("[features]", text)
        self.assertIn("web_search = true", text)
        self.assertNotIn("old-command", text)

    def test_codex_merge_replaces_stale_absolute_command_when_command_changes(self):
        canonical = self.hub / "mcp" / "merge-codex.json"
        canonical.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "new-command",
                            "args": [],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        target = self.home / ".codex" / "config.toml"
        target.parent.mkdir(parents=True)
        target.write_text(
            '[mcp_servers.shared]\n'
            'command = "/usr/local/bin/old-command"\n'
            'args = ["--old"]\n',
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
        self.assertIn("args = []", text)
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

    def test_sync_from_runs_promotion_then_full_distribution(self):
        self.write_vscode_profile(
            "paper", {"shared": {"command": "new-command", "args": ["--new"]}}
        )

        result = self.run_agent_sync("sync", "--from", "vscode", "--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        canonical = json.loads(self.canonical.read_text())["mcpServers"]
        self.assertEqual(canonical["shared"]["command"], "new-command")
        cursor = json.loads((self.home / ".cursor" / "mcp.json").read_text())["mcpServers"]
        self.assertEqual(cursor["shared"]["command"], "new-command")
        self.assertIn("Agent Sync Doctor", result.stdout)

    def test_sync_from_bootstraps_minimal_hub(self):
        self.write_vscode_profile("paper", {"shared": {"command": "working-command"}})
        fresh_hub = Path(self.tempdir.name) / "fresh-hub"

        result = self.run_agent_sync(
            "sync", "--from", "vscode", "--yes", hub=fresh_hub
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((fresh_hub / "manifest.yaml").exists())
        self.assertIn("skills: []", (fresh_hub / "manifest.yaml").read_text())
        self.assertFalse((fresh_hub / "skills" / "hello-sync").exists())
        servers = json.loads((fresh_hub / "mcp" / "shared-servers.json").read_text())
        self.assertIn("shared", servers["mcpServers"])

    def test_sync_dry_run_does_not_distribute_or_change_hub(self):
        self.write_vscode_profile("paper", {"new-server": {"command": "new-command"}})
        before = self.canonical.read_bytes()

        result = self.run_agent_sync("sync", "--from", "vscode", "--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.canonical.read_bytes(), before)
        self.assertFalse((self.home / ".cursor" / "mcp.json").exists())

    def test_unknown_sync_source_fails_without_writing(self):
        before = self.canonical.read_bytes()

        result = self.run_agent_sync("sync", "--from", "unknown", "--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.canonical.read_bytes(), before)

    def test_codex_can_be_used_as_source_with_system_python(self):
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            '[mcp_servers.shared]\ncommand = "codex-command"\nargs = ["--codex"]\n\n'
            '[mcp_servers.shared.env]\nTOKEN = "local-secret"\n',
            encoding="utf-8",
        )

        result = self.run_promote_source("codex", "--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        shared = json.loads(self.canonical.read_text())["mcpServers"]["shared"]
        self.assertEqual(shared["command"], "codex-command")
        self.assertEqual(shared["args"], ["--codex"])
        self.assertEqual(shared["env"]["TOKEN"], "${TOKEN}")

    def test_disabled_json_source_mcp_is_not_promoted(self):
        antigravity = self.home / ".gemini" / "config"
        antigravity.mkdir(parents=True)
        (antigravity / "mcp_config.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "enabled": {"command": "enabled-command"},
                        "disabled": {
                            "command": "disabled-command",
                            "disabled": True,
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_promote_source("antigravity", "--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        servers = json.loads(self.canonical.read_text())["mcpServers"]
        self.assertIn("enabled", servers)
        self.assertNotIn("disabled", servers)

    def test_disabled_codex_source_mcp_is_not_promoted(self):
        codex = self.home / ".codex"
        codex.mkdir(parents=True)
        (codex / "config.toml").write_text(
            '[mcp_servers.enabled]\n'
            'command = "enabled-command"\n'
            "enabled = true\n\n"
            '[mcp_servers.disabled]\n'
            'command = "disabled-command"\n'
            "enabled = false\n",
            encoding="utf-8",
        )

        result = self.run_promote_source("codex", "--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        servers = json.loads(self.canonical.read_text())["mcpServers"]
        self.assertIn("enabled", servers)
        self.assertNotIn("disabled", servers)

    def test_sync_uses_existing_hub_as_source_of_truth(self):
        result = self.run_agent_sync("sync")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cursor = json.loads((self.home / ".cursor" / "mcp.json").read_text())["mcpServers"]
        self.assertIn("shared", cursor)
        self.assertIn("retire-me", cursor)

    def test_verify_fails_when_target_file_exists_but_shared_name_is_missing(self):
        json_targets = [
            (self.home / ".gemini" / "config" / "mcp_config.json", "mcpServers", True),
            (self.home / ".cursor" / "mcp.json", "mcpServers", False),
            (self.home / ".claude.json", "mcpServers", True),
            (
                self.home
                / "Library"
                / "Application Support"
                / "Code"
                / "User"
                / "mcp.json",
                "servers",
                True,
            ),
            (self.home / ".config" / "opencode" / "opencode.json", "mcp", True),
        ]
        for path, key, complete in json_targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            servers = {
                "shared": {"command": "shared-mcp"},
                "retire-me": {"command": "retired-command"},
            } if complete else {}
            path.write_text(json.dumps({key: servers}), encoding="utf-8")
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text(
            '[mcp_servers.shared]\ncommand = "shared-mcp"\n\n'
            '[mcp_servers.retire-me]\ncommand = "retired-command"\n',
            encoding="utf-8",
        )

        result = subprocess.run(
            ["bash", str(VERIFY)],
            env=os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)},
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cursor shared MCP 0/2", result.stdout)


if __name__ == "__main__":
    unittest.main()
