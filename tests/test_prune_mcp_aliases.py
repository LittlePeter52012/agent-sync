import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PRUNE = REPO / "scripts" / "prune-mcp-aliases.py"


class PruneMcpAliasesTests(unittest.TestCase):
    def test_duplicate_alias_deep_fills_only_missing_canonical_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "shared.json"
            canonical.write_text(
                json.dumps(
                    {"mcpServers": {"shared": {"command": "/bin/sh"}}}
                ),
                encoding="utf-8",
            )
            target = root / "settings.json"
            target.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "shared": {
                                "command": "/bin/sh",
                                "env": {
                                    "TOKEN": "${TOKEN}",
                                    "CANONICAL": "wins",
                                },
                                "args": ["fixed", ""],
                            },
                            "SHARED": {
                                "command": "/bin/false",
                                "env": {
                                    "TOKEN": "concrete-marker",
                                    "CANONICAL": "loses",
                                    "ALIAS_ONLY": "kept",
                                },
                                "args": ["other", "filled", "appended"],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            subprocess.run(
                ["python3", str(PRUNE), str(canonical), str(target)],
                cwd=REPO,
                text=True,
                capture_output=True,
                check=True,
            )

            servers = json.loads(target.read_text())["mcpServers"]
            self.assertNotIn("SHARED", servers)
            shared = servers["shared"]
            self.assertEqual(shared["command"], "/bin/sh")
            self.assertEqual(shared["env"]["TOKEN"], "concrete-marker")
            self.assertEqual(shared["env"]["CANONICAL"], "wins")
            self.assertEqual(shared["env"]["ALIAS_ONLY"], "kept")
            self.assertEqual(shared["args"], ["fixed", "filled", "appended"])


if __name__ == "__main__":
    unittest.main()
