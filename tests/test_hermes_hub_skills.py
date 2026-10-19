"""Hermes gets its own Hub Skill target with manifest-driven exclusions."""

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.hub_manifest import (
    SKILL_TARGETS,
    ManifestError,
    read_manifest_skills,
    read_target_exclusions,
    unlisted_exclusions,
)


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SYNC_SKILLS = SCRIPTS / "sync-skills.sh"
SYNC_HERMES = SCRIPTS / "sync_hermes.py"
LIST_SYNC = SCRIPTS / "list-sync.sh"
VERIFY_ALL = SCRIPTS / "verify-all.sh"

HUB_ENTRY = "~/.hermes/hub-skills"
SHARED_ENTRY = "~/.agents/skills"


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name} fixture\n---\n# {name}\n"


def manifest(skills, exclude=None) -> str:
    text = ""
    if exclude is not None:
        text += "targets:\n  hermes:\n"
        if exclude:
            text += "    exclude:\n" + "".join(f"      - {s}\n" for s in exclude)
        else:
            text += "    exclude: []\n"
    return text + "skills:\n" + "".join(f"  - {s}\n" for s in skills)


class HubManifestTargetsTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text(text, encoding="utf-8")
            return read_target_exclusions(path)

    def test_reads_canonical_section_before_or_after_skills(self):
        self.assertEqual(
            self.parse(manifest(["alpha", "beta"], exclude=["beta"])),
            {"hermes": ("beta",)},
        )
        self.assertEqual(
            self.parse(
                "skills:\n  - alpha\n"
                "# narrowed per target\n"
                "targets:\n  hermes:\n    exclude: []\n"
            ),
            {"hermes": ()},
        )
        self.assertEqual(self.parse("skills:\n  - alpha\n"), {})

    def test_rejects_anything_outside_the_schema(self):
        cases = {
            "unknown target": "targets:\n  nobody:\n    exclude: []\n",
            "unknown key": "targets:\n  hermes:\n    include:\n      - a\n",
            "inline mapping": "targets: {hermes: {exclude: [a]}}\n",
            "inline list": "targets:\n  hermes:\n    exclude: [a]\n",
            "duplicate target": "targets:\n  hermes:\n    exclude: []\n  hermes:\n    exclude: []\n",
            "duplicate skill": "targets:\n  hermes:\n    exclude:\n      - a\n      - a\n",
            "duplicate section": "targets:\n  hermes:\n    exclude: []\ntargets:\n",
            "unsafe name": "targets:\n  hermes:\n    exclude:\n      - ../outside\n",
            "tab indentation": "targets:\n\thermes:\n",
            "entry without exclude": "targets:\n  hermes:\n      - a\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                with self.assertRaises(ManifestError):
                    self.parse(text)

    def test_reports_exclusions_of_unlisted_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text(manifest(["alpha"], exclude=["alpha", "ghost"]), encoding="utf-8")
            self.assertEqual(read_manifest_skills(path), ["alpha"])
            self.assertEqual(unlisted_exclusions(path), ["hermes:ghost"])

    def test_known_targets_match_lib_sh(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".hermes").mkdir()
            (home / ".hermes" / "config.yaml").write_text("skills: {}\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", "-c", f'source "{SCRIPTS / "lib.sh"}"; skill_target_specs'],
                env=os.environ | {"HOME": str(home), "HERMES_HOME": str(home / ".hermes")},
                text=True,
                capture_output=True,
                check=True,
            )
        keys = {line.split("|", 1)[0] for line in result.stdout.splitlines()}
        self.assertEqual(keys, set(SKILL_TARGETS))


