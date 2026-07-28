import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE_REPO = Path("/Users/slavatang/.local/share/agent-sync")


class UpdateToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.tool = self.root / "tool"
        self.home = self.root / "home"
        self.home.mkdir()

        shutil.copytree(
            SOURCE_REPO,
            self.seed,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        self.git("-C", self.seed, "init", "-b", "main")
        self.git("-C", self.seed, "add", ".")
        self.git(
            "-C",
            self.seed,
            "-c",
            "user.name=Agent Sync Test",
            "-c",
            "user.email=agent-sync-test@example.invalid",
            "commit",
            "-m",
            "seed",
        )
        self.git("init", "--bare", self.origin)
        self.git("-C", self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git("-C", self.seed, "remote", "add", "origin", str(self.origin))
        self.git("-C", self.seed, "push", "-u", "origin", "main")
        self.git("clone", str(self.origin), self.tool)

    def tearDown(self):
        self.tempdir.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *map(str, args)],
            text=True,
            capture_output=True,
            check=True,
        )

    def run_update(self, *args):
        env = os.environ | {"HOME": str(self.home)}
        return subprocess.run(
            [str(self.tool / "bin" / "agent-sync"), "update", *args],
            cwd=self.tool,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_update_without_options_runs_on_up_to_date_repo(self):
        result = self.run_update()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already up to date.", result.stdout)

    def test_update_check_treats_local_ahead_as_up_to_date(self):
        (self.tool / "local-policy.txt").write_text("local policy\n")
        self.git("-C", self.tool, "add", "local-policy.txt")
        self.git(
            "-C",
            self.tool,
            "-c",
            "user.name=Agent Sync Test",
            "-c",
            "user.email=agent-sync-test@example.invalid",
            "commit",
            "-m",
            "local policy",
        )

        result = self.run_update("--check")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Everything up to date.", result.stdout)
        self.assertNotIn("update available:", result.stdout)


if __name__ == "__main__":
    unittest.main()
