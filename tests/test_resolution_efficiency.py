from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
VERIFY = REPO / "scripts" / "verify-all.sh"
LIST_SYNC = REPO / "scripts" / "list-sync.sh"


class ResolutionEfficiencyTests(unittest.TestCase):
    def test_verify_resolves_capability_policy_once_per_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hub = root / "hub"
            log = root / "codex.log"
            (hub / "mcp").mkdir(parents=True)
            (hub / "skills" / "alpha").mkdir(parents=True)
            (hub / "skills" / "beta").mkdir(parents=True)
            for skill in ("alpha", "beta"):
                (hub / "skills" / skill / "SKILL.md").write_text(
                    f"# {skill}\n", encoding="utf-8"
                )
            (hub / "manifest.yaml").write_text(
                "math_skills:\n  - alpha\n  - beta\n", encoding="utf-8"
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
            policy = hub / "policies" / "tool-scopes.json"
            policy.parent.mkdir()
            policy.write_text(
                json.dumps(
                    {
                        "capability_ownership": {
                            "defaults": {"selection": "auto"},
                            "capabilities": {
                                "search": {
                                    "hub": {"skills": [], "mcp": []},
                                    "targets": {
                                        "codex": {
                                            "official_plugins": ["exa@market"],
                                            "selection": "auto",
                                        }
                                    },
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            codex = root / "codex"
            codex.write_text(
                "#!/bin/sh\n"
                'printf "call\\n" >> "$CODEX_LOG"\n'
                'printf \'{"installed": []}\\n\'\n',
                encoding="utf-8",
            )
            codex.chmod(0o755)
            env = os.environ | {
                "HOME": str(home),
                "AGENT_HUB_ROOT": str(hub),
                "CODEX_LOG": str(log),
                "PATH": f"{root}:{os.environ.get('PATH', '')}",
            }

            result = subprocess.run(
                ["bash", str(VERIFY)],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("alpha @ hub", result.stdout)
            self.assertIn("beta @ hub", result.stdout)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 1, calls)

            log.write_text("", encoding="utf-8")
            status = subprocess.run(
                ["bash", str(LIST_SYNC)],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 1, calls)


if __name__ == "__main__":
    unittest.main()
