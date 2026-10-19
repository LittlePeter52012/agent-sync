"""Host-local capability overrides in ~/.config/agent-sync/host-policy.json."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.capability_resolver import resolve_target_policy
from scripts.config_inventory import SATISFIED_MCP_STATES, resolve_mcp_coverage
from scripts.host_policy import HostPolicyError, host_policy_path, parse_host_policy


REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "bin" / "agent-sync"
RESOLVER = REPO / "scripts" / "capability_resolver.py"
VERIFY_ALL = REPO / "scripts" / "verify-all.sh"
DEPLOY = REPO / "scripts" / "deploy-remote.sh"


def host_policy(targets, capability="memory"):
    return {
        "schema_version": 1,
        "capability_ownership": {
            "capabilities": {
                capability: {
                    "targets": {
                        target: {"selection": "disabled", "reason": "plugin not installed here"}
                        for target in targets
                    }
                }
            }
        },
    }


class HostPolicyParserTests(unittest.TestCase):
    def parse(self, document):
        text = document if isinstance(document, str) else json.dumps(document)
        return parse_host_policy(text, known_capabilities={"memory"})

    def test_reads_disabled_targets_with_reasons(self):
        overrides = self.parse(host_policy(["codex", "vscode:Work"]))
        self.assertEqual(set(overrides), {("memory", "codex"), ("memory", "vscode:Work")})
        self.assertEqual(overrides[("memory", "codex")].reason, "plugin not installed here")

    def test_rejects_anything_outside_the_schema(self):
        def changed(edit):
            document = host_policy(["codex"])
            edit(document)
            return document

        target = lambda d: d["capability_ownership"]["capabilities"]["memory"]["targets"]["codex"]
        cases = {
            "not json": "{",
            "duplicate key": '{"schema_version": 1, "schema_version": 1}',
            "root list": "[]",
            "wrong version": changed(lambda d: d.update(schema_version=2)),
            "boolean version": changed(lambda d: d.update(schema_version=True)),
            "unknown root key": changed(lambda d: d.update(mcp={})),
            "empty capabilities": changed(lambda d: d["capability_ownership"].update(capabilities={})),
            "unknown capability": host_policy(["codex"], capability="nonexistent"),
            "uppercase target": host_policy(["Codex"]),
            "other selection": changed(lambda d: target(d).update(selection="hub")),
            "missing reason": changed(lambda d: target(d).pop("reason")),
            "blank reason": changed(lambda d: target(d).update(reason="  ")),
            "extra target key": changed(lambda d: target(d).update(official_plugins=[])),
        }
        for case, document in cases.items():
            with self.subTest(case=case):
                with self.assertRaises(HostPolicyError):
                    self.parse(document)


class HostPolicyResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.hub = self.root / "hub"
        (self.hub / "mcp").mkdir(parents=True)
        (self.hub / "policies").mkdir()
        (self.hub / "skills" / "kept-skill").mkdir(parents=True)
        (self.hub / "skills" / "kept-skill" / "SKILL.md").write_text(
            "---\nname: kept-skill\ndescription: fixture\n---\n", encoding="utf-8"
        )
        (self.hub / "manifest.yaml").write_text("skills:\n  - kept-skill\n", encoding="utf-8")
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps({"mcpServers": {"memory-mcp": {"command": "/bin/sh"}}}),
            encoding="utf-8",
        )
        plugin_target = {"official_plugins": ["memory@official"], "selection": "plugin:memory@official"}
        (self.hub / "policies" / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "capability_ownership": {
                        "capabilities": {
                            "memory": {
                                "hub": {"skills": [], "mcp": ["memory-mcp"]},
                                "targets": {"claude": plugin_target, "codex": plugin_target},
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.policy_file = host_policy_path(self.home)
        # Plugin discovery asks the product CLIs; never reach the real ones.
        stubs = self.root / "bin"
        stubs.mkdir()
        for name in ("claude", "codex"):
            (stubs / name).write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")
            (stubs / name).chmod(0o755)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{stubs}:{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def write_host_policy(self, document):
        self.policy_file.parent.mkdir(parents=True, exist_ok=True)
        text = document if isinstance(document, str) else json.dumps(document)
        self.policy_file.write_text(text, encoding="utf-8")

    def run_cmd(self, *command):
        return subprocess.run(list(command), env=self.env, text=True, capture_output=True)

    def doctor_messages(self):
        result = self.run_cmd("python3", str(REPO / "scripts" / "agent-doctor.py"), "--json")
        return [item["message"] for item in json.loads(result.stdout)["findings"]]

    def test_without_host_policy_behaviour_is_unchanged(self):
        decision = resolve_target_policy("claude", self.home, self.hub).capabilities["memory"]
        coverage = resolve_mcp_coverage("claude", self.home, self.hub)

        self.assertEqual(decision.state, "owner-missing")
        self.assertEqual(coverage["memory-mcp"], {"state": "owner-missing", "owner": "plugin:memory@official"})
        self.assertTrue(
            any("Claude capability memory is owner-missing" in m for m in self.doctor_messages())
        )

    def test_override_disables_capability_on_this_host_only_for_listed_targets(self):
        self.write_host_policy(host_policy(["claude", "codex"]))

        resolution = resolve_target_policy("claude", self.home, self.hub)
        decision = resolution.capabilities["memory"]
        coverage = resolve_mcp_coverage("claude", self.home, self.hub)
        other = resolve_target_policy("gemini", self.home, self.hub).capabilities["memory"]

        self.assertEqual(decision.state, "disabled-on-host")
        self.assertEqual(decision.selection_source, "host-policy")
        self.assertEqual(decision.reason, "disabled on this host: plugin not installed here")
        self.assertIn("memory-mcp", resolution.suppressed_mcp)
        self.assertEqual(coverage["memory-mcp"]["state"], "disabled-on-host")
        self.assertIn("disabled-on-host", SATISFIED_MCP_STATES)
        self.assertEqual(coverage["memory-mcp"]["reason"], decision.reason)
        self.assertEqual(other.state, "hub-managed")
        messages = self.doctor_messages()
        self.assertFalse(any("capability memory is" in m for m in messages), messages)

    def test_verify_reports_disabled_on_host_as_satisfied_with_reason(self):
        self.write_host_policy(host_policy(["claude", "codex"]))

        result = self.run_cmd("bash", str(VERIFY_ALL))

        self.assertIn(f"✅ 2 override(s) in {self.policy_file}", result.stdout)
        self.assertIn("✅ Claude shared MCP 1/1", result.stdout)
        self.assertIn("✅ Codex / ChatGPT shared MCP 1/1", result.stdout)
        self.assertIn(
            "· memory-mcp: disabled-on-host — disabled on this host: plugin not installed here",
            result.stdout,
        )

    def test_invalid_host_policy_stops_sync_before_any_change(self):
        (self.hub / "rules").mkdir()
        (self.hub / "rules" / "example.md").write_text("[Example Rule]\nbe nice\n", encoding="utf-8")
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir()
        claude_md.write_text("# mine\n", encoding="utf-8")
        hermes_config = self.home / ".hermes" / "config.yaml"
        hermes_config.parent.mkdir()
        hermes_config.write_text("skills:\n  external_dirs: []\n", encoding="utf-8")
        invalid = host_policy(["claude"])
        invalid["capability_ownership"]["capabilities"]["memory"]["targets"]["claude"]["selection"] = "hub"
        self.write_host_policy(invalid)
        before = sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*"))

        validate = self.run_cmd("python3", str(RESOLVER), "--home", str(self.home), "--hub", str(self.hub), "validate")
        sync = self.run_cmd(str(ENTRYPOINT), "sync")
        verify = self.run_cmd("bash", str(VERIFY_ALL))

        self.assertEqual(validate.returncode, 2)
        self.assertIn("host policy", validate.stderr)
        self.assertIn("capabilities.memory.targets.claude.selection must be one of: disabled", validate.stderr)
        self.assertNotEqual(sync.returncode, 0)
        self.assertIn("host policy", sync.stderr)
        after = sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*"))
        self.assertEqual(after, before)
        self.assertEqual(claude_md.read_text(encoding="utf-8"), "# mine\n")
        self.assertEqual(hermes_config.read_text(encoding="utf-8"), "skills:\n  external_dirs: []\n")
        self.assertIn(f"❌ host policy {self.policy_file}: capabilities.memory.targets.claude.selection", verify.stdout)
        self.assertNotEqual(verify.returncode, 0)


class DeployKeepsHostPolicyTests(unittest.TestCase):
    """`agent-sync deploy --apply` replaces the tool and Hub, never the host policy."""

    def test_deploy_neither_ships_nor_replaces_the_host_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote_home = root / "remote-home"
            policy = host_policy_path(remote_home)
            policy.parent.mkdir(parents=True)
            original = json.dumps(host_policy(["codex"]), indent=2) + "\n"
            policy.write_text(original, encoding="utf-8")
            policy.chmod(0o600)
            for old in (".local/share/agent-sync", ".config/agent-hub"):
                (remote_home / old).mkdir(parents=True)
                (remote_home / old / "OLD").write_text("old\n", encoding="utf-8")

            sources = {}
            for name, files in (
                ("tool", {"bin/agent-sync": "#!/bin/sh\nexit 0\n", "VERSION": "9.9.9\n"}),
                ("hub", {"manifest.yaml": "skills:\n"}),
            ):
                repo = root / name
                for relative, text in files.items():
                    (repo / relative).parent.mkdir(parents=True, exist_ok=True)
                    (repo / relative).write_text(text, encoding="utf-8")
                (repo / "bin").exists() and (repo / "bin" / "agent-sync").chmod(0o755)
                subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
                subprocess.run(["git", "add", "."], cwd=repo, check=True)
                subprocess.run(
                    ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "-qm", "init"],
                    cwd=repo, check=True,
                )
                sources[name] = repo

            # Runs each "remote" command locally with the remote HOME.
            ssh = root / "local-ssh"
            ssh.write_text(
                "#!/bin/bash\n"
                'while [ "$1" = "-o" ]; do shift 2; done\n'
                "shift\n"
                'HOME="$REMOTE_HOME" exec /bin/bash -c "$*"\n',
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            env = os.environ | {
                "AGENT_SYNC_HOME": str(sources["tool"]),
                "AGENT_HUB_ROOT": str(sources["hub"]),
                "AGENT_SYNC_SSH_BIN": str(ssh),
                "REMOTE_HOME": str(remote_home),
            }

            result = subprocess.run(
                ["bash", str(DEPLOY), "mac-mini", "--apply"],
                env=env, text=True, capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                (remote_home / ".local/share/agent-sync/VERSION").read_text(encoding="utf-8"),
                "9.9.9\n",
            )
            self.assertEqual(policy.read_text(encoding="utf-8"), original)
            self.assertEqual(policy.stat().st_mode & 0o777, 0o600)
            backups = remote_home / ".local/state/agent-sync/remote-backups"
            self.assertEqual(list(backups.rglob("host-policy.json")), [])


if __name__ == "__main__":
    unittest.main()
