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

    def test_doctor_reports_skill_copy_that_is_not_hub_managed(self):
        report = json.loads(self.run_doctor().stdout)
        codex = next(
            agent for agent in report["agents"]
            if agent["name"] == "Codex / ChatGPT"
        )
        messages = "\n".join(item["message"] for item in report["findings"])

        self.assertEqual(codex["managed_skills"], {"configured": 0, "expected": 1})
        self.assertIn(
            "Codex / ChatGPT skill ownership is not Hub-managed.",
            messages,
        )

    def test_doctor_text_shows_managed_skill_coverage(self):
        env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
        }

        result = subprocess.run(
            ["python3", str(DOCTOR)],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("Managed", result.stdout)
        codex = next(
            line for line in result.stdout.splitlines()
            if line.startswith("Codex / ChatGPT")
        )
        self.assertRegex(codex, r"1/1\s+0/1")

    def test_json_flags_missing_supported_agent_configuration(self):
        report = json.loads(self.run_doctor().stdout)
        cursor = next(agent for agent in report["agents"] if agent["name"] == "Cursor")

        self.assertFalse(cursor["config_present"])
        self.assertTrue(any("Cursor" in finding["message"] for finding in report["findings"]))

    def test_doctor_reports_antigravity_surfaces_independently(self):
        roots = {
            "Gemini global": self.home / ".gemini" / "config",
            "Antigravity App": self.home / ".gemini" / "antigravity",
            "Antigravity CLI": self.home / ".gemini" / "antigravity-cli",
            "Antigravity IDE": self.home / ".gemini" / "antigravity-ide",
        }
        for root in roots.values():
            (root / "skills" / "example").mkdir(parents=True)
            (root / "skills" / "example" / "SKILL.md").write_text("skill")
            (root / "mcp_config.json").write_text(
                json.dumps(
                    {"mcpServers": {"shared": {"command": "shared-mcp"}}}
                )
            )

        fake_bin = Path(self.tempdir.name) / "bin"
        fake_bin.mkdir()
        agy = fake_bin / "agy"
        agy.write_text("#!/bin/sh\nexit 0\n")
        agy.chmod(0o755)
        report = json.loads(
            self.run_doctor(
                extra_env={
                    "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                }
            ).stdout
        )
        records = {agent["name"]: agent for agent in report["agents"]}

        for name in roots:
            self.assertIn(name, records)
            self.assertEqual(records[name]["skills"], {"configured": 1, "expected": 1})
            self.assertEqual(records[name]["mcp"], {"configured": 1, "expected": 1})
        self.assertTrue(records["Antigravity CLI"]["cli_present"])
        self.assertNotIn("Gemini / Antigravity", records)

        (roots["Antigravity CLI"] / "skills" / "example" / "SKILL.md").unlink()
        after = json.loads(
            self.run_doctor(
                extra_env={
                    "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                }
            ).stdout
        )
        coverage = [
            finding["message"]
            for finding in after["findings"]
            if "skill coverage is incomplete" in finding["message"]
            and (
                "Antigravity" in finding["message"]
                or "Gemini global" in finding["message"]
            )
        ]
        self.assertEqual(
            coverage,
            ["Antigravity CLI skill coverage is incomplete."],
        )

    def test_doctor_reports_gemini_cli_from_actual_user_settings(self):
        agents_skill = self.home / ".agents" / "skills" / "example"
        agents_skill.parent.mkdir(parents=True)
        agents_skill.symlink_to(self.hub / "skills" / "example")
        settings = self.home / ".gemini" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {"mcpServers": {"shared": {"command": "/bin/sh"}}}
            ),
            encoding="utf-8",
        )

        report = json.loads(self.run_doctor().stdout)
        record = next(
            agent for agent in report["agents"]
            if agent["name"] == "Gemini CLI"
        )

        self.assertEqual(record["skills"], {"configured": 1, "expected": 1})
        self.assertEqual(
            record["managed_skills"],
            {"configured": 1, "expected": 1},
        )
        self.assertEqual(record["mcp"], {"configured": 1, "expected": 1})

    def test_doctor_reports_case_insensitive_mcp_aliases(self):
        settings = self.home / ".gemini" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {"command": "/bin/sh"},
                        "SHARED": {"command": "/bin/sh"},
                    }
                }
            ),
            encoding="utf-8",
        )

        messages = "\n".join(
            item["message"]
            for item in json.loads(self.run_doctor().stdout)["findings"]
        )

        self.assertIn(
            "Duplicate MCP aliases in Gemini CLI: SHARED, shared",
            messages,
        )

    def test_doctor_reports_hermes_external_shared_skills(self):
        agents_skill = self.home / ".agents" / "skills" / "example"
        agents_skill.parent.mkdir(parents=True)
        agents_skill.symlink_to(self.hub / "skills" / "example")
        hermes = self.home / ".hermes"
        hermes.mkdir()
        (hermes / "config.yaml").write_text(
            "skills:\n  external_dirs:\n    - ~/.agents/skills\n",
            encoding="utf-8",
        )

        report = json.loads(self.run_doctor().stdout)
        record = next(
            agent for agent in report["agents"]
            if agent["name"] == "Hermes"
        )

        self.assertEqual(record["skills"], {"configured": 1, "expected": 1})
        self.assertEqual(
            record["managed_skills"],
            {"configured": 1, "expected": 1},
        )
        self.assertEqual(record["mcp"], {"configured": 0, "expected": 0})

    def test_doctor_treats_absent_hermes_as_optional(self):
        report = json.loads(self.run_doctor().stdout)
        names = {agent["name"] for agent in report["agents"]}
        messages = "\n".join(
            item["message"] for item in report["findings"]
        )

        self.assertNotIn("Hermes", names)
        self.assertNotIn(
            "Hermes is supported but has no local configuration",
            messages,
        )
        self.assertNotIn("Hermes skill coverage is incomplete", messages)

    def test_doctor_honors_custom_hermes_home(self):
        agents_skill = self.home / ".agents" / "skills" / "example"
        agents_skill.parent.mkdir(parents=True)
        agents_skill.symlink_to(self.hub / "skills" / "example")
        custom_home = self.home / "custom-hermes"
        custom_home.mkdir()
        (custom_home / "config.yaml").write_text(
            "skills:\n  external_dirs:\n    - ~/.agents/skills\n",
            encoding="utf-8",
        )

        report = json.loads(
            self.run_doctor(
                extra_env={"HERMES_HOME": str(custom_home)}
            ).stdout
        )
        record = next(
            agent for agent in report["agents"]
            if agent["name"] == "Hermes"
        )

        self.assertTrue(record["config_present"])
        self.assertEqual(record["skills"], {"configured": 1, "expected": 1})

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

        self.assertRegex(
            result.stdout,
            re.compile(r"^Claude\s+—\s+—\s+0/1$", re.M),
        )

    def test_list_sync_distinguishes_present_from_hub_managed_skills(self):
        env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
        }

        result = subprocess.run(
            ["bash", str(LIST_SYNC)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        codex = next(
            line for line in result.stdout.splitlines()
            if line.startswith("Codex ")
        )

        self.assertRegex(codex, r"Codex\s+1/1\s+0/1\s+—")

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

    def test_claude_mcp_sync_resolves_shared_placeholder_from_donors(self):
        canonical = self.tempdir.name + "/shared.json"
        Path(canonical).write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "shared-mcp",
                            "env": {
                                "OPENAPI_MCP_HEADERS": "${ANYTYPE_MCP_HEADERS}",
                                "OUTPUT_DIR": "${SHARED_OUTPUT_DIR}",
                            },
                        }
                    }
                }
            )
        )
        log = Path(self.tempdir.name) / "claude.log"
        fake_claude = Path(self.tempdir.name) / "claude"
        fake_claude.write_text(f'#!/bin/sh\n[ "$3" = "list" ] && exit 0\nprintf "%s\\n" "$*" > "{log}"\n')
        fake_claude.chmod(0o755)
        env = os.environ | {"HOME": str(self.home), "CLAUDE_BIN": str(fake_claude)}
        for relative in (
            ".cursor/mcp.json",
            ".gemini/settings.json",
        ):
            with self.subTest(relative=relative):
                for previous in (
                    self.home / ".cursor" / "mcp.json",
                    self.home / ".gemini" / "settings.json",
                ):
                    previous.unlink(missing_ok=True)
                donor = self.home / relative
                donor.parent.mkdir(parents=True, exist_ok=True)
                donor.write_text(
                    json.dumps(
                        {
                            "mcpServers": {
                                "shared": {
                                    "command": "shared-mcp",
                                    "env": {
                                        "OPENAPI_MCP_HEADERS": "donor-value",
                                        "OUTPUT_DIR": "donor-output",
                                    },
                                }
                            }
                        }
                    )
                )
                subprocess.run(
                    ["python3", str(SYNC_CLAUDE_MCP), canonical],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                self.assertIn(
                    "OPENAPI_MCP_HEADERS=donor-value",
                    log.read_text(),
                )
                self.assertIn("OUTPUT_DIR=donor-output", log.read_text())

    def test_doctor_resolves_placeholder_from_gemini_cli_settings(self):
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "/bin/sh",
                            "env": {
                                "GEMINI_ONLY_TOKEN": "${GEMINI_ONLY_TOKEN}"
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        settings = self.home / ".gemini" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {
                            "command": "/bin/sh",
                            "env": {
                                "GEMINI_ONLY_TOKEN": "marker-value"
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        messages = "\n".join(
            item["message"]
            for item in json.loads(self.run_doctor().stdout)["findings"]
        )

        self.assertNotIn(
            "Unresolved Hub placeholder for shared: GEMINI_ONLY_TOKEN",
            messages,
        )

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

    def test_doctor_runtime_rechecks_a_transient_connection_failure(self):
        fake_bin = Path(self.tempdir.name) / "bin"
        fake_bin.mkdir()
        opencode = fake_bin / "opencode"
        attempts = Path(self.tempdir.name) / "opencode-attempts"
        opencode.write_text(
            "#!/bin/sh\n"
            f"attempts={attempts}\n"
            "count=0\n"
            "[ -f \"$attempts\" ] && count=$(cat \"$attempts\")\n"
            "count=$((count + 1))\n"
            "printf '%s' \"$count\" >\"$attempts\"\n"
            "if [ \"$count\" -eq 1 ]; then\n"
            "  printf 'x mempalace failed\\n'\n"
            "else\n"
            "  printf 'mempalace connected\\n'\n"
            "fi\n",
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

        self.assertNotIn("Runtime MCP connection failed", messages)
        self.assertEqual(attempts.read_text(), "2")

    def test_doctor_runtime_supports_tool_specific_timeout(self):
        fake_bin = Path(self.tempdir.name) / "bin"
        fake_bin.mkdir()
        opencode = fake_bin / "opencode"
        opencode.write_text(
            "#!/bin/sh\nsleep 2\nprintf 'healthy: connected\\n'\n",
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
            extra_env={
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "AGENT_SYNC_RUNTIME_TIMEOUT": "1",
                "AGENT_SYNC_RUNTIME_TIMEOUT_OPENCODE": "10",
            },
        )
        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )

        self.assertNotIn("timed out for OpenCode", messages)

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

    def test_doctor_audits_tool_only_mcp_scope(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "cursor": {
                            "allowed_tool_only": ["cursor-native"],
                            "required_tool_only": ["cursor-native"],
                        },
                        "codex": {
                            "allowed_tool_only": ["llm_for_zotero"],
                            "required_tool_only": ["llm_for_zotero"],
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        cursor = self.home / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {"command": "/bin/sh"},
                        "cursor-native": {"command": "/bin/sh"},
                        "accidental-tool": {
                            "command": "/bin/sh",
                            "env": {"TOKEN": "TOKEN_SHOULD_NOT_APPEAR"},
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.home / ".codex" / "config.toml").write_text(
            '[mcp_servers.shared]\ncommand = "/bin/sh"\n'
            '[mcp_servers.llm_for_zotero]\n'
            'url = "http://127.0.0.1:23119/llm-for-zotero/mcp"\n'
            '[mcp_servers.llm_for_zotero.http_headers]\n'
            'Authorization = "Bearer TOKEN_SHOULD_NOT_APPEAR"\n',
            encoding="utf-8",
        )

        result = self.run_doctor()
        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )

        self.assertIn("Unexpected tool-only MCP in Cursor: accidental-tool", messages)
        self.assertNotIn("cursor-native", messages)
        self.assertNotIn("llm_for_zotero", messages)
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_doctor_accepts_legacy_antigravity_policy_key(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "antigravity": {
                            "allowed_tool_only": ["legacy-native"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        config = self.home / ".gemini" / "config" / "mcp_config.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared": {"command": "/bin/sh"},
                        "legacy-native": {"command": "/bin/sh"},
                    }
                }
            ),
            encoding="utf-8",
        )

        messages = "\n".join(
            item["message"]
            for item in json.loads(self.run_doctor().stdout)["findings"]
        )

        self.assertNotIn("unsupported MCP tool: antigravity", messages)
        self.assertNotIn("Unexpected tool-only MCP in Gemini global", messages)

    def test_doctor_reports_invalid_tool_only_mcp_policy(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "codex": {
                            "allowed_tool_only": ["allowed-native"],
                            "required_tool_only": [
                                "allowed-native",
                                "missing-native",
                            ],
                        },
                        "cursor": {
                            "allowed_tool_only": "not-a-list",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        report = json.loads(self.run_doctor().stdout)
        messages = "\n".join(item["message"] for item in report["findings"])

        self.assertIn(
            "required_tool_only must be a subset of allowed_tool_only",
            messages,
        )
        self.assertIn(
            "Required tool-only MCP missing from Codex / ChatGPT: allowed-native",
            messages,
        )
        self.assertIn("invalid allowed_tool_only for cursor", messages)

    def test_doctor_reports_missing_cli_for_shared_skill(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "skills": {
                        "example": {
                            "required_commands": [
                                "/private/TOKEN_SHOULD_NOT_APPEAR/"
                                "definitely-missing-example-cli"
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_doctor()
        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )

        self.assertIn(
            "Required CLI missing for Skill example: definitely-missing-example-cli",
            messages,
        )
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_doctor_accepts_available_cli_for_shared_skill(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "skills": {
                        "example": {"required_commands": ["example-cli"]}
                    }
                }
            ),
            encoding="utf-8",
        )
        binary_dir = Path(self.tempdir.name) / "bin"
        binary_dir.mkdir()
        binary = binary_dir / "example-cli"
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)

        result = self.run_doctor(
            extra_env={"PATH": f"{binary_dir}:{os.environ.get('PATH', '')}"}
        )
        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )

        self.assertNotIn("Required CLI missing for Skill example", messages)

    def test_doctor_reports_unlisted_skill_dependency_policy(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "skills": {
                        "not-in-manifest": {"required_commands": ["example-cli"]}
                    }
                }
            ),
            encoding="utf-8",
        )

        messages = "\n".join(
            item["message"]
            for item in json.loads(self.run_doctor().stdout)["findings"]
        )

        self.assertIn(
            "Skill dependency policy names unlisted Hub Skill: not-in-manifest",
            messages,
        )

    def test_doctor_redacts_path_from_unlisted_skill_dependency_policy(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "skills": {
                        "/private/TOKEN_SHOULD_NOT_APPEAR/not-in-manifest": {
                            "required_commands": ["example-cli"]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_doctor()

        self.assertIn(
            "Skill dependency policy names unlisted Hub Skill: not-in-manifest",
            result.stdout,
        )
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_doctor_reports_invalid_skill_dependency_policy(self):
        policy = self.hub / "policies"
        policy.mkdir()
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "skills": {
                        "example": {"required_commands": "not-a-list"}
                    }
                }
            ),
            encoding="utf-8",
        )

        messages = "\n".join(
            item["message"]
            for item in json.loads(self.run_doctor().stdout)["findings"]
        )

        self.assertIn(
            "Skill dependency policy has invalid required_commands for example",
            messages,
        )

    def test_doctor_audits_required_lifecycle_hooks_without_exposing_commands(self):
        policy = self.hub / "policies"
        policy.mkdir(exist_ok=True)
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "codex": {
                            "required_events": {
                                "SessionStart": [
                                    "TOKEN_SHOULD_NOT_APPEAR session-start"
                                ],
                                "Stop": ["mempalace hook run --hook stop"],
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        hooks = self.home / ".codex" / "hooks.json"
        hooks.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": (
                                            "/bin/sh TOKEN_SHOULD_NOT_APPEAR "
                                            "session-start"
                                        ),
                                    }
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_doctor()
        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )

        self.assertNotIn("SessionStart lifecycle hook is missing", messages)
        self.assertIn("Codex / ChatGPT Stop lifecycle hook is missing.", messages)
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_doctor_reports_invalid_lifecycle_hook_policy(self):
        policy = self.hub / "policies"
        policy.mkdir(exist_ok=True)
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "unsupported": {
                            "required_events": {
                                "Stop": ["example"]
                            }
                        },
                        "cursor": {
                            "required_events": "not-an-object"
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        messages = "\n".join(
            item["message"]
            for item in json.loads(self.run_doctor().stdout)["findings"]
        )

        self.assertIn(
            "Hook scope policy has an unsupported tool: unsupported",
            messages,
        )
        self.assertIn(
            "Hook scope policy has invalid required_events for cursor.",
            messages,
        )

    def test_doctor_reports_malformed_lifecycle_hook_command(self):
        policy = self.hub / "policies"
        policy.mkdir(exist_ok=True)
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "codex": {
                            "required_events": {
                                "SessionStart": ["session-start"],
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        hooks = self.home / ".codex" / "hooks.json"
        hooks.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "command": (
                                    '/bin/sh "TOKEN_SHOULD_NOT_APPEAR '
                                    "session-start"
                                )
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        result = self.run_doctor()
        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )

        self.assertIn(
            "Codex / ChatGPT SessionStart lifecycle hook command is malformed.",
            messages,
        )
        self.assertNotIn("TOKEN_SHOULD_NOT_APPEAR", result.stdout)

    def test_gemini_hook_findings_use_gemini_cli_label(self):
        policy = self.hub / "policies"
        policy.mkdir(exist_ok=True)
        (policy / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "gemini": {
                            "required_events": {
                                "PreCompress": ["mempal_precompact_hook.sh"]
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        messages = "\n".join(
            item["message"]
            for item in json.loads(self.run_doctor().stdout)["findings"]
        )

        self.assertIn(
            "Gemini CLI PreCompress lifecycle hook is missing.",
            messages,
        )
        self.assertNotIn(
            "Gemini global PreCompress lifecycle hook is missing.",
            messages,
        )

    def test_init_example_skill_dependency_policy_matches_manifest(self):
        initialized_hub = Path(self.tempdir.name) / "initialized-hub"
        env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(initialized_hub),
        }
        subprocess.run(
            [str(ENTRYPOINT), "init"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        result = subprocess.run(
            ["python3", str(DOCTOR), "--json"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        messages = "\n".join(
            item["message"] for item in json.loads(result.stdout)["findings"]
        )
        self.assertNotIn(
            "Skill dependency policy names unlisted Hub Skill",
            messages,
        )

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
