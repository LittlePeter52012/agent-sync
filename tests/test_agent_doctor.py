import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DOCTOR = REPO / "scripts" / "agent-doctor.py"
ENTRYPOINT = REPO / "bin" / "agent-sync"
SYNC_RULES = REPO / "scripts" / "sync-rules.sh"
LIST_SYNC = REPO / "scripts" / "list-sync.sh"
SYNC_CLAUDE_MCP = REPO / "scripts" / "sync-mcp-claude.py"
SYNC_MCP = REPO / "scripts" / "sync-mcp.sh"


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

    def run_doctor(self, *args, extra_env=None, check=True):
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(DOCTOR), "--json", *args],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=check,
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

    def test_rule_sync_deduplicates_legacy_rule_copies(self):
        rule = "[Agent Sync Disambiguation Rule]\nUse agent-sync.\n"
        rules = self.hub / "rules"
        rules.mkdir()
        (rules / "agent-sync-disambiguation.md").write_text(rule)
        for relative in (".codex/AGENTS.md", ".claude/CLAUDE.md", ".gemini/GEMINI.md"):
            target = self.home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rule * 3)
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        subprocess.run(["bash", str(SYNC_RULES)], env=env, check=True, capture_output=True, text=True)

        target = self.home / ".codex" / "AGENTS.md"
        self.assertEqual(target.read_text().count(rule.strip()), 1)

    def test_list_sync_reports_claude_mcp_coverage(self):
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        result = subprocess.run(["bash", str(LIST_SYNC)], env=env, check=True, capture_output=True, text=True)

        self.assertRegex(result.stdout, re.compile(r"^Claude\s+—\s+0/1$", re.M))

    def test_fix_dry_run_describes_repairs_without_writing(self):
        target = self.home / ".codex" / "AGENTS.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("unchanged\n")
        before = target.read_text()
        env = os.environ | {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        result = subprocess.run(
            [str(ENTRYPOINT), "fix", "--dry-run"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("sync skills", result.stdout)
        self.assertIn("deduplicate synced rules", result.stdout)
        self.assertEqual(target.read_text(), before)

    def test_claude_mcp_sync_resolves_shared_placeholder_from_cursor(self):
        cursor = self.home / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text(json.dumps({"mcpServers": {"shared": {"command": "shared-mcp", "env": {"OPENAPI_MCP_HEADERS": "donor-value"}}}}))
        canonical = self.tempdir.name + "/shared.json"
        Path(canonical).write_text(json.dumps({"mcpServers": {"shared": {"command": "shared-mcp", "env": {"OPENAPI_MCP_HEADERS": "${ANYTYPE_MCP_HEADERS}"}}}}))
        log = Path(self.tempdir.name) / "claude.log"
        fake_claude = Path(self.tempdir.name) / "claude"
        fake_claude.write_text(f'#!/bin/sh\n[ "$3" = "list" ] && exit 0\nprintf "%s\\n" "$*" > "{log}"\n')
        fake_claude.chmod(0o755)
        env = os.environ | {"HOME": str(self.home), "CLAUDE_BIN": str(fake_claude)}
        subprocess.run(["python3", str(SYNC_CLAUDE_MCP), canonical], env=env, check=True, capture_output=True, text=True)

        self.assertIn("OPENAPI_MCP_HEADERS=donor-value", log.read_text())

    def test_vscode_profile_without_mcp_is_reported_and_synced(self):
        profile = self.home / "Library" / "Application Support" / "Code" / "User" / "profiles" / "paper"
        profile.mkdir(parents=True)
        (profile / "settings.json").write_text("{}")
        fake_claude = Path(self.tempdir.name) / "claude"
        fake_claude.write_text('#!/bin/sh\nexit 0\n')
        fake_claude.chmod(0o755)
        env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "CLAUDE_BIN": str(fake_claude),
        }

        before = json.loads(self.run_doctor().stdout)
        vscode = next(agent for agent in before["agents"] if agent["name"] == "Copilot / VS Code")
        self.assertEqual(vscode["profiles"], [{"id": "paper", "mcp": {"configured": 0, "expected": 1}}])
        self.assertTrue(any("paper" in finding["message"] for finding in before["findings"]))

        subprocess.run(["bash", str(SYNC_MCP)], env=env, check=True, capture_output=True, text=True)
        profile_config = json.loads((profile / "mcp.json").read_text())
        self.assertIn("shared", profile_config["servers"])

    def test_doctor_reports_unresolved_placeholder_and_missing_executable_safely(self):
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "broken": {
                            "command": (
                                "/private/TOKEN_SHOULD_NOT_APPEAR/"
                                "definitely-missing-mcp-command"
                            ),
                            "env": {"TOKEN": "${MISSING_TOKEN}"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_doctor()
        report = json.loads(result.stdout)
        messages = "\n".join(finding["message"] for finding in report["findings"])

        self.assertIn("MISSING_TOKEN", messages)
        self.assertIn("definitely-missing-mcp-command", messages)
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_doctor_reports_missing_tool_only_mcp_and_retired_residue(self):
        (self.hub / "mcp" / "retired-servers.json").write_text(
            json.dumps({"retiredServers": ["retired-tool"]}),
            encoding="utf-8",
        )
        (self.home / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {"command": "/bin/sh"},
                        "broken-tool-only": {
                            "command": "definitely-missing-tool-only-command",
                        },
                        "retired-tool": {"command": "/bin/sh"},
                    }
                }
            ),
            encoding="utf-8",
        )

        report = json.loads(self.run_doctor().stdout)
        messages = "\n".join(item["message"] for item in report["findings"])

        self.assertIn("broken-tool-only", messages)
        self.assertIn("retired-tool", messages)
        self.assertIn("Claude", messages)

    def test_doctor_runtime_reports_failed_mcp_without_raw_output(self):
        fake_bin = Path(self.tempdir.name) / "bin"
        fake_bin.mkdir()
        opencode = fake_bin / "opencode"
        opencode.write_text(
            "#!/bin/sh\n"
            "printf 'x stale-runtime failed TOKEN_SHOULD_NOT_APPEAR\\n'\n",
            encoding="utf-8",
        )
        opencode.chmod(0o755)
        claude = fake_bin / "claude"
        claude.write_text(
            "#!/bin/sh\nprintf 'healthy: command - connected\\n'\n",
            encoding="utf-8",
        )
        claude.chmod(0o755)

        result = self.run_doctor(
            "--runtime",
            extra_env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        )
        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )

        self.assertIn("stale-runtime", messages)
        self.assertIn("OpenCode", messages)
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_doctor_audits_required_and_forbidden_plugin_scope(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "plugins": {
                        "opencode": {"required": ["required-plugin"]},
                        "codex": {"forbidden": ["forbidden-plugin@market"]},
                    }
                }
            ),
            encoding="utf-8",
        )
        opencode = self.home / ".config" / "opencode"
        opencode.mkdir(parents=True)
        (opencode / "opencode.json").write_text(
            json.dumps({"plugin": ["another-plugin"], "mcp": {}}),
            encoding="utf-8",
        )
        (self.home / ".codex" / "config.toml").write_text(
            '[plugins."forbidden-plugin@market"]\nenabled = true\n',
            encoding="utf-8",
        )

        report = json.loads(self.run_doctor().stdout)
        messages = "\n".join(item["message"] for item in report["findings"])

        self.assertIn("required-plugin", messages)
        self.assertIn("forbidden-plugin@market", messages)

    def test_strict_runtime_exits_nonzero_when_findings_exist(self):
        (self.home / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "broken-tool-only": {
                            "command": "definitely-missing-tool-only-command",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_doctor("--strict", check=False)

        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
