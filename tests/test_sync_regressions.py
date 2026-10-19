import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SYNC_SKILLS = REPO / "scripts" / "sync-skills.sh"
SYNC_RULES = REPO / "scripts" / "sync-rules.sh"
MERGE_CODEX = REPO / "scripts" / "merge-mcp-codex.py"
PRUNE_RETIRED = REPO / "scripts" / "prune-retired-mcp.py"
PRUNE_ALIASES = REPO / "scripts" / "prune-mcp-aliases.py"
MERGE_JSON = REPO / "scripts" / "merge-mcp.py"
SYNC_MCP = REPO / "scripts" / "sync-mcp.sh"
SYNC_CLAUDE = REPO / "scripts" / "sync-mcp-claude.py"


class SyncRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.hub = self.root / "hub"
        for name in ("kept-skill", "dropped-skill"):
            skill = self.hub / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        self.write_manifest("kept-skill", "dropped-skill")
        self.env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def write_manifest(self, *skills):
        lines = "".join(f"  - {name}\n" for name in skills)
        (self.hub / "manifest.yaml").write_text(
            "skills:\n" + lines,
            encoding="utf-8",
        )

    def run_script(self, *command):
        return subprocess.run(
            list(command),
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_skills_sync_removes_links_for_skills_dropped_from_manifest(self):
        self.run_script("bash", str(SYNC_SKILLS))
        codex = self.home / ".codex" / "skills"
        self.assertTrue((codex / "dropped-skill").is_symlink())
        outside = self.root / "outside-skill"
        outside.mkdir()
        (codex / "own-link").symlink_to(outside)
        (codex / "own-dir").mkdir()

        self.write_manifest("kept-skill")
        self.run_script("bash", str(SYNC_SKILLS))

        for relative in (".codex/skills", ".claude/skills", ".agents/skills"):
            base = self.home / relative
            self.assertTrue((base / "kept-skill").is_symlink(), relative)
            self.assertFalse(
                os.path.lexists(base / "dropped-skill"),
                relative,
            )
        self.assertTrue((codex / "own-link").is_symlink())
        self.assertTrue((codex / "own-dir").is_dir())
        self.assertTrue((self.hub / "skills" / "dropped-skill").is_dir())

    def test_skills_sync_removes_dangling_link_to_deleted_hub_skill(self):
        self.run_script("bash", str(SYNC_SKILLS))
        self.write_manifest("kept-skill")
        dropped = self.hub / "skills" / "dropped-skill"
        (dropped / "SKILL.md").unlink()
        dropped.rmdir()

        self.run_script("bash", str(SYNC_SKILLS))

        link = self.home / ".claude" / "skills" / "dropped-skill"
        self.assertFalse(os.path.lexists(link))

    def test_skills_dry_run_changes_nothing(self):
        self.run_script("bash", str(SYNC_SKILLS))
        self.write_manifest("kept-skill")
        fresh_home = self.root / "fresh-home"
        fresh_home.mkdir()

        result = self.run_script("bash", str(SYNC_SKILLS), "--dry-run")
        self.env["HOME"] = str(fresh_home)
        self.run_script("bash", str(SYNC_SKILLS), "--dry-run")

        self.assertIn("dropped-skill", result.stdout)
        self.assertTrue(
            (self.home / ".codex" / "skills" / "dropped-skill").is_symlink()
        )
        self.assertEqual(list(fresh_home.iterdir()), [])

    def test_codex_merge_preserves_codex_only_server_settings(self):
        canonical = self.root / "shared.json"
        canonical.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "remote": {"type": "http", "url": "https://example.invalid/mcp"},
                        "local": {"command": "npx", "args": ["-y", "pkg@1.0.0"]},
                    }
                }
            ),
            encoding="utf-8",
        )
        target = self.root / "config.toml"
        target.write_text(
            'model = "test"\n\n'
            "[mcp_servers.remote]\n"
            'url = "https://example.invalid/mcp"\n'
            "enabled = false\n"
            "tool_timeout_sec = 120\n\n"
            "[mcp_servers.local]\n"
            'command = "npx"\n'
            'args = ["-y", "pkg@1.0.0"]\n'
            "startup_timeout_sec = 60\n"
            "enabled_tools = [\n"
            '  "read", # comment\n'
            '  "search",\n'
            "]\n\n"
            "[features]\n"
            "hooks = true\n",
            encoding="utf-8",
        )

        self.run_script("python3", str(MERGE_CODEX), str(canonical), str(target))
        first = target.read_text(encoding="utf-8")
        self.run_script("python3", str(MERGE_CODEX), str(canonical), str(target))

        self.assertEqual(target.read_text(encoding="utf-8"), first)
        for line in (
            "enabled = false",
            "tool_timeout_sec = 120",
            "startup_timeout_sec = 60",
            "enabled_tools = [",
            '  "search",',
            "hooks = true",
        ):
            self.assertEqual(first.count(line), 1, line)
        remote = first.index("[mcp_servers.remote]")
        self.assertLess(remote, first.index("enabled = false"))
        self.assertLess(first.index("enabled = false"), first.index("[mcp_servers.local]"))

    def test_rules_sync_removes_output_for_rules_deleted_from_hub(self):
        rules = self.hub / "rules"
        rules.mkdir()
        (rules / "rule-a.md").write_text("Rule A\n", encoding="utf-8")
        (rules / "rule-b.md").write_text("Rule B\n", encoding="utf-8")
        targets = (
            self.home / ".claude" / "CLAUDE.md",
            self.home / ".codex" / "AGENTS.md",
            self.home / ".gemini" / "GEMINI.md",
        )
        for target in targets:
            target.parent.mkdir(parents=True)
            target.write_text("# Mine\n\nuser text\n", encoding="utf-8")
        copilot = self.home / ".copilot" / "instructions"
        copilot.mkdir(parents=True)
        manual = copilot / "manual.instructions.md"
        manual.write_text("---\nname: Manual\n---\nkeep\n", encoding="utf-8")
        self.run_script("bash", str(SYNC_RULES))

        (rules / "rule-b.md").unlink()
        self.run_script("bash", str(SYNC_RULES))
        first = [target.read_text(encoding="utf-8") for target in targets]
        self.run_script("bash", str(SYNC_RULES))

        for target, text in zip(targets, first):
            self.assertEqual(target.read_text(encoding="utf-8"), text)
            self.assertNotIn("agent-sync:begin:rule-b", text, target)
            self.assertNotIn("Rule B", text, target)
            self.assertIn("agent-sync:begin:rule-a", text, target)
            self.assertIn("user text", text, target)
        self.assertFalse((copilot / "rule-b.instructions.md").exists())
        self.assertTrue((copilot / "rule-a.instructions.md").exists())
        self.assertTrue(manual.exists())

    def test_rule_sync_leaves_malformed_stale_markers_alone(self):
        rules = self.hub / "rules"
        rules.mkdir()
        (rules / "rule-a.md").write_text("Rule A\n", encoding="utf-8")
        target = self.home / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "# Mine\n\n<!-- agent-sync:begin:old -->\nunclosed user text\n",
            encoding="utf-8",
        )

        self.run_script("bash", str(SYNC_RULES))

        text = target.read_text(encoding="utf-8")
        self.assertIn("unclosed user text", text)
        self.assertIn("agent-sync:begin:rule-a", text)

    def test_config_writers_keep_symlinked_targets(self):
        shared = self.root / "config" / "mcp_config.json"
        shared.parent.mkdir()
        link = self.root / "surface" / "mcp_config.json"
        link.parent.mkdir()
        retired = self.root / "retired.json"
        retired.write_text(json.dumps({"retiredServers": ["old"]}), encoding="utf-8")
        canonical = self.root / "shared.json"
        canonical.write_text(
            json.dumps({"mcpServers": {"exa": {"url": "https://example.invalid"}}}),
            encoding="utf-8",
        )
        shared.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "old": {"command": "x"},
                        "Exa": {"url": "https://example.invalid"},
                    }
                }
            ),
            encoding="utf-8",
        )
        link.symlink_to(shared)

        self.run_script("python3", str(PRUNE_RETIRED), str(retired), str(link))
        self.run_script("python3", str(PRUNE_ALIASES), str(canonical), str(link))

        self.assertTrue(link.is_symlink())
        servers = json.loads(shared.read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(sorted(servers), ["exa"])

        toml = self.root / "dotfiles" / "config.toml"
        toml.parent.mkdir()
        toml.write_text('model = "test"\n', encoding="utf-8")
        toml_link = self.root / "codex-config.toml"
        toml_link.symlink_to(toml)
        self.run_script("python3", str(MERGE_CODEX), str(canonical), str(toml_link))

        self.assertTrue(toml_link.is_symlink())
        self.assertIn("[mcp_servers.exa]", toml.read_text(encoding="utf-8"))

    # --- Claude MCP replacement must never lose the configured server ---

    def fake_claude(self, add_json_exit=0):
        """A `claude` stand-in that edits ~/.claude.json; plain `add` fails."""
        script = self.root / "claude"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            "path = Path.home() / '.claude.json'\n"
            "doc = json.loads(path.read_text()) if path.exists() else {}\n"
            "servers = doc.setdefault('mcpServers', {})\n"
            "args = sys.argv[1:]\n"
            "with open(Path.home() / 'claude.log', 'a') as log:\n"
            "    log.write(' '.join(args[:2]) + '\\n')\n"
            "if args[:2] == ['mcp', 'list']:\n"
            "    for name in servers: print(f'{name}: cmd - ok')\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['mcp', 'remove']:\n"
            "    servers.pop(args[-1], None)\n"
            "elif args[:2] == ['mcp', 'add-json']:\n"
            f"    if {add_json_exit}: raise SystemExit({add_json_exit})\n"
            "    servers[args[-2]] = json.loads(args[-1])\n"
            "elif args[:2] == ['mcp', 'add']:\n"
            "    raise SystemExit(1)\n"
            "path.write_text(json.dumps(doc))\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script

    def claude_setup(self, add_json_exit=0):
        original = {
            "type": "stdio",
            "command": "old-mcp",
            "args": ["--flag"],
            "env": {"SHARED_TOKEN": "original-secret-value"},
        }
        (self.home / ".claude.json").write_text(
            json.dumps({"mcpServers": {"shared": original}}),
            encoding="utf-8",
        )
        canonical = self.root / "claude-shared.json"
        canonical.write_text(
            json.dumps({"mcpServers": {"shared": {"command": "new-mcp"}}}),
            encoding="utf-8",
        )
        self.env["CLAUDE_BIN"] = str(self.fake_claude(add_json_exit))
        return original, canonical

    @staticmethod
    def entry_hash(entry):
        return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()

    def test_claude_sync_restores_entry_when_replacement_add_fails(self):
        original, canonical = self.claude_setup()

        result = subprocess.run(
            ["python3", str(SYNC_CLAUDE), str(canonical)],
            env=self.env, capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        servers = json.loads((self.home / ".claude.json").read_text())["mcpServers"]
        self.assertIn("shared", servers)
        self.assertEqual(self.entry_hash(servers["shared"]), self.entry_hash(original))
        self.assertIn("skipped: shared", result.stdout)
        self.assertNotIn("original-secret-value", result.stdout + result.stderr)

    def test_claude_sync_reports_failed_restore(self):
        _, canonical = self.claude_setup(add_json_exit=3)

        result = subprocess.run(
            ["python3", str(SYNC_CLAUDE), str(canonical)],
            env=self.env, capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("could not restore after a failed add: shared", result.stderr)
        self.assertNotIn("original-secret-value", result.stdout + result.stderr)

    def test_claude_sync_keeps_server_it_cannot_snapshot(self):
        _, canonical = self.claude_setup()
        (self.home / ".claude.json").write_text("{}", encoding="utf-8")
        listing = self.root / "claude"
        text = listing.read_text(encoding="utf-8").replace(
            "    for name in servers:", "    for name in ['shared', *servers]:"
        )
        listing.write_text(text, encoding="utf-8")

        subprocess.run(
            ["python3", str(SYNC_CLAUDE), str(canonical)],
            env=self.env, capture_output=True, text=True, check=True,
        )

        calls = (self.home / "claude.log").read_text().splitlines()
        self.assertNotIn("mcp remove", calls)

    # --- config writers replace files atomically and keep their mode ---

    def test_atomic_writer_keeps_original_on_failure_and_preserves_mode(self):
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import atomic_file
        finally:
            sys.path.pop(0)
        target = self.root / "mcp.json"
        target.write_text("old\n", encoding="utf-8")
        target.chmod(0o640)

        with mock.patch.object(atomic_file.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_file.atomic_write_text(target, "new\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.name.startswith(".mcp")), [])

        atomic_file.atomic_write_text(target, "new\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

    def test_merge_mcp_keeps_mode_and_symlink(self):
        shared = self.root / "dot" / "mcp.json"
        shared.parent.mkdir()
        shared.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        shared.chmod(0o640)
        link = self.root / "mcp-link.json"
        link.symlink_to(shared)
        canonical = self.root / "shared.json"
        canonical.write_text(
            json.dumps({"mcpServers": {"exa": {"url": "https://example.invalid"}}}),
            encoding="utf-8",
        )

        inode = shared.stat().st_ino

        self.run_script("python3", str(MERGE_JSON), str(canonical), str(link))

        # Replaced through a temp file + os.replace, never truncated in place.
        self.assertNotEqual(shared.stat().st_ino, inode)
        self.assertTrue(link.is_symlink())
        self.assertIn("exa", json.loads(shared.read_text(encoding="utf-8"))["mcpServers"])
        self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o640)

    # --- one broken MCP target must not stop the rest ---

    def test_mcp_sync_continues_past_failing_target_and_fails_at_end(self):
        (self.hub / "mcp").mkdir()
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps({"mcpServers": {"exa": {"url": "https://example.invalid"}}}),
            encoding="utf-8",
        )
        cursor = self.home / ".cursor" / "mcp.json"
        cursor.parent.mkdir()
        cursor.write_text("{ // comments are not JSON\n}", encoding="utf-8")
        gemini = self.home / ".gemini" / "settings.json"
        gemini.parent.mkdir()
        gemini.write_text("{}", encoding="utf-8")
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text('model = "test"\n', encoding="utf-8")
        self.env["CLAUDE_BIN"] = str(self.fake_claude())

        result = subprocess.run(
            ["bash", str(SYNC_MCP)], env=self.env, capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("MCP sync failed for 1 target(s)", result.stderr)
        self.assertIn("- Cursor", result.stderr)
        self.assertIn("exa", json.loads(gemini.read_text(encoding="utf-8"))["mcpServers"])
        self.assertIn("[mcp_servers.exa]", (self.home / ".codex" / "config.toml").read_text())
        self.assertIn("mcp add", (self.home / "claude.log").read_text())
        self.assertEqual(cursor.read_text(encoding="utf-8"), "{ // comments are not JSON\n}")

    # --- a foreign same-name symlink is backed up like a directory ---

    def test_skills_sync_backs_up_foreign_symlink_before_replacing(self):
        codex = self.home / ".codex" / "skills"
        codex.mkdir(parents=True)
        outside = self.root / "my-own-kept-skill"
        outside.mkdir()
        (codex / "kept-skill").symlink_to(outside)
        (codex / "dropped-skill").symlink_to(self.hub / "skills" / "no-longer-here")

        self.run_script("bash", str(SYNC_SKILLS))

        for name in ("kept-skill", "dropped-skill"):
            self.assertEqual(
                os.path.realpath(codex / name),
                os.path.realpath(self.hub / "skills" / name),
            )
        backups = list((self.hub / ".sync-backups").glob("*/.codex_skills/*"))
        self.assertEqual([p.name for p in backups], ["kept-skill"])
        self.assertTrue(backups[0].is_symlink())
        self.assertEqual(os.path.realpath(backups[0]), os.path.realpath(outside))

    # --- `agent-sync test` never distributes unless asked ---

    def test_suite_skips_live_distribution_unless_requested(self):
        tool = self.root / "tool"
        (tool / "bin").mkdir(parents=True)
        shutil.copytree(
            REPO / "scripts", tool / "scripts",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        calls = self.root / "calls.log"
        stub = tool / "bin" / "agent-sync"
        stub.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)
        env = self.env | {"AGENT_SYNC_HOME": str(tool)}
        env.pop("AGENT_SYNC_TEST_LIVE", None)

        default = subprocess.run(
            ["bash", str(tool / "scripts" / "test-suite.sh")],
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertIn("SKIP  second agent-sync all (live distribution", default.stdout)
        self.assertNotIn("all", calls.read_text().split())

        subprocess.run(
            ["bash", str(tool / "scripts" / "test-suite.sh")],
            env=env | {"AGENT_SYNC_TEST_LIVE": "1"}, capture_output=True, text=True, check=False,
        )
        self.assertIn("all", calls.read_text().split())


if __name__ == "__main__":
    unittest.main()
