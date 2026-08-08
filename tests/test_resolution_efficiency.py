from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
VERIFY = REPO / "scripts" / "verify-all.sh"


class ResolutionEfficiencyTests(unittest.TestCase):
    def test_verify_resolves_capability_policy_once_per_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hub = root / "hub"
            log = root / "python.log"
            (hub / "mcp").mkdir(parents=True)
            (hub / "skills" / "alpha").mkdir(parents=True)
            (hub / "skills" / "beta").mkdir(parents=True)
            for skill in ("alpha", "beta"):
                (hub / "skills" / skill / "SKILL.md").write_text(
                    f"# {skill}\n", encoding="utf-8"
                )
            (hub / "manifest.yaml").write_text(
                "skills:\n  - alpha\n  - beta\n", encoding="utf-8"
            )
            (hub / "mcp" / "shared-servers.json").write_text(
                json.dumps({"mcpServers": {}}), encoding="utf-8"
            )
            bases = (
                home / ".gemini/config/skills",
                home / ".gemini/antigravity/skills",
                home / ".gemini/antigravity-cli/skills",
                home / ".gemini/antigravity-ide/skills",
                home / ".claude/skills",
                home / ".cursor/skills",
                home / ".codex/skills",
                home / ".config/opencode/skills",
                home / ".copilot/skills",
                home / ".agents/skills",
            )
            for base in bases:
                base.mkdir(parents=True)
                for skill in ("alpha", "beta"):
                    (base / skill).symlink_to(hub / "skills" / skill)
            wrapper = root / "python-wrapper"
            wrapper.write_text(
                "#!/bin/sh\n"
                'case "$1" in *capability_resolver.py) printf "call\\n" >> "$PY_LOG" ;; esac\n'
                'exec python3 "$@"\n',
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            env = os.environ | {
                "HOME": str(home),
                "AGENT_HUB_ROOT": str(hub),
                "PYTHON": str(wrapper),
                "PY_LOG": str(log),
            }

            result = subprocess.run(
                ["bash", str(VERIFY)],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), len(bases), calls)


if __name__ == "__main__":
    unittest.main()
