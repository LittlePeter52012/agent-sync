import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "mcp-deps.py"
SPEC = importlib.util.spec_from_file_location("mcp_deps", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load MCP dependency parser")
MCP_DEPS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MCP_DEPS
SPEC.loader.exec_module(MCP_DEPS)

DependencyError = MCP_DEPS.DependencyError
discover_dependencies = MCP_DEPS.discover_dependencies


class McpDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hub = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, servers):
        config = self.hub / "mcp" / "shared-servers.json"
        config.parent.mkdir(exist_ok=True)
        config.write_text(
            json.dumps({"mcpServers": servers}), encoding="utf-8"
        )

    def test_discovers_exact_npm_and_pypi_pins(self):
        self.write_config(
            {
                "mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]},
                "obsidian": {
                    "command": "npx",
                    "args": [
                        "@mauricio.wolff/mcp-obsidian@0.8.2",
                        "${OBSIDIAN_VAULT}",
                    ],
                },
                "local": {"command": "/bin/local", "args": []},
            }
        )

        specs = discover_dependencies(self.hub)

        self.assertEqual(
            [
                (item.server, item.ecosystem, item.package, item.version, item.pin_state)
                for item in specs
            ],
            [
                ("mineru", "pypi", "mineru-open-mcp", "1.0.21", "exact"),
                ("obsidian", "npm", "@mauricio.wolff/mcp-obsidian", "0.8.2", "exact"),
            ],
        )

    def test_reports_unversioned_uvx_package(self):
        self.write_config({"mineru": {"command": "uvx", "args": ["mineru-open-mcp"]}})

        [spec] = discover_dependencies(self.hub)

        self.assertEqual(
            (spec.ecosystem, spec.package, spec.version, spec.pin_state),
            ("pypi", "mineru-open-mcp", None, "unversioned"),
        )

    def test_reports_latest_scoped_npm_package(self):
        self.write_config(
            {
                "obsidian": {
                    "command": "npx",
                    "args": ["@mauricio.wolff/mcp-obsidian@latest"],
                }
            }
        )

        [spec] = discover_dependencies(self.hub)

        self.assertEqual(
            (spec.ecosystem, spec.package, spec.version, spec.pin_state),
            ("npm", "@mauricio.wolff/mcp-obsidian", None, "latest"),
        )

    def test_rejects_malformed_recognized_launch_arguments(self):
        for command, argument in (
            ("npx", "--yes"),
            ("npx", "@"),
            ("npx", "mcp-tool@^1.0.0"),
            ("npx", "mcp-tool@1.0.0-rc.1"),
            ("uvx", "--from"),
            ("uvx", "mcp-tool>=1.0"),
            ("uvx", "mcp-tool==1.0.0-rc.1"),
            ("uvx", "mcp-tool==~=1.0"),
        ):
            with self.subTest(command=command, argument=argument):
                self.write_config({"broken": {"command": command, "args": [argument]}})

                with self.assertRaises(DependencyError):
                    discover_dependencies(self.hub)

    def test_redacts_malformed_dependency_arguments(self):
        for command, argument in (
            ("npx", "--token=ghp_9f70b1ee05a34d9b8c2f"),
            ("uvx", "mcp-tool==1.0.0-${PYPI_PRIVATE_TOKEN}"),
        ):
            with self.subTest(command=command):
                self.write_config({"private-server": {"command": command, "args": [argument]}})

                with self.assertRaises(DependencyError) as raised:
                    discover_dependencies(self.hub)

                message = str(raised.exception)
                self.assertNotIn(argument, message)
                self.assertEqual(
                    message,
                    f"private-server: invalid {command} dependency argument at position 0",
                )

    def test_ignores_local_executables(self):
        self.write_config({"local": {"command": "/bin/local", "args": ["--version"]}})

        self.assertEqual(discover_dependencies(self.hub), [])


if __name__ == "__main__":
    unittest.main()