class HermesHubSkillsTests(unittest.TestCase):
    SKILLS = ("alpha", "beta", "withheld")

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.hermes = self.home / ".hermes"
        self.hermes.mkdir(parents=True)
        self.config = self.hermes / "config.yaml"
        self.config.write_text(
            "model:\n  default: example  # keep me\n"
            "skills:\n"
            "  external_dirs:\n"
            f"    - {SHARED_ENTRY}\n"
            "  template_vars: true\n"
            "memory:\n  memory_enabled: true\n",
            encoding="utf-8",
        )
        self.config.chmod(0o600)
        self.hub_skills = self.hermes / "hub-skills"
        self.shared = self.home / ".agents" / "skills"
        self.hub = self.root / "hub"
        for name in self.SKILLS:
            skill = self.hub / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(skill_md(name), encoding="utf-8")
        self.write_manifest(self.SKILLS, exclude=["withheld"])
        self.env = os.environ | {
            "HOME": str(self.home),
            "HERMES_HOME": str(self.hermes),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def write_manifest(self, skills, exclude=None):
        (self.hub / "manifest.yaml").write_text(manifest(skills, exclude), encoding="utf-8")

    def run_cmd(self, *command, check=True):
        return subprocess.run(
            list(command), env=self.env, text=True, capture_output=True, check=check
        )

    def sync_skills(self, *args, check=True):
        return self.run_cmd("bash", str(SYNC_SKILLS), *args, check=check)

    def sync_hermes(self, *args, check=True):
        return self.run_cmd("python3", str(SYNC_HERMES), *args, check=check)

    def own_skill(self, home: Path, name: str, category="own"):
        skill = home / "skills" / category / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(skill_md(name), encoding="utf-8")

    def backups(self):
        return sorted((self.hub / ".sync-backups").glob("*/hermes/**/config.yaml"))

    def assert_hub_link(self, path: Path, name: str):
        self.assertTrue(path.is_symlink(), path)
        self.assertEqual(os.path.realpath(path), os.path.realpath(self.hub / "skills" / name))

    # --- the Hermes target honors manifest exclusions ---

    def test_hermes_target_links_allowlist_minus_exclusions(self):
        self.sync_skills()

        self.assert_hub_link(self.hub_skills / "alpha", "alpha")
        self.assert_hub_link(self.hub_skills / "beta", "beta")
        self.assertFalse(os.path.lexists(self.hub_skills / "withheld"))
        for name in self.SKILLS:  # other tools are not narrowed
            self.assert_hub_link(self.shared / name, name)
            self.assert_hub_link(self.home / ".codex" / "skills" / name, name)

    def test_newly_excluded_skill_loses_its_hermes_link_only(self):
        self.write_manifest(self.SKILLS)
        self.sync_skills()
        self.assert_hub_link(self.hub_skills / "withheld", "withheld")

        self.write_manifest(self.SKILLS, exclude=["withheld"])
        self.sync_skills()

        self.assertFalse(os.path.lexists(self.hub_skills / "withheld"))
        self.assert_hub_link(self.shared / "withheld", "withheld")

    def test_no_hermes_config_means_no_hermes_target(self):
        self.config.unlink()
        self.sync_skills()
        self.assertFalse(self.hub_skills.exists())

    def test_invalid_targets_section_stops_before_any_change(self):
        (self.hub / "manifest.yaml").write_text(
            "targets:\n  hermes:\n    exclude: [withheld]\nskills:\n  - alpha\n",
            encoding="utf-8",
        )
        result = self.sync_skills(check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("targets", result.stderr)
        self.assertFalse(self.hub_skills.exists())
        self.assertFalse(self.shared.exists())

    def test_prune_in_hub_skills_only_removes_hub_links(self):
        self.write_manifest(("alpha", "beta", "withheld", "dropped"), exclude=["withheld"])
        dropped = self.hub / "skills" / "dropped"
        dropped.mkdir()
        (dropped / "SKILL.md").write_text(skill_md("dropped"), encoding="utf-8")
        self.sync_skills()
        self.assert_hub_link(self.hub_skills / "dropped", "dropped")
        outside = self.root / "outside"
        for name in ("own-link-target", "alpha-elsewhere"):
            (outside / name).mkdir(parents=True)
        (self.hub_skills / "own-link").symlink_to(outside / "own-link-target")
        (self.hub_skills / "own-dir").mkdir()
        (self.hub_skills / "withheld").mkdir()  # a real dir with an excluded name
        (self.hub_skills / "alpha").unlink()
        (self.hub_skills / "alpha").symlink_to(outside / "alpha-elsewhere")
        (self.hub_skills / "beta").unlink()
        (self.hub_skills / "beta").mkdir()  # a real dir with a listed name

        self.write_manifest(self.SKILLS, exclude=["withheld"])
        result = self.sync_skills()

        self.assertFalse(os.path.lexists(self.hub_skills / "dropped"))
        self.assertTrue((self.hub_skills / "own-link").is_symlink())
        self.assertTrue((self.hub_skills / "own-dir").is_dir())
        self.assertTrue((self.hub_skills / "withheld").is_dir())
        self.assertFalse((self.hub_skills / "withheld").is_symlink())
        self.assertTrue((self.hub_skills / "beta").is_dir())
        self.assertFalse((self.hub_skills / "beta").is_symlink())
        self.assertIn("beta — directory exists", result.stderr)
        self.assert_hub_link(self.hub_skills / "alpha", "alpha")
        saved = list((self.hub / ".sync-backups").glob("*/.hermes_hub-skills/alpha"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(os.path.realpath(saved[0]), os.path.realpath(outside / "alpha-elsewhere"))

    # --- external_dirs migration ---

    def test_drops_shared_entry_when_hermes_loses_nothing(self):
        self.own_skill(self.hermes, "withheld")
        original = self.config.read_text(encoding="utf-8")
        self.sync_skills()

        result = self.sync_hermes()

        text = self.config.read_text(encoding="utf-8")
        self.assertIn(f"  external_dirs:\n    - {HUB_ENTRY}\n  template_vars: true\n", text)
        self.assertNotIn(SHARED_ENTRY, text)
        self.assertIn("model:\n  default: example  # keep me\n", text)
        self.assertIn("memory:\n  memory_enabled: true\n", text)
        self.assertIn(f"removed {SHARED_ENTRY}", result.stdout)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        [saved] = self.backups()
        self.assertEqual(saved.read_text(encoding="utf-8"), original)
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertEqual(self.sync_hermes("--check").returncode, 0)

    def test_keeps_shared_entry_when_a_foreign_skill_would_be_lost(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        foreign = self.shared / "foreign-skill"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text(skill_md("foreign-skill"), encoding="utf-8")

        result = self.sync_hermes()

        text = self.config.read_text(encoding="utf-8")
        self.assertIn(f"    - {SHARED_ENTRY}\n", text)
        self.assertIn(f"    - {HUB_ENTRY}\n", text)
        self.assertIn(f"kept {SHARED_ENTRY}", result.stderr)
        self.assertIn("foreign-skill (from ~/.agents/skills/foreign-skill)", result.stderr)
        self.assertEqual(self.sync_hermes("--check").returncode, 0)

    def test_keeps_shared_entry_when_excluded_skill_has_no_own_copy(self):
        self.sync_skills()

        result = self.sync_hermes()

        self.assertIn(f"    - {SHARED_ENTRY}\n", self.config.read_text(encoding="utf-8"))
        self.assertIn("withheld (excluded for Hermes; Hermes has no own copy)", result.stderr)
        self.assertIn("still sees Hub Skills withheld from it: withheld", result.stderr)

    def test_keeps_shared_entry_until_hub_skills_is_populated(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        for link in self.hub_skills.iterdir():
            link.unlink()

        result = self.sync_hermes()

        self.assertIn(f"    - {SHARED_ENTRY}\n", self.config.read_text(encoding="utf-8"))
        self.assertIn(f"alpha (not linked in {HUB_ENTRY} yet)", result.stderr)

    def test_foreign_link_is_safe_when_hermes_has_every_skill_it_holds(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        bundle = self.root / "skill-bundle" / "skills"
        for name in ("brainstorming", "writing-plans"):
            (bundle / name).mkdir(parents=True)
            (bundle / name / "SKILL.md").write_text(skill_md(name), encoding="utf-8")
            self.own_skill(self.hermes, name, category="software-development")
        (self.shared / "skill-bundle").symlink_to(bundle)

        self.sync_hermes()
        self.assertNotIn(SHARED_ENTRY, self.config.read_text(encoding="utf-8"))
        self.assertTrue((self.shared / "skill-bundle").is_symlink())  # other tools keep it

    def test_profiles_are_registered_and_checked_against_their_own_skills(self):
        self.own_skill(self.hermes, "withheld")
        profiles = {}
        for name, has_copy in (("keeper", True), ("bare", False)):
            profile = self.hermes / "profiles" / name
            profile.mkdir(parents=True)
            config = profile / "config.yaml"
            config.write_text(
                "skills:\n  external_dirs: [~/.agents/skills]\n", encoding="utf-8"
            )
            if has_copy:
                self.own_skill(profile, "withheld")
            profiles[name] = config
        memory = self.hermes / "profiles" / "bare" / "memories" / "MEMORY.md"
        memory.parent.mkdir()
        memory.write_text("learned\n", encoding="utf-8")
        self.sync_skills()

        result = self.sync_hermes()

        keeper = profiles["keeper"].read_text(encoding="utf-8")
        bare = profiles["bare"].read_text(encoding="utf-8")
        self.assertEqual(keeper, f"skills:\n  external_dirs:\n    - {HUB_ENTRY}\n")
        self.assertIn(f"    - {HUB_ENTRY}\n", bare)
        self.assertIn(f"    - {SHARED_ENTRY}\n", bare)
        self.assertIn("profiles/bare/config.yaml: kept", result.stderr)
        self.assertNotIn(SHARED_ENTRY, self.config.read_text(encoding="utf-8"))
        self.assertEqual(memory.read_text(encoding="utf-8"), "learned\n")
        self.assertEqual(len(self.backups()), 3)

    def test_second_run_changes_nothing(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        self.sync_hermes()
        config_before = self.config.read_bytes()
        links_before = sorted((p.name, os.readlink(p)) for p in self.hub_skills.iterdir())
        backups_before = self.backups()

        self.sync_skills()
        result = self.sync_hermes()

        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(
            sorted((p.name, os.readlink(p)) for p in self.hub_skills.iterdir()), links_before
        )
        self.assertEqual(self.backups(), backups_before)
        self.assertIn(f"✓ {HUB_ENTRY} registered", result.stdout)
        self.assertEqual(self.sync_hermes("--check").returncode, 0)

    def test_dry_run_and_check_write_nothing(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        before = self.config.read_bytes()

        dry = self.sync_hermes("--dry-run")
        check = self.sync_hermes("--check", check=False)

        self.assertIn(f"(dry-run) config.yaml: registered {HUB_ENTRY}", dry.stdout)
        self.assertIn(f"(dry-run) config.yaml: removed {SHARED_ENTRY}", dry.stdout)
        self.assertEqual(check.returncode, 2)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_check_reports_a_pending_safe_migration(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        self.config.write_text(
            f"skills:\n  external_dirs:\n    - {HUB_ENTRY}\n    - {SHARED_ENTRY}\n",
            encoding="utf-8",
        )
        self.assertEqual(self.sync_hermes("--check", check=False).returncode, 2)

    def test_flow_list_entry_is_removed_and_other_entries_kept(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        self.config.write_text(
            "skills:\n  external_dirs: [~/.agents/skills, ~/team-skills] # shared\n",
            encoding="utf-8",
        )

        self.sync_hermes()

        self.assertEqual(
            self.config.read_text(encoding="utf-8"),
            f"skills:\n  external_dirs: # shared\n    - ~/team-skills\n    - {HUB_ENTRY}\n",
        )

    # --- status and verify report Hermes against its own target ---

    def test_status_counts_hermes_against_allowlist_minus_exclusions(self):
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        self.sync_hermes()

        result = self.run_cmd("bash", str(LIST_SYNC))

        self.assertRegex(result.stdout, r"(?m)^Hermes\s+3/3\s+2/2\s")

    def test_verify_checks_hermes_target_and_exclusions(self):
        (self.hub / "mcp").mkdir()
        (self.hub / "mcp" / "shared-servers.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
        self.own_skill(self.hermes, "withheld")
        self.sync_skills()
        self.sync_hermes()

        passing = self.run_cmd("bash", str(VERIFY_ALL), check=False)
        (self.hub_skills / "withheld").symlink_to(self.hub / "skills" / "withheld")
        failing = self.run_cmd("bash", str(VERIFY_ALL), check=False)

        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
        self.assertIn(f"✅ alpha Hub-managed @ {self.hub_skills}", passing.stdout)
        self.assertIn("✅ withheld excluded @ Hermes", passing.stdout)
        self.assertIn("✅ Hermes registers its Hub Skill directory", passing.stdout)
        self.assertIn("❌ withheld excluded @ Hermes (duplicate Hub link)", failing.stdout)
        self.assertNotEqual(failing.returncode, 0)


if __name__ == "__main__":
    unittest.main()
