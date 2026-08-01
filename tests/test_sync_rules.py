import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.rule_blocks import top_level_managed_blocks


REPO = Path(__file__).resolve().parents[1]
SYNC_RULES = REPO / "scripts" / "sync-rules.sh"
VERIFY_ALL = REPO / "scripts" / "verify-all.sh"
DOCTOR = REPO / "scripts" / "agent-doctor.py"
AGGREGATE_TARGETS = (
    ".claude/CLAUDE.md",
    ".codex/AGENTS.md",
    ".gemini/GEMINI.md",
)


class SyncRulesTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.hub = self.root / "hub"
        self.rules = self.hub / "rules"
        self.rules.mkdir(parents=True)
        (self.hub / "manifest.yaml").write_text(
            "skills: []\n",
            encoding="utf-8",
        )
        (self.hub / "mcp").mkdir()
        (self.hub / "mcp" / "shared-servers.json").write_text(
            '{"mcpServers": {}}\n',
            encoding="utf-8",
        )
        self.rule_text = {
            "alpha": "[Alpha Rule]\nLine with two spaces  \n  Indented tail",
            "beta": "[Beta Rule]\n\nSecond body.\n\n",
        }
        for name, text in self.rule_text.items():
            (self.rules / f"{name}.md").write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def initialize_aggregate_targets(self, content):
        for relative in AGGREGATE_TARGETS:
            target = self.home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def initialize_aggregate_target_bytes(self, content):
        for relative in AGGREGATE_TARGETS:
            target = self.home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def run_sync(self):
        return subprocess.run(
            ["bash", str(SYNC_RULES)],
            cwd=self.root,
            env=os.environ
            | {
                "HOME": str(self.home),
                "AGENT_HUB_ROOT": str(self.hub),
            },
            text=True,
            capture_output=True,
            check=True,
        )

    def aggregate_bytes(self):
        return {
            relative: (self.home / relative).read_bytes()
            for relative in AGGREGATE_TARGETS
        }

    def test_aggregate_rules_are_byte_idempotent_for_empty_targets(self):
        self.initialize_aggregate_targets("")

        self.run_sync()
        first = self.aggregate_bytes()
        self.run_sync()

        self.assertEqual(self.aggregate_bytes(), first)

    def test_aggregate_rules_preserve_prose_and_canonicalize_legacy_separators(self):
        alpha = self.rule_text["alpha"]
        beta = self.rule_text["beta"].strip()
        legacy = (
            "  User preface.  \n\n\n"
            f"{alpha}\n\n"
            "<!-- agent-sync:begin:alpha -->\n"
            "stale alpha copy\n"
            "<!-- agent-sync:end:alpha -->\n\n\n"
            "User closing.\n\n\n"
            f"{beta}\n"
        )
        self.initialize_aggregate_targets(legacy)

        self.run_sync()
        first = self.aggregate_bytes()
        self.run_sync()

        self.assertEqual(self.aggregate_bytes(), first)
        expected = (
            "  User preface.  \n\n"
            "User closing.\n\n"
            "<!-- agent-sync:begin:alpha -->\n"
            "[Alpha Rule]\n"
            "Line with two spaces  \n"
            "  Indented tail\n"
            "<!-- agent-sync:end:alpha -->\n\n"
            "<!-- agent-sync:begin:beta -->\n"
            "[Beta Rule]\n\n"
            "Second body.\n\n"
            "<!-- agent-sync:end:beta -->\n"
        ).encode("utf-8")
        for relative, data in first.items():
            with self.subTest(target=relative):
                text = data.decode("utf-8")
                self.assertEqual(data, expected)
                for name, rule in self.rule_text.items():
                    self.assertEqual(
                        text.count(f"<!-- agent-sync:begin:{name} -->"),
                        1,
                    )
                    self.assertEqual(
                        text.count(f"<!-- agent-sync:end:{name} -->"),
                        1,
                    )
                    self.assertIn(rule.encode("utf-8"), data)

    def test_copilot_rule_files_remain_stable_and_preserve_source_text(self):
        self.initialize_aggregate_targets("")

        self.run_sync()
        copilot = self.home / ".copilot" / "instructions"
        first = {
            path.name: path.read_bytes()
            for path in sorted(copilot.glob("*.instructions.md"))
        }
        self.run_sync()
        second = {
            path.name: path.read_bytes()
            for path in sorted(copilot.glob("*.instructions.md"))
        }

        self.assertEqual(second, first)
        self.assertEqual(
            set(first),
            {"alpha.instructions.md", "beta.instructions.md"},
        )
        self.assertEqual(
            first,
            {
                "alpha.instructions.md": (
                    "---\n"
                    'name: "alpha"\n'
                    "description: Agent rule synced by agent-sync from personal hub.\n"
                    'applyTo: "**"\n'
                    "---\n\n"
                    "[Alpha Rule]\n"
                    "Line with two spaces  \n"
                    "  Indented tail\n"
                ).encode("utf-8"),
                "beta.instructions.md": (
                    "---\n"
                    'name: "beta"\n'
                    "description: Agent rule synced by agent-sync from personal hub.\n"
                    'applyTo: "**"\n'
                    "---\n\n"
                    "[Beta Rule]\n\n"
                    "Second body.\n\n"
                ).encode("utf-8"),
            },
        )
        for name, rule in self.rule_text.items():
            with self.subTest(rule=name):
                self.assertIn(rule.encode("utf-8"), first[f"{name}.instructions.md"])

    def test_batch_sync_preserves_overlapping_bodies_and_foreign_markers(self):
        alpha = (
            "[Alpha]\n"
            "Before overlap\n"
            "[Beta]\nShared body\n"
            "After overlap\n"
            "<!-- agent-sync:begin:beta -->\n"
            "literal foreign marker body\n"
            "<!-- agent-sync:end:beta -->\n"
        )
        beta = "[Beta]\nShared body"
        (self.rules / "alpha.md").write_text(alpha, encoding="utf-8")
        (self.rules / "beta.md").write_text(beta, encoding="utf-8")
        self.initialize_aggregate_targets("")

        self.run_sync()
        first = self.aggregate_bytes()
        self.run_sync()

        self.assertEqual(self.aggregate_bytes(), first)
        for relative, data in first.items():
            with self.subTest(target=relative):
                self.assertIn(alpha.encode("utf-8"), data)
                self.assertIn(beta.encode("utf-8"), data)
                logical = top_level_managed_blocks(
                    data.decode("utf-8"),
                    {"alpha", "beta"},
                )
                self.assertEqual(
                    [block.name for block in logical],
                    ["alpha", "beta"],
                )
                self.assertEqual(
                    data.count(
                        b"<!-- agent-sync:begin:alpha -->\n[Alpha]\n"
                    ),
                    1,
                )
                self.assertEqual(
                    data.count(
                        b"<!-- agent-sync:begin:beta -->\n[Beta]\n"
                    ),
                    1,
                )

        env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
        }
        verify = subprocess.run(
            ["bash", str(VERIFY_ALL)],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        doctor = subprocess.run(
            ["python3", str(DOCTOR), "--json"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        messages = "\n".join(
            finding["message"]
            for finding in json.loads(doctor.stdout)["findings"]
        )
        self.assertNotIn("synced rule", messages)

    def test_crlf_and_mixed_newlines_are_preserved_byte_for_byte(self):
        rule_bytes = {
            "alpha": b"[Alpha]\r\nCRLF body\r\n",
            "beta": b"[Beta]\nMixed body\r\nNo EOL",
        }
        for name, data in rule_bytes.items():
            (self.rules / f"{name}.md").write_bytes(data)
        unmanaged = b"Unmanaged\r\nMarkdown break  \r\n\r\n"
        self.initialize_aggregate_target_bytes(unmanaged)

        self.run_sync()
        first = self.aggregate_bytes()
        copilot = self.home / ".copilot" / "instructions"
        first_copilot = {
            name: (copilot / f"{name}.instructions.md").read_bytes()
            for name in rule_bytes
        }
        self.run_sync()

        self.assertEqual(self.aggregate_bytes(), first)
        self.assertEqual(
            {
                name: (copilot / f"{name}.instructions.md").read_bytes()
                for name in rule_bytes
            },
            first_copilot,
        )
        for relative, data in first.items():
            with self.subTest(target=relative):
                self.assertTrue(data.startswith(unmanaged))
                for rule in rule_bytes.values():
                    self.assertIn(rule, data)
        for name, rule in rule_bytes.items():
            with self.subTest(copilot=name):
                self.assertIn(rule, first_copilot[name])

    def test_copilot_name_is_a_json_compatible_quoted_yaml_scalar(self):
        name = 'bad: name # "quoted" 雪'
        rule = "[Special Filename Rule]\nBody.\n"
        (self.rules / "alpha.md").unlink()
        (self.rules / "beta.md").unlink()
        (self.rules / f"{name}.md").write_text(rule, encoding="utf-8")
        self.initialize_aggregate_targets("")

        self.run_sync()

        target = (
            self.home
            / ".copilot"
            / "instructions"
            / f"{name}.instructions.md"
        )
        frontmatter = target.read_text(encoding="utf-8").split("---", 2)[1]
        fields = dict(
            line.split(": ", 1)
            for line in frontmatter.strip().splitlines()
        )
        self.assertEqual(json.loads(fields["name"]), name)
        self.assertEqual(
            fields,
            {
                "name": json.dumps(name, ensure_ascii=False),
                "description": "Agent rule synced by agent-sync from personal hub.",
                "applyTo": '"**"',
            },
        )


if __name__ == "__main__":
    unittest.main()
