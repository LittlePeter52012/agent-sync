import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SYNC_HERMES = REPO / "scripts" / "sync_hermes.py"
ENTRYPOINT = REPO / "bin" / "agent-sync"
LIST_SYNC = REPO / "scripts" / "list-sync.sh"


class SyncHermesTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home"
        self.home.mkdir()
        self.config = self.home / ".hermes" / "config.yaml"
        self.config.parent.mkdir()
        self.external = self.home / ".agents" / "skills"
        self.external.mkdir(parents=True)
        self.hub = Path(self.tempdir.name) / "hub"
        self.hub.mkdir()
        (self.hub / "manifest.yaml").write_text(
            "skills:\n",
            encoding="utf-8",
        )
        self.env = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_HOME": str(REPO),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def run_sync(self, *args, check=True):
        return subprocess.run(
            ["python3", str(SYNC_HERMES), *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=check,
        )

    def test_adds_agents_skills_to_empty_external_dirs(self):
        self.config.write_text(
            "model:\n  default: example\nskills:\n  external_dirs: []\n",
            encoding="utf-8",
        )

        self.run_sync()

        text = self.config.read_text(encoding="utf-8")
        self.assertIn("  external_dirs:\n    - ~/.agents/skills\n", text)
        self.assertIn("model:\n  default: example\n", text)
        self.assertEqual(self.run_sync("--check").returncode, 0)

    def test_preserves_existing_external_dirs_and_is_idempotent(self):
        self.config.write_text(
            "skills:\n"
            "  external_dirs:\n"
            "    - ~/shared/team-skills\n"
            "  template_vars: true\n",
            encoding="utf-8",
        )

        self.run_sync()
        once = self.config.read_text(encoding="utf-8")
        self.run_sync()
        twice = self.config.read_text(encoding="utf-8")

        self.assertIn("    - ~/shared/team-skills\n", once)
        self.assertIn("    - ~/.agents/skills\n", once)
        self.assertEqual(once, twice)

    def test_normalizes_inline_flow_list_without_creating_nested_list(self):
        self.config.write_text(
            "skills:\n"
            "  external_dirs: [~/shared/one, '~/shared/two']\n"
            "  template_vars: true\n",
            encoding="utf-8",
        )

        self.run_sync()

        text = self.config.read_text(encoding="utf-8")
        self.assertIn("    - ~/shared/one\n", text)
        self.assertIn("    - '~/shared/two'\n", text)
        self.assertIn("    - ~/.agents/skills\n", text)
        self.assertNotIn("- [~/shared/one", text)
        self.assertEqual(self.run_sync("--check").returncode, 0)

    def test_preserves_comment_on_empty_inline_list(self):
        self.config.write_text(
            "skills:\n"
            "  external_dirs: [] # managed by Hermes\n"
            "  template_vars: true\n",
            encoding="utf-8",
        )

        self.run_sync()

        text = self.config.read_text(encoding="utf-8")
        self.assertIn(
            "  external_dirs: # managed by Hermes\n"
            "    - ~/.agents/skills\n",
            text,
        )
        self.assertNotIn("    - []", text)

    def test_preserves_four_space_skills_child_indentation(self):
        self.config.write_text(
            "skills:\n"
            "    external_dirs:\n"
            "        - ~/shared/team-skills\n"
            "    template_vars: true\n",
            encoding="utf-8",
        )

        self.run_sync()

        text = self.config.read_text(encoding="utf-8")
        self.assertEqual(text.count("external_dirs:"), 1)
        self.assertIn("        - ~/shared/team-skills\n", text)
        self.assertIn("        - ~/.agents/skills\n", text)
        self.assertEqual(self.run_sync("--check").returncode, 0)

    def test_check_reports_drift_without_writing(self):
        self.config.write_text(
            "skills:\n  external_dirs: []\n",
            encoding="utf-8",
        )
        before = self.config.read_text(encoding="utf-8")

        result = self.run_sync("--check", check=False)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.config.read_text(encoding="utf-8"), before)

    def test_entrypoint_dispatches_hermes_sync(self):
        self.config.write_text(
            "skills:\n  external_dirs: []\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [str(ENTRYPOINT), "hermes"],
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("registered ~/.agents/skills", result.stdout)
        self.assertIn(
            "    - ~/.agents/skills\n",
            self.config.read_text(encoding="utf-8"),
        )

    def test_registers_shared_skills_for_each_hermes_profile(self):
        self.config.write_text(
            "skills:\n  external_dirs:\n    - ~/.agents/skills\n",
            encoding="utf-8",
        )
        profile = self.config.parent / "profiles" / "angelina"
        profile.mkdir(parents=True)
        profile_config = profile / "config.yaml"
        profile_config.write_text(
            "model:\n  provider: example\n"
            "skills:\n  external_dirs: []\n",
            encoding="utf-8",
        )
        memory = profile / "memories" / "MEMORY.md"
        memory.parent.mkdir()
        memory.write_text("private learned experience\n", encoding="utf-8")

        self.run_sync()

        self.assertIn(
            "    - ~/.agents/skills\n",
            profile_config.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "model:\n  provider: example\n",
            profile_config.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            memory.read_text(encoding="utf-8"),
            "private learned experience\n",
        )
        self.assertEqual(self.run_sync("--check").returncode, 0)

    def test_check_reports_profile_drift_without_writing_any_config(self):
        self.config.write_text(
            "skills:\n  external_dirs:\n    - ~/.agents/skills\n",
            encoding="utf-8",
        )
        profile = self.config.parent / "profiles" / "angelina"
        profile.mkdir(parents=True)
        profile_config = profile / "config.yaml"
        profile_config.write_text(
            "skills:\n  external_dirs: []\n",
            encoding="utf-8",
        )
        default_before = self.config.read_bytes()
        profile_before = profile_config.read_bytes()

        result = self.run_sync("--check", check=False)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.config.read_bytes(), default_before)
        self.assertEqual(profile_config.read_bytes(), profile_before)

    def test_list_honors_custom_hermes_home(self):
        custom_home = self.home / "custom-hermes"
        custom_home.mkdir()
        (custom_home / "config.yaml").write_text(
            "skills:\n  external_dirs:\n    - ~/.agents/skills\n",
            encoding="utf-8",
        )
        env = self.env | {"HERMES_HOME": str(custom_home)}

        result = subprocess.run(
            ["bash", str(LIST_SYNC)],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertRegex(result.stdout, r"(?m)^Hermes\s")


if __name__ == "__main__":
    unittest.main()
