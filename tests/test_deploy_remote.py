import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "scripts" / "deploy-remote.sh"
ENTRYPOINT = REPO / "bin" / "agent-sync"


class DeployRemoteTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.tool = root / "tool"
        self.hub = root / "hub"
        self.log = root / "ssh.log"
        self.ssh = root / "fake-ssh"
        self.ssh.write_text(
            "#!/bin/sh\n"
            'printf "CALL\\n" >> "$SSH_LOG"\n'
            'case "$*" in\n'
            '  *"tar -xf -"*) printf "ARCHIVE\\n" >> "$SSH_LOG" ;;\n'
            '  *"bash -s"*) printf "ACTIVATE\\n" >> "$SSH_LOG" ;;\n'
            "esac\n"
            "cat >/dev/null\n"
            'printf "remote preflight ok\\n"\n',
            encoding="utf-8",
        )
        self.ssh.chmod(0o755)
        for repo, filename in (
            (self.tool, "VERSION"),
            (self.hub, "manifest.yaml"),
        ):
            repo.mkdir()
            (repo / filename).write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "add", filename], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Agent Sync Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "init",
                ],
                cwd=repo,
                check=True,
            )
        self.env = os.environ | {
            "AGENT_SYNC_HOME": str(self.tool),
            "AGENT_HUB_ROOT": str(self.hub),
            "AGENT_SYNC_SSH_BIN": str(self.ssh),
            "SSH_LOG": str(self.log),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def run_deploy(self, *args):
        return subprocess.run(
            ["bash", str(DEPLOY), *args],
            env=self.env,
            text=True,
            capture_output=True,
        )

    def test_default_is_read_only_preflight(self):
        result = self.run_deploy("mac-mini")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Preflight only", result.stdout)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls.count("CALL"), 1)
        self.assertNotIn("ARCHIVE", calls)

    def test_apply_streams_committed_archives_and_activation_script(self):
        result = self.run_deploy("mac-mini", "--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Remote deployment completed", result.stdout)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls.count("CALL"), 3)
        self.assertEqual(calls.count("ARCHIVE"), 2)
        self.assertEqual(calls.count("ACTIVATE"), 1)

    def test_apply_refuses_dirty_source_before_ssh_mutation(self):
        (self.hub / "manifest.yaml").write_text("dirty\n", encoding="utf-8")

        result = self.run_deploy("mac-mini", "--apply")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertFalse(self.log.exists())

    def test_entrypoint_dispatches_deploy(self):
        result = subprocess.run(
            [str(ENTRYPOINT), "deploy", "bad host"],
            env=self.env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid SSH host", result.stderr)


if __name__ == "__main__":
    unittest.main()
