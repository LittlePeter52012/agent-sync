import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from scripts import rule_blocks


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

    def run_sync(self, check=True):
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
            check=check,
        )

    def aggregate_bytes(self):
        return {
            relative: (self.home / relative).read_bytes()
            for relative in AGGREGATE_TARGETS
        }

    def output_snapshot(self):
        paths = [self.home / relative for relative in AGGREGATE_TARGETS]
        paths.extend(
            sorted(
                (self.home / ".copilot" / "instructions").glob(
                    "*.instructions.md"
                )
            )
        )
        return {
            str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in paths
            if path.exists()
        }

    def tree_snapshot(self, root=None):
        root = root or self.home
        snapshot = {}
        if not root.exists():
            return snapshot
        for path in sorted(root.rglob("*")):
            relative = str(path.relative_to(root))
            metadata = path.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISREG(metadata.st_mode):
                snapshot[relative] = (
                    "file",
                    path.read_bytes(),
                    mode,
                    metadata.st_mtime_ns,
                )
            elif stat.S_ISDIR(metadata.st_mode):
                snapshot[relative] = ("directory", mode)
            elif stat.S_ISLNK(metadata.st_mode):
                snapshot[relative] = ("symlink", os.readlink(path), mode)
            else:
                snapshot[relative] = ("other", mode)
        return snapshot

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
            "<!-- agent-sync:begin:unknown -->\n"
            "literal unknown marker body\n"
            "<!-- agent-sync:end:unknown -->\n"
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
                logical = rule_blocks.parse_managed_blocks(
                    data.decode("utf-8"),
                    {"alpha", "beta"},
                ).blocks
                self.assertEqual(
                    [block.name for block in logical],
                    ["alpha", "beta"],
                )
                self.assertEqual(
                    rule_blocks.parse_managed_blocks(
                        data.decode("utf-8"),
                        {"alpha", "beta"},
                    ).issues,
                    [],
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

    def test_structural_preflight_fails_closed_without_any_output_write(self):
        self.initialize_aggregate_targets("")
        self.run_sync()
        valid_alpha = (self.rules / "alpha.md").read_text(encoding="utf-8")
        invalid_source = (
            "[Alpha]\n"
            "<!-- agent-sync:begin:alpha -->\n"
            "ambiguous own marker\n"
            "<!-- agent-sync:end:alpha -->\n"
        )
        (self.rules / "alpha.md").write_text(invalid_source, encoding="utf-8")
        before = self.output_snapshot()

        result = self.run_sync(check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            result.stderr,
            "ERROR: rule synchronization structural preflight failed; "
            "no files changed.\n",
        )
        self.assertEqual(self.output_snapshot(), before)
        source_issues = rule_blocks.source_marker_issues(
            "alpha",
            invalid_source,
        )
        self.assertEqual(
            {issue.kind for issue in source_issues},
            {"source-own-marker"},
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
        self.assertNotEqual(verify.returncode, 0)
        self.assertIn("structural rule error", verify.stdout)
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
        self.assertIn("Structural synced-rule error", messages)

        (self.rules / "alpha.md").write_text(valid_alpha, encoding="utf-8")
        malformed = {
            "ambiguous-begin": (
                "HEAD\n"
                "<!-- agent-sync:begin:alpha -->\n"
                "<!-- agent-sync:begin:alpha -->\n"
                "<!-- agent-sync:end:alpha -->\n"
            ),
            "unclosed-begin": (
                "HEAD\n<!-- agent-sync:begin:alpha -->\nTAIL\n"
            ),
            "stray-end": "HEAD\n<!-- agent-sync:end:alpha -->\nTAIL\n",
        }
        codex = self.home / ".codex" / "AGENTS.md"
        expected_issue = {
            "ambiguous-begin": "ambiguous-begin",
            "unclosed-begin": "unclosed-begin",
            "stray-end": "stray-end",
        }
        for case, text in malformed.items():
            with self.subTest(case=case):
                codex.write_text(text, encoding="utf-8")
                parsed = rule_blocks.parse_managed_blocks(
                    text,
                    {"alpha", "beta"},
                )
                self.assertEqual(
                    {issue.kind for issue in parsed.issues},
                    {expected_issue[case]},
                )
                before = self.output_snapshot()
                result = self.run_sync(check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output_snapshot(), before)
                verify = subprocess.run(
                    ["bash", str(VERIFY_ALL)],
                    cwd=self.root,
                    env=env,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(verify.returncode, 0)
                self.assertIn("structural rule error", verify.stdout)
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
                self.assertIn(
                    "Structural synced-rule error in AGENTS.md",
                    messages,
                )

    def test_verify_and_doctor_require_exact_managed_block_content(self):
        self.initialize_aggregate_targets("")
        self.run_sync()
        codex = self.home / ".codex" / "AGENTS.md"
        text = codex.read_text(encoding="utf-8")
        source = self.rule_text["alpha"]
        codex.write_text(
            text.replace(source, f"EXTRA BEFORE\n{source}\nEXTRA AFTER", 1),
            encoding="utf-8",
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
        doctor = subprocess.run(
            ["python3", str(DOCTOR), "--json"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertNotEqual(verify.returncode, 0)
        messages = "\n".join(
            finding["message"]
            for finding in json.loads(doctor.stdout)["findings"]
        )
        self.assertIn("Drifted synced rule in AGENTS.md", messages)

    def test_legacy_cleanup_is_longest_first_and_removes_lf_history(self):
        rule_data = {
            "alpha": b"INNER",
            "beta": b"PREFIX\nINNER\nSUFFIX",
            "delta": b"MIX\r\nA\nB\rC",
            "gamma": b"CRLF\r\nBODY",
        }
        for path in self.rules.glob("*.md"):
            path.unlink()
        for name, data in rule_data.items():
            (self.rules / f"{name}.md").write_bytes(data)
        legacy = (
            b"KEEP\n\n"
            b"PREFIX\nINNER\nSUFFIX\n\n"
            b"MIX\r\nA\nB\rC\n\n"
            b"MIX\nA\nB\nC\n\n"
            b"CRLF\r\nBODY\n\n"
            b"CRLF\nBODY\n"
        )
        self.initialize_aggregate_target_bytes(legacy)

        self.run_sync()
        first = self.aggregate_bytes()
        self.run_sync()

        self.assertEqual(self.aggregate_bytes(), first)
        for relative, data in first.items():
            with self.subTest(target=relative):
                parsed = rule_blocks.parse_managed_blocks(
                    data.decode("utf-8"),
                    set(rule_data),
                )
                unmanaged = data[: parsed.blocks[0].start]
                self.assertEqual(unmanaged, b"KEEP\n\n")
                self.assertEqual(
                    [block.name for block in parsed.blocks],
                    sorted(rule_data),
                )

    def test_empty_rules_are_noop_and_absent_math_keeps_priority_prose(self):
        for path in self.rules.glob("*.md"):
            path.unlink()
        self.initialize_aggregate_target_bytes(b"USER\r\n\r\n")
        copilot = self.home / ".copilot" / "instructions"
        copilot.mkdir(parents=True)
        (copilot / "user.instructions.md").write_bytes(b"USER COPILOT\r\n")
        before = self.output_snapshot()

        self.run_sync()

        self.assertEqual(self.output_snapshot(), before)

        (self.rules / "alpha.md").write_text("[Alpha]\n", encoding="utf-8")
        priority = (
            "> **路由优先级**：数学论文相关请求优先走下方 "
            "[Math Router Rule]，不要先用通用 skill router。"
        )
        self.initialize_aggregate_targets(f"USER\n\n{priority}\n")
        self.run_sync()
        first = self.aggregate_bytes()
        self.run_sync()
        self.assertEqual(self.aggregate_bytes(), first)
        for data in first.values():
            self.assertIn(priority.encode("utf-8"), data)

    def test_lone_cr_sources_and_separators_are_byte_idempotent(self):
        for path in self.rules.glob("*.md"):
            path.unlink()
        rule = b"[Alpha]\rLone CR body\r"
        (self.rules / "alpha.md").write_bytes(rule)
        unmanaged = b"USER\rTEXT\r\r"
        self.initialize_aggregate_target_bytes(unmanaged)

        self.run_sync()
        first = self.aggregate_bytes()
        copilot = (
            self.home
            / ".copilot"
            / "instructions"
            / "alpha.instructions.md"
        )
        first_copilot = copilot.read_bytes()
        self.run_sync()

        self.assertEqual(self.aggregate_bytes(), first)
        self.assertEqual(copilot.read_bytes(), first_copilot)
        self.assertIn(rule, first_copilot)
        for data in first.values():
            self.assertTrue(data.startswith(unmanaged))
            self.assertIn(rule, data)

    def test_transaction_rejects_unwritable_second_aggregate_without_changes(self):
        self.initialize_aggregate_targets("")
        self.run_sync()
        (self.rules / "alpha.md").write_text(
            "[Alpha Rule]\nChanged body.\n",
            encoding="utf-8",
        )
        codex = self.home / ".codex" / "AGENTS.md"
        codex.chmod(0o444)
        before = self.tree_snapshot()

        try:
            result = self.run_sync(check=False)
            after = self.tree_snapshot()
        finally:
            codex.chmod(0o644)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            result.stderr,
            "ERROR: rule synchronization output transaction failed; "
            "no files changed.\n",
        )
        self.assertEqual(after, before)

    def test_transaction_rejects_late_copilot_path_conflict_without_changes(self):
        self.initialize_aggregate_targets("")
        self.run_sync()
        (self.rules / "alpha.md").write_text(
            "[Alpha Rule]\nChanged body.\n",
            encoding="utf-8",
        )
        beta = (
            self.home
            / ".copilot"
            / "instructions"
            / "beta.instructions.md"
        )
        beta.unlink()
        beta.mkdir()
        before = self.tree_snapshot()

        result = self.run_sync(check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            result.stderr,
            "ERROR: rule synchronization output transaction failed; "
            "no files changed.\n",
        )
        self.assertEqual(self.tree_snapshot(), before)

    def test_transaction_rolls_back_an_injected_late_replace_failure(self):
        from scripts import rule_transaction

        transaction_root = self.root / "transaction"
        transaction_root.mkdir()
        first = transaction_root / "first.md"
        second = transaction_root / "second.md"
        created = transaction_root / "created.md"
        first.write_bytes(b"first original\n")
        second.write_bytes(b"second original\n")
        first.chmod(0o640)
        second.chmod(0o600)
        before = self.tree_snapshot(transaction_root)
        replacements = 0

        def fail_third_replace(source, destination):
            nonlocal replacements
            replacements += 1
            if replacements == 3:
                raise OSError("injected late replace failure")
            os.replace(source, destination)

        with self.assertRaises(rule_transaction.OutputTransactionError):
            rule_transaction.publish_outputs(
                (
                    (first, b"first replacement\n"),
                    (created, b"new file\n"),
                    (second, b"second replacement\n"),
                ),
                replace=fail_third_replace,
            )

        self.assertEqual(self.tree_snapshot(transaction_root), before)

    def test_legacy_cleanup_reaches_fixed_point_after_boundary_join(self):
        for path in self.rules.glob("*.md"):
            path.unlink()
        rule = "A\n\nA"
        (self.rules / "legacy.md").write_text(rule, encoding="utf-8")
        self.initialize_aggregate_targets("AA\n\nAA")

        self.run_sync()
        first = self.aggregate_bytes()
        self.run_sync()

        self.assertEqual(self.aggregate_bytes(), first)
        for relative, data in first.items():
            with self.subTest(target=relative):
                parsed = rule_blocks.parse_managed_blocks(
                    data.decode("utf-8"),
                    {"legacy"},
                )
                self.assertEqual(len(parsed.blocks), 1)
                self.assertEqual(data[: parsed.blocks[0].start], b"")
                self.assertEqual(data.count(rule.encode("utf-8")), 1)

    def test_legacy_cleanup_removes_8000_and_32000_copies_quickly(self):
        for path in self.rules.glob("*.md"):
            path.unlink()
        legacy_rule = "[Legacy Rule]\nBody"
        (self.rules / "legacy.md").write_text(
            legacy_rule,
            encoding="utf-8",
        )
        for copies in (8000, 32000):
            with self.subTest(copies=copies):
                self.initialize_aggregate_targets(
                    (legacy_rule + "\n\n") * copies + "KEEP\n"
                )
                started = time.monotonic()
                self.run_sync()
                elapsed = time.monotonic() - started

                self.assertLess(elapsed, 6.0)
                for relative, data in self.aggregate_bytes().items():
                    parsed = rule_blocks.parse_managed_blocks(
                        data.decode("utf-8"),
                        {"legacy"},
                    )
                    self.assertEqual(len(parsed.blocks), 1)
                    self.assertEqual(data[: parsed.blocks[0].start], b"KEEP\n\n")
                    self.assertEqual(
                        data.count(legacy_rule.encode("utf-8")),
                        1,
                    )


class RuleTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def snapshot(self, root=None):
        root = root or self.root
        snapshot = {}
        for path in sorted(root.rglob("*")):
            metadata = path.lstat()
            relative = str(path.relative_to(root))
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISREG(metadata.st_mode):
                snapshot[relative] = (
                    "file",
                    path.read_bytes(),
                    mode,
                    metadata.st_mtime_ns,
                )
            elif stat.S_ISDIR(metadata.st_mode):
                snapshot[relative] = ("directory", mode)
            else:
                snapshot[relative] = ("other", mode)
        return snapshot

    def transaction_artifacts(self, root=None):
        root = root or self.root
        return sorted(
            path
            for path in root.rglob(".agent-sync-rule-*")
            if path.is_file()
        )

    def make_mixed_outputs(self, root):
        root.mkdir()
        first = root / "first.md"
        second = root / "second.md"
        first.write_bytes(b"first original\n")
        second.write_bytes(b"second original\n")
        first.chmod(0o640)
        second.chmod(0o600)
        os.utime(first, ns=(1_600_000_000_000_000_000,) * 2)
        os.utime(second, ns=(1_610_000_000_000_000_000,) * 2)
        return (
            (first, b"first replacement\n"),
            (root / "created.md", b"created\n"),
            (second, b"second replacement\n"),
            (root / "nested" / "created.md", b"nested created\n"),
        )

    def test_keyboard_interrupt_at_every_commit_position_restores_everything(self):
        from scripts import rule_transaction

        for fail_at in range(1, 5):
            with self.subTest(fail_at=fail_at):
                transaction_root = self.root / f"interrupt-{fail_at}"
                outputs = self.make_mixed_outputs(transaction_root)
                before = self.snapshot(transaction_root)
                replacements = 0

                def interrupt_commit(source, destination):
                    nonlocal replacements
                    replacements += 1
                    if replacements == fail_at:
                        raise KeyboardInterrupt
                    os.replace(source, destination)

                with self.assertRaises(KeyboardInterrupt):
                    rule_transaction.publish_outputs(
                        outputs,
                        replace=interrupt_commit,
                    )

                self.assertEqual(self.snapshot(transaction_root), before)
                self.assertEqual(self.transaction_artifacts(transaction_root), [])

    def test_interrupt_after_creating_output_directory_removes_it(self):
        from scripts import rule_transaction

        output_parent = self.root / "created-parent"
        target = output_parent / "target.md"
        real_mkdir = Path.mkdir

        def mkdir_then_interrupt(path, *args, **kwargs):
            real_mkdir(path, *args, **kwargs)
            if path == output_parent:
                raise KeyboardInterrupt

        with mock.patch.object(Path, "mkdir", new=mkdir_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                rule_transaction.publish_outputs(((target, b"replacement\n"),))

        self.assertFalse(output_parent.exists())
        self.assertEqual(self.transaction_artifacts(), [])

    def test_backup_timestamp_failure_is_registered_and_cleaned(self):
        from scripts import rule_transaction

        target = self.root / "target.md"
        target.write_bytes(b"original\n")
        before = self.snapshot()

        def fail_utime(_path, *, ns):
            raise OSError(f"injected utime failure: {ns}")

        with self.assertRaises(rule_transaction.OutputTransactionError) as raised:
            rule_transaction.publish_outputs(
                ((target, b"replacement\n"),),
                utime=fail_utime,
            )

        self.assertTrue(raised.exception.recovered)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.transaction_artifacts(), [])

    def test_interrupt_during_new_stage_or_backup_registration_leaves_no_artifact(self):
        from scripts import rule_transaction

        cases = (
            ("stage", self.root / "new.md", 2),
            ("backup", self.root / "existing.md", 5),
        )
        for label, target, interrupt_at in cases:
            with self.subTest(label=label):
                if label == "backup":
                    target.write_bytes(b"original\n")
                before = self.snapshot()
                real_fstat = os.fstat
                calls = 0

                def interrupt_fstat(descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == interrupt_at:
                        raise KeyboardInterrupt
                    return real_fstat(descriptor)

                with mock.patch.object(
                    rule_transaction.os,
                    "fstat",
                    side_effect=interrupt_fstat,
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        rule_transaction.publish_outputs(
                            ((target, b"replacement\n"),)
                        )

                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.transaction_artifacts(), [])

    def test_each_rollback_operation_failure_preserves_recovery_backup(self):
        from scripts import rule_transaction

        for operation in ("replace", "chmod", "utime"):
            with self.subTest(operation=operation):
                transaction_root = self.root / f"rollback-{operation}"
                transaction_root.mkdir()
                first = transaction_root / "first.md"
                second = transaction_root / "second.md"
                first.write_bytes(b"first original\n")
                second.write_bytes(b"second original\n")
                commits = 0

                def fail_second_commit(source, destination):
                    nonlocal commits
                    commits += 1
                    if commits == 2:
                        raise OSError("injected second commit failure")
                    os.replace(source, destination)

                def fail_rollback_replace(_source, _destination):
                    raise OSError("injected rollback replace failure")

                def fail_rollback_chmod(_path, _mode):
                    raise OSError("injected rollback chmod failure")

                def fail_rollback_utime(_path, *, ns):
                    raise OSError(f"injected rollback utime failure: {ns}")

                kwargs = {
                    "rollback_replace": (
                        fail_rollback_replace if operation == "replace" else None
                    ),
                    "rollback_chmod": (
                        fail_rollback_chmod if operation == "chmod" else None
                    ),
                    "rollback_utime": (
                        fail_rollback_utime if operation == "utime" else None
                    ),
                }
                with self.assertRaises(
                    rule_transaction.OutputTransactionError
                ) as raised:
                    rule_transaction.publish_outputs(
                        (
                            (first, b"first replacement\n"),
                            (second, b"second replacement\n"),
                        ),
                        replace=fail_second_commit,
                        **kwargs,
                    )

                self.assertFalse(raised.exception.recovered)
                backups = self.transaction_artifacts(transaction_root)
                self.assertTrue(
                    any(path.read_bytes() == b"first original\n" for path in backups)
                )

    def test_concurrent_destination_replace_is_not_overwritten(self):
        from scripts import rule_transaction

        first = self.root / "first.md"
        second = self.root / "second.md"
        first.write_bytes(b"first original\n")
        second.write_bytes(b"second original\n")
        commits = 0

        def replace_then_concurrent_write(source, destination):
            nonlocal commits
            commits += 1
            if commits == 2:
                concurrent = self.root / "concurrent.tmp"
                concurrent.write_bytes(b"CONCURRENT\n")
                os.replace(concurrent, first)
                raise OSError("stop after concurrent write")
            os.replace(source, destination)

        with self.assertRaises(rule_transaction.OutputTransactionError) as raised:
            rule_transaction.publish_outputs(
                (
                    (first, b"first replacement\n"),
                    (second, b"second replacement\n"),
                ),
                replace=replace_then_concurrent_write,
            )

        self.assertFalse(raised.exception.recovered)
        self.assertEqual(first.read_bytes(), b"CONCURRENT\n")
        self.assertEqual(second.read_bytes(), b"second original\n")
        self.assertTrue(
            any(
                path.read_bytes() == b"first original\n"
                for path in self.transaction_artifacts()
            )
        )

    def test_parent_path_swap_does_not_publish_into_replacement_directory(self):
        from scripts import rule_transaction

        parent = self.root / "outputs"
        parent.mkdir()
        target = parent / "target.md"
        target.write_bytes(b"original\n")
        moved = self.root / "moved-outputs"

        def swap_parent(_source, _destination):
            parent.rename(moved)
            parent.mkdir()
            (parent / "target.md").write_bytes(b"CONCURRENT\n")
            raise OSError("parent swapped")

        with self.assertRaises(rule_transaction.OutputTransactionError) as raised:
            rule_transaction.publish_outputs(
                ((target, b"replacement\n"),),
                replace=swap_parent,
            )

        self.assertFalse(raised.exception.recovered)
        self.assertEqual((parent / "target.md").read_bytes(), b"CONCURRENT\n")
        self.assertEqual((moved / "target.md").read_bytes(), b"original\n")
        self.assertTrue(
            any(
                path.read_bytes() == b"original\n"
                for path in self.transaction_artifacts(moved)
            )
        )

    def test_existing_hardlink_destination_is_rejected_without_mutation(self):
        from scripts import rule_transaction

        target = self.root / "target.md"
        peer = self.root / "peer.md"
        target.write_bytes(b"original\n")
        os.link(target, peer)
        before = self.snapshot()

        with self.assertRaises(rule_transaction.OutputTransactionError) as raised:
            rule_transaction.publish_outputs(((target, b"replacement\n"),))

        self.assertTrue(raised.exception.recovered)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(target.stat().st_ino, peer.stat().st_ino)

    def test_cli_reports_manual_recovery_for_incomplete_rollback(self):
        script_dir = self.root / "scripts"
        script_dir.mkdir()
        for name in ("sync-rules.sh", "lib.sh", "rule_blocks.py"):
            shutil.copy2(REPO / "scripts" / name, script_dir / name)
        (script_dir / "rule_transaction.py").write_text(
            "class OutputTransactionError(RuntimeError):\n"
            "    def __init__(self, *, recovered):\n"
            "        self.recovered = recovered\n"
            "\n"
            "def publish_outputs(outputs):\n"
            "    list(outputs)\n"
            "    raise OutputTransactionError(recovered=False)\n",
            encoding="utf-8",
        )
        hub = self.root / "hub"
        rules = hub / "rules"
        rules.mkdir(parents=True)
        (rules / "alpha.md").write_text("[Alpha]\n", encoding="utf-8")
        home = self.root / "home"
        for relative in AGGREGATE_TARGETS:
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("USER\n", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(script_dir / "sync-rules.sh")],
            cwd=self.root,
            env=os.environ
            | {
                "HOME": str(home),
                "AGENT_HUB_ROOT": str(hub),
            },
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            result.stderr,
            "ERROR: rule synchronization output transaction failed; "
            "manual recovery required.\n",
        )


if __name__ == "__main__":
    unittest.main()
