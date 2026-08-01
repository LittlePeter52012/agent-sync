import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "mcp-deps.py"
SPEC = importlib.util.spec_from_file_location("mcp_deps", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load MCP dependency parser")
MCP_DEPS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MCP_DEPS
SPEC.loader.exec_module(MCP_DEPS)

DependencyError = MCP_DEPS.DependencyError
discover_dependencies = MCP_DEPS.discover_dependencies


PYPI = {
    "info": {
        "version": "1.0.22",
        "project_urls": {"Source": "https://github.com/example/mineru"},
    },
    "releases": {"1.0.22": [{"yanked": False}]},
}
NPM = {
    "dist-tags": {"latest": "0.8.2"},
    "repository": {"url": "https://github.com/example/obsidian.git"},
    "versions": {
        "0.8.2": {"deprecated": "Renamed to @bitbonsai/mcpvault"}
    },
}


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
            ("npx", "--token=private-value-9f70b1ee05a34d9b8c2f"),
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

    def test_classifies_primary_registry_versions_and_deprecation(self):
        self.write_config(
            {
                "mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]},
                "obsidian": {
                    "command": "npx",
                    "args": ["@mauricio.wolff/mcp-obsidian@0.8.2"],
                },
            }
        )
        seen = []

        def fetch_json(url):
            seen.append(url)
            if url == "https://pypi.org/pypi/mineru-open-mcp/json":
                return PYPI
            if url == (
                "https://registry.npmjs.org/"
                "%40mauricio.wolff%2Fmcp-obsidian"
            ):
                return NPM
            self.fail(f"unexpected registry URL: {url}")

        results = MCP_DEPS.check_dependencies(
            discover_dependencies(self.hub), fetch_json
        )

        self.assertEqual(
            [item["status"] for item in results],
            ["update-available", "deprecated"],
        )
        self.assertEqual(
            seen,
            [
                "https://pypi.org/pypi/mineru-open-mcp/json",
                "https://registry.npmjs.org/%40mauricio.wolff%2Fmcp-obsidian",
            ],
        )
        self.assertEqual(
            set(results[0]),
            {
                "server",
                "ecosystem",
                "package",
                "current",
                "latest",
                "pin_state",
                "status",
                "deprecated",
                "metadata_url",
            },
        )
        self.assertEqual(results[0]["current"], "1.0.21")
        self.assertEqual(results[0]["latest"], "1.0.22")
        self.assertFalse(results[0]["deprecated"])
        self.assertTrue(results[1]["deprecated"])

    def test_registry_failures_and_invalid_latest_versions_are_unknown(self):
        spec = MCP_DEPS.DependencySpec(
            "example", "pypi", "example-package", "1.0.0", "exact", 0
        )
        fixtures = (
            ("registry error", URLError("token=registry-secret")),
            (
                "missing latest",
                {"info": {}, "releases": {}},
            ),
            (
                "prerelease latest",
                {
                    "info": {"version": "1.1.0rc1"},
                    "releases": {"1.1.0rc1": [{"yanked": False}]},
                },
            ),
            (
                "all files yanked",
                {
                    "info": {"version": "1.0.1"},
                    "releases": {
                        "1.0.1": [{"yanked": True}, {"yanked": True}]
                    },
                },
            ),
        )

        for label, fixture in fixtures:
            with self.subTest(label=label):
                def fetch_json(_url, value=fixture):
                    if isinstance(value, BaseException):
                        raise value
                    return value

                [result] = MCP_DEPS.check_dependencies([spec], fetch_json)

                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["latest"])
                self.assertNotIn("registry-secret", json.dumps(result))

    def test_unpinned_entries_use_latest_without_aborting(self):
        self.write_config(
            {
                "mineru": {"command": "uvx", "args": ["mineru-open-mcp"]},
                "obsidian": {
                    "command": "npx",
                    "args": ["@mauricio.wolff/mcp-obsidian@latest"],
                },
            }
        )

        def fetch_json(url):
            return PYPI if url.startswith("https://pypi.org/") else NPM

        results = MCP_DEPS.check_dependencies(
            discover_dependencies(self.hub), fetch_json
        )

        self.assertEqual(results[0]["status"], "unpinned")
        self.assertIsNone(results[0]["current"])
        self.assertEqual(results[0]["latest"], "1.0.22")
        self.assertEqual(results[1]["status"], "deprecated")
        self.assertIsNone(results[1]["current"])

    def test_compares_numeric_versions_without_lexical_or_width_errors(self):
        fixtures = (
            ("equal widths", "1.0", "1.0.0", "current"),
            ("reverse equal widths", "1.0.0", "1.0", "current"),
            ("local newer", "1.10.0", "1.9.9", "ahead"),
        )

        for label, current, latest, expected in fixtures:
            with self.subTest(label=label):
                spec = MCP_DEPS.DependencySpec(
                    "example", "npm", "example-package", current, "exact", 0
                )
                payload = {
                    "dist-tags": {"latest": latest},
                    "versions": {latest: {}},
                }

                [result] = MCP_DEPS.check_dependencies(
                    [spec], lambda _url: payload
                )

                self.assertEqual(result["status"], expected)

    def test_strips_registry_control_characters_before_returning_fields(self):
        version = "1.0.\x001"
        spec = MCP_DEPS.DependencySpec(
            "example", "npm", "exam\nple", version, "exact", 0
        )
        payload = {
            "dist-tags": {"latest": version},
            "versions": {version: {}},
        }

        [result] = MCP_DEPS.check_dependencies([spec], lambda _url: payload)

        self.assertEqual(result["package"], "example")
        self.assertEqual(result["current"], "1.0.1")
        self.assertEqual(result["latest"], "1.0.1")
        self.assertEqual(result["status"], "current")
        self.assertEqual(
            result["metadata_url"], "https://registry.npmjs.org/example"
        )

    def test_default_fetcher_uses_timeout_and_closes_response(self):
        response = FakeResponse(b'{"info": {"version": "1.0.0"}}')

        def opener(url, timeout):
            self.assertEqual(url, "https://pypi.org/pypi/example/json")
            self.assertEqual(timeout, 8)
            return response

        payload = MCP_DEPS.default_fetch_json(
            "https://pypi.org/pypi/example/json", opener=opener
        )

        self.assertEqual(payload, {"info": {"version": "1.0.0"}})
        self.assertTrue(response.closed)

    def test_default_fetcher_rejects_declared_and_streamed_oversize_responses(self):
        fixtures = (
            (
                "declared",
                FakeResponse(b"{}", content_length=2 * 1024 * 1024 + 1),
            ),
            ("streamed", FakeResponse(b" " * (2 * 1024 * 1024 + 1))),
        )

        for label, response in fixtures:
            with self.subTest(label=label):
                with self.assertRaises(DependencyError) as raised:
                    MCP_DEPS.default_fetch_json(
                        "https://registry.npmjs.org/example",
                        opener=lambda _url, timeout: response,
                    )

                self.assertEqual(str(raised.exception), "registry response rejected")
                self.assertTrue(response.closed)

    def test_default_fetcher_sanitizes_timeout_http_and_malformed_json_errors(self):
        secret = "registry-token-1f5d8cb3"

        def timeout_opener(_url, timeout):
            raise TimeoutError(secret)

        def http_opener(url, timeout):
            raise HTTPError(url, 500, secret, {}, io.BytesIO(secret.encode()))

        malformed = FakeResponse(f'{{"secret": "{secret}"'.encode())
        fixtures = (
            ("timeout", timeout_opener),
            ("http", http_opener),
            ("malformed", lambda _url, timeout: malformed),
            (
                "read error",
                lambda _url, timeout: ReadErrorResponse(secret),
            ),
        )

        for label, opener in fixtures:
            with self.subTest(label=label):
                with self.assertRaises(DependencyError) as raised:
                    MCP_DEPS.default_fetch_json(
                        "https://registry.npmjs.org/example", opener=opener
                    )

                message = str(raised.exception)
                self.assertNotIn(secret, message)
                self.assertIn(
                    message,
                    {"registry request failed", "invalid registry response"},
                )


class FakeResponse(io.BytesIO):
    def __init__(self, payload, content_length=None):
        super().__init__(payload)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)


class ReadErrorResponse(FakeResponse):
    def __init__(self, message):
        super().__init__(b"")
        self.message = message

    def read(self, _size=-1):
        raise OSError(self.message)


if __name__ == "__main__":
    unittest.main()
