import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SYNC_RULES = REPO / "scripts" / "sync-rules.sh"
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
                    "name: alpha\n"
                    "description: Agent rule synced by agent-sync from personal hub.\n"
                    'applyTo: "**"\n'
                    "---\n\n"
                    "[Alpha Rule]\n"
                    "Line with two spaces  \n"
                    "  Indented tail\n"
                ).encode("utf-8"),
                "beta.instructions.md": (
                    "---\n"
                    "name: beta\n"
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


if __name__ == "__main__":
    unittest.main()
