import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock
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
SAFE_NPM = {
    "dist-tags": {"latest": "0.8.2"},
    "versions": {"0.8.2": {}},
}


class McpDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hub = Path(self.temp_dir.name)
        self.shared_path = self.hub / "mcp" / "shared-servers.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, servers):
        self.shared_path.parent.mkdir(exist_ok=True)
        self.shared_path.write_text(
            json.dumps({"mcpServers": servers}), encoding="utf-8"
        )

    def fetch_json(self, url):
        if url.startswith("https://pypi.org/"):
            return PYPI
        if url.startswith("https://registry.npmjs.org/"):
            return SAFE_NPM
        self.fail(f"unexpected registry URL: {url}")

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

    def test_pin_dry_run_returns_proposal_without_changing_shared_servers(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )
        before = self.shared_path.read_bytes()

        proposed = MCP_DEPS.pin_dependency(
            self.hub,
            "mineru",
            "1.0.22",
            apply=False,
            fetch_json=self.fetch_json,
        )

        self.assertIn('"mineru-open-mcp==1.0.22"', proposed)
        self.assertEqual(self.shared_path.read_bytes(), before)

    def test_pin_apply_changes_only_the_target_dependency_argument(self):
        self.write_config(
            {
                "mineru": {
                    "command": "uvx",
                    "args": ["mineru-open-mcp==1.0.21", "--quiet"],
                    "env": {"EXTRA": "${EXTRA}"},
                }
            }
        )
        before = self.shared_path.read_text(encoding="utf-8")

        written = MCP_DEPS.pin_dependency(
            self.hub,
            "mineru",
            "1.0.22",
            apply=True,
            fetch_json=self.fetch_json,
        )

        after = self.shared_path.read_text(encoding="utf-8")
        self.assertEqual(written, after)
        self.assertEqual(
            after,
            before.replace(
                "mineru-open-mcp==1.0.21", "mineru-open-mcp==1.0.22"
            ),
        )

    def test_pin_apply_preserves_regular_file_mode_and_ownership(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )
        os.chmod(self.shared_path, 0o640)
        before = self.shared_path.stat()

        MCP_DEPS.pin_dependency(
            self.hub,
            "mineru",
            "1.0.22",
            apply=True,
            fetch_json=self.fetch_json,
        )

        after = self.shared_path.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))

    def test_pin_refuses_extended_attributes_without_reading_values(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )
        before = self.shared_path.read_bytes()

        with mock.patch.object(
            MCP_DEPS.os, "listxattr", return_value=["user.untrusted"], create=True
        ):
            with self.assertRaisesRegex(
                DependencyError, "MCP dependency config has unsupported metadata"
            ):
                MCP_DEPS.pin_dependency(
                    self.hub,
                    "mineru",
                    "1.0.22",
                    apply=True,
                    fetch_json=self.fetch_json,
                )

        self.assertEqual(self.shared_path.read_bytes(), before)

    def test_native_xattr_detector_allows_only_provenance(self):
        with mock.patch.object(
            MCP_DEPS.os,
            "listxattr",
            return_value=["com.apple.provenance"],
            create=True,
        ):
            self.assertFalse(MCP_DEPS._has_xattrs(self.shared_path))
        with mock.patch.object(
            MCP_DEPS.os,
            "listxattr",
            return_value=["com.apple.provenance", "user.untrusted"],
            create=True,
        ):
            self.assertTrue(MCP_DEPS._has_xattrs(self.shared_path))

    def test_pin_allows_only_provenance_xattr_in_darwin_production_detector(self):
        if sys.platform != "darwin":
            self.skipTest("requires the Darwin xattr detector")
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )
        names = subprocess.run(
            ["/usr/bin/xattr", str(self.shared_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.decode("utf-8").splitlines()
        if set(names) != {"com.apple.provenance"}:
            self.skipTest("temporary file does not carry only provenance")
        before = self.shared_path.read_bytes()

        proposed = MCP_DEPS.pin_dependency(
            self.hub,
            "mineru",
            "1.0.22",
            apply=False,
            fetch_json=self.fetch_json,
        )
        self.assertIn('"mineru-open-mcp==1.0.22"', proposed)
        self.assertEqual(self.shared_path.read_bytes(), before)

        MCP_DEPS.pin_dependency(
            self.hub,
            "mineru",
            "1.0.22",
            apply=True,
            fetch_json=self.fetch_json,
        )

        self.assertFalse(MCP_DEPS._has_xattrs(self.shared_path))
        self.assertIn(
            b"mineru-open-mcp==1.0.22", self.shared_path.read_bytes()
        )

    def test_pin_refuses_darwin_acl_with_native_xattr_helper(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )

        with mock.patch.object(MCP_DEPS, "_has_xattrs", return_value=False), mock.patch.object(
            MCP_DEPS, "_has_macos_acl", return_value=True
        ), mock.patch.object(
            MCP_DEPS.sys, "platform", "darwin"
        ):
            with self.assertRaisesRegex(
                DependencyError, "MCP dependency config has unsupported metadata"
            ):
                MCP_DEPS.pin_dependency(
                    self.hub,
                    "mineru",
                    "1.0.22",
                    apply=False,
                    fetch_json=self.fetch_json,
                )

    def test_pin_refuses_special_file_mode_bits(self):
        for bit in (stat.S_ISUID, stat.S_ISGID, stat.S_ISVTX):
            with self.subTest(bit=bit):
                self.write_config(
                    {
                        "mineru": {
                            "command": "uvx",
                            "args": ["mineru-open-mcp==1.0.21"],
                        }
                    }
                )
                os.chmod(self.shared_path, 0o640 | bit)
                before = self.shared_path.read_bytes()

                with mock.patch.object(MCP_DEPS, "_has_xattrs", return_value=False):
                    with self.assertRaisesRegex(
                        DependencyError, "MCP dependency config has unsupported metadata"
                    ):
                        MCP_DEPS.pin_dependency(
                            self.hub,
                            "mineru",
                            "1.0.22",
                            apply=True,
                            fetch_json=self.fetch_json,
                        )

                self.assertEqual(self.shared_path.read_bytes(), before)

    def test_pin_sanitizes_write_failures_and_cleans_up_temporary_files(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )
        before = self.shared_path.read_bytes()
        failures = (
            ("fsync", "fsync", OSError("untrusted filesystem detail")),
            ("replace", "replace", OSError("untrusted filesystem detail")),
            ("fchmod", "fchmod", RuntimeError("untrusted filesystem detail")),
        )

        for label, name, error in failures:
            with self.subTest(label=label):
                with mock.patch.object(MCP_DEPS, "_has_xattrs", return_value=False), mock.patch.object(
                    MCP_DEPS.os,
                    name,
                    side_effect=error,
                ):
                    with self.assertRaisesRegex(
                        DependencyError, "^MCP dependency update failed$") as raised:
                        MCP_DEPS.pin_dependency(
                            self.hub,
                            "mineru",
                            "1.0.22",
                            apply=True,
                            fetch_json=self.fetch_json,
                        )

                self.assertNotIn("untrusted filesystem detail", str(raised.exception))
                self.assertEqual(self.shared_path.read_bytes(), before)
                self.assertEqual(
                    list(self.shared_path.parent.glob(".shared-servers.json.*")), []
                )

    def test_pin_sanitizes_config_read_failures(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )

        with mock.patch.object(
            MCP_DEPS.Path,
            "read_bytes",
            side_effect=OSError("untrusted filesystem detail"),
        ):
            with self.assertRaisesRegex(
                DependencyError, "^MCP dependency config is unavailable$"
            ) as raised:
                MCP_DEPS.pin_dependency(
                    self.hub,
                    "mineru",
                    "1.0.22",
                    apply=False,
                    fetch_json=self.fetch_json,
                )

        self.assertNotIn("untrusted filesystem detail", str(raised.exception))

    def test_pin_refuses_file_level_symlinks_for_dry_runs_and_apply(self):
        target = self.hub / "canonical-shared-servers.json"
        target.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "mineru": {
                            "command": "uvx",
                            "args": ["mineru-open-mcp==1.0.21"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.shared_path.parent.mkdir(exist_ok=True)
        self.shared_path.symlink_to(target)
        before = target.read_bytes()

        for apply in (False, True):
            with self.subTest(apply=apply):
                with self.assertRaisesRegex(
                    DependencyError, "MCP dependency config cannot be a symlink"
                ):
                    MCP_DEPS.pin_dependency(
                        self.hub,
                        "mineru",
                        "1.0.22",
                        apply=apply,
                        fetch_json=self.fetch_json,
                    )

                self.assertTrue(self.shared_path.is_symlink())
                self.assertEqual(target.read_bytes(), before)

    def test_pin_apply_preserves_original_newline_and_whitespace_bytes(self):
        for newline in (b"\r\n", b"\r"):
            with self.subTest(newline=newline):
                self.shared_path.parent.mkdir(exist_ok=True)
                before = (
                    b"{" + newline
                    + b"  \"mcpServers\" : {" + newline
                    + b"    \"mineru\" : { \"command\" : \"uvx\", \"args\" : [ \"mineru-open-mcp==1.0.21\" ] }"
                    + newline
                    + b"  }" + newline
                    + b"}\t " + newline
                )
                self.shared_path.write_bytes(before)

                MCP_DEPS.pin_dependency(
                    self.hub,
                    "mineru",
                    "1.0.22",
                    apply=True,
                    fetch_json=self.fetch_json,
                )

                self.assertEqual(
                    self.shared_path.read_bytes(),
                    before.replace(
                        b"mineru-open-mcp==1.0.21", b"mineru-open-mcp==1.0.22"
                    ),
                )

    def test_pin_converts_unversioned_and_latest_dependencies_to_exact_versions(self):
        fixtures = (
            (
                "mineru",
                {"command": "uvx", "args": ["mineru-open-mcp"]},
                "1.0.22",
                '"mineru-open-mcp==1.0.22"',
            ),
            (
                "obsidian",
                {
                    "command": "npx",
                    "args": ["@mauricio.wolff/mcp-obsidian@latest"],
                },
                "0.8.2",
                '"@mauricio.wolff/mcp-obsidian@0.8.2"',
            ),
        )

        for server, config, version, expected in fixtures:
            with self.subTest(server=server):
                self.write_config({server: config})

                proposed = MCP_DEPS.pin_dependency(
                    self.hub,
                    server,
                    version,
                    apply=False,
                    fetch_json=self.fetch_json,
                )

                self.assertIn(expected, proposed)

    def test_pin_refuses_invalid_server_and_target_versions(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )
        invalid_requests = (
            ("unknown", "1.0.22"),
            ("mineru", "mineru-open-mcp==1.0.22"),
            ("mineru", "latest"),
            ("mineru", "1.1.0rc1"),
            ("mineru", "1.1.0-beta.1"),
            ("mineru", "^1.0.22"),
        )

        for server, version in invalid_requests:
            with self.subTest(server=server, version=version):
                with self.assertRaises(DependencyError):
                    MCP_DEPS.pin_dependency(
                        self.hub,
                        server,
                        version,
                        apply=False,
                        fetch_json=self.fetch_json,
                    )

    def test_pin_refuses_missing_or_yanked_pypi_target_versions(self):
        self.write_config(
            {"mineru": {"command": "uvx", "args": ["mineru-open-mcp==1.0.21"]}}
        )
        fixtures = (
            (
                "missing",
                {"info": {"version": "1.0.22"}, "releases": {}},
            ),
            (
                "yanked",
                {
                    "info": {"version": "1.0.22"},
                    "releases": {"1.0.22": [{"yanked": True}]},
                },
            ),
        )

        for label, payload in fixtures:
            with self.subTest(label=label):
                with self.assertRaises(DependencyError):
                    MCP_DEPS.pin_dependency(
                        self.hub,
                        "mineru",
                        "1.0.22",
                        apply=False,
                        fetch_json=lambda _url, value=payload: value,
                    )

    def test_pin_refuses_deprecated_npm_target_versions(self):
        self.write_config(
            {
                "obsidian": {
                    "command": "npx",
                    "args": ["@mauricio.wolff/mcp-obsidian@latest"],
                }
            }
        )
        deprecated_target = {
            "dist-tags": {"latest": "0.8.2"},
            "versions": {"0.8.2": {"deprecated": "renamed"}},
        }

        with self.assertRaises(DependencyError):
            MCP_DEPS.pin_dependency(
                self.hub,
                "obsidian",
                "0.8.2",
                apply=False,
                fetch_json=lambda _url: deprecated_target,
            )

    def test_pin_refuses_when_old_argument_token_is_not_unique(self):
        self.write_config(
            {
                "mineru": {
                    "command": "uvx",
                    "args": ["mineru-open-mcp==1.0.21"],
                },
                "copy": {
                    "command": "uvx",
                    "args": ["mineru-open-mcp==1.0.21"],
                },
            }
        )

        with self.assertRaises(DependencyError):
            MCP_DEPS.pin_dependency(
                self.hub,
                "mineru",
                "1.0.22",
                apply=False,
                fetch_json=self.fetch_json,
            )

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
            (
                "deceptive declared length",
                FakeResponse(
                    b" " * (2 * 1024 * 1024 + 1), content_length=2
                ),
            ),
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

        http_body = TrackingBody(secret.encode())
        http_error = HTTPError(
            "https://registry.npmjs.org/example",
            500,
            secret,
            {},
            http_body,
        )

        def http_opener(_url, timeout):
            raise http_error

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

        self.assertTrue(http_body.closed)
        self.assertFalse(http_body.read_called)


class McpSmokeDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hub = Path(self.temp_dir.name)
        self.shared_path = self.hub / "mcp" / "shared-servers.json"
        self.record_path = self.hub / "fake-child-record.json"
        self.fake_child = self.hub / "fake-mcp-child.py"
        self.fake_child.write_text(
            textwrap.dedent(
                """
                import json
                import os
                import signal
                import sys
                import time
                from pathlib import Path

                record_path = Path(sys.argv[1])
                scenario = sys.argv[2]
                extra_arg = sys.argv[3] if len(sys.argv) > 3 else None
                messages = []

                def receive():
                    line = sys.stdin.readline()
                    if not line:
                        return None
                    message = json.loads(line)
                    messages.append(message)
                    return message

                def send(message):
                    sys.stdout.write(json.dumps(message) + "\\n")
                    sys.stdout.flush()

                initialize = receive()
                if scenario == "timeout":
                    time.sleep(5)
                if scenario == "malformed":
                    sys.stdout.write("not-json PRIVATE-RAW-OUTPUT\\n")
                    sys.stdout.flush()
                    time.sleep(5)
                if scenario == "stdout-cap":
                    sys.stdout.write("PRIVATE-RAW-OUTPUT" * 20000 + "\\n")
                    sys.stdout.flush()
                    time.sleep(5)
                if scenario == "stderr-cap":
                    sys.stderr.write("PRIVATE-RAW-OUTPUT" * 20000)
                    sys.stderr.flush()
                    time.sleep(5)
                if scenario == "notification":
                    send({
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {"progress": 1},
                    })
                response_id = 999 if scenario == "wrong-id" else initialize["id"]
                if scenario == "rpc-error":
                    send({
                        "jsonrpc": "2.0",
                        "id": response_id,
                        "error": {"code": -32000, "message": "PRIVATE-RAW-OUTPUT"},
                    })
                    time.sleep(5)
                protocol = "2024-11-05" if scenario == "protocol-mismatch" else "2025-06-18"
                send({
                    "jsonrpc": "2.0",
                    "id": response_id,
                    "result": {"protocolVersion": protocol, "capabilities": {}},
                })
                initialized = receive()
                tools_request = receive()
                if scenario == "non-list-tools":
                    tools = {"name": "extract"}
                elif scenario == "invalid-tool":
                    tools = [{"name": ""}]
                elif scenario == "unsafe-tool":
                    tools = [{"name": "extract\\nPRIVATE-RAW-OUTPUT"}]
                else:
                    tools = [
                        {"name": "status", "description": "private description"},
                        {"name": "extract"},
                        {"name": "status"},
                    ]
                send({
                    "jsonrpc": "2.0",
                    "id": tools_request["id"],
                    "result": {"tools": tools},
                })
                if scenario == "late-stderr-cap":
                    sys.stderr.write("PRIVATE-RAW-OUTPUT" * 20000)
                    sys.stderr.flush()
                record_path.write_text(json.dumps({
                    "messages": messages,
                    "extra_arg": extra_arg,
                    "fake_env": os.environ.get("FAKE_ENV"),
                }))
                if scenario == "nonzero":
                    raise SystemExit(7)
                if scenario == "hang-after-tools":
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    while True:
                        time.sleep(1)
                """
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, config):
        self.shared_path.parent.mkdir(exist_ok=True)
        self.shared_path.write_text(
            json.dumps({"mcpServers": {"mineru": config}}), encoding="utf-8"
        )

    def fake_config(self, scenario="healthy"):
        return {
            "type": "stdio",
            "command": "${FAKE_COMMAND}",
            "args": ["${FAKE_SCRIPT}", "${RECORD_FILE}", scenario, "${ARG_VALUE}"],
            "env": {"FAKE_ENV": "${ENV_VALUE}"},
        }

    def smoke(self, scenario="healthy", **kwargs):
        self.write_config(self.fake_config(scenario))
        environment = {
            "FAKE_COMMAND": sys.executable,
            "FAKE_SCRIPT": str(self.fake_child),
            "RECORD_FILE": str(self.record_path),
            "ARG_VALUE": "resolved-argument",
            "ENV_VALUE": "resolved-environment",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            return MCP_DEPS.smoke_dependency(
                self.hub, "mineru", timeout=kwargs.pop("timeout", 2), **kwargs
            )

    def test_performs_initialize_notification_and_tool_list_handshake(self):
        result = self.smoke()

        self.assertEqual(
            result,
            {
                "server": "mineru",
                "initialized": True,
                "protocol_version": "2025-06-18",
                "tool_count": 2,
                "tools": ["extract", "status"],
                "status": "healthy",
            },
        )
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [message["method"] for message in record["messages"]],
            ["initialize", "notifications/initialized", "tools/list"],
        )
        self.assertEqual(record["extra_arg"], "resolved-argument")
        self.assertEqual(record["fake_env"], "resolved-environment")
        self.assertEqual(
            record["messages"][0]["params"]["clientInfo"],
            {
                "name": "agent-sync",
                "version": (Path(__file__).parents[1] / "VERSION")
                .read_text(encoding="utf-8")
                .strip(),
            },
        )

    def test_ignores_valid_server_notifications_before_a_response(self):
        result = self.smoke("notification")

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["tools"], ["extract", "status"])

    def test_rejects_protocol_and_response_failures_without_raw_output(self):
        scenarios = (
            "timeout",
            "malformed",
            "wrong-id",
            "rpc-error",
            "protocol-mismatch",
            "non-list-tools",
            "invalid-tool",
            "unsafe-tool",
            "nonzero",
            "stdout-cap",
            "stderr-cap",
            "late-stderr-cap",
        )

        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with self.assertRaises(DependencyError) as raised:
                    self.smoke(scenario, timeout=0.3)

                self.assertNotIn("PRIVATE-RAW-OUTPUT", str(raised.exception))

    def test_rejects_non_stdio_and_unsafe_server_names_before_launch(self):
        launches = []

        def runner(*_args, **_kwargs):
            launches.append(True)
            raise AssertionError("runner must not be called")

        invalid = (
            ("mineru", {"type": "http", "url": "https://example.test/mcp"}),
            ("mineru", {"url": "https://example.test/mcp"}),
            ("../mineru", {"command": sys.executable, "args": []}),
        )

        for server, config in invalid:
            with self.subTest(server=server, config=config):
                self.write_config(config)
                with self.assertRaises(DependencyError):
                    MCP_DEPS.smoke_dependency(
                        self.hub, server, timeout=0.1, runner=runner
                    )

        self.assertEqual(launches, [])

    def test_rejects_unresolved_command_arg_and_env_before_launch(self):
        launches = []

        def runner(*_args, **_kwargs):
            launches.append(True)
            raise AssertionError("runner must not be called")

        fixtures = (
            {"command": "${MISSING_COMMAND}", "args": []},
            {"command": sys.executable, "args": ["${MISSING_ARG}"]},
            {
                "command": sys.executable,
                "args": [],
                "env": {"TOKEN": "${MISSING_ENV}"},
            },
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            for config in fixtures:
                with self.subTest(config=config):
                    self.write_config(config)
                    with self.assertRaises(DependencyError):
                        MCP_DEPS.smoke_dependency(
                            self.hub, "mineru", timeout=0.1, runner=runner
                        )

        self.assertEqual(launches, [])

    def test_rejects_nonfinite_timeouts_before_launch(self):
        launches = []

        def runner(*_args, **_kwargs):
            launches.append(True)
            raise AssertionError("runner must not be called")

        self.write_config({"command": sys.executable, "args": []})

        for timeout in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout):
                with self.assertRaises(DependencyError):
                    MCP_DEPS.smoke_dependency(
                        self.hub, "mineru", timeout=timeout, runner=runner
                    )

        self.assertEqual(launches, [])

    def test_does_not_disclose_environment_or_resolved_values(self):
        secret = "probe-secret-70f2c140"
        config = {
            "command": "${FAKE_COMMAND}",
            "args": ["${FAKE_SCRIPT}", "${RECORD_FILE}", "malformed", secret],
            "env": {"FAKE_ENV": secret},
        }
        self.write_config(config)
        environment = {
            "FAKE_COMMAND": sys.executable,
            "FAKE_SCRIPT": str(self.fake_child),
            "RECORD_FILE": str(self.record_path),
            "UNRELATED_PRIVATE_VALUE": secret,
        }

        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(DependencyError) as raised:
                MCP_DEPS.smoke_dependency(self.hub, "mineru", timeout=0.3)

        message = str(raised.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn("UNRELATED_PRIVATE_VALUE", message)

    def test_uses_allowed_target_donor_placeholders_without_disclosure(self):
        self.write_config(
            {
                "command": sys.executable,
                "args": [str(self.fake_child), str(self.record_path), "healthy", "${DONOR_ARG}"],
                "env": {"FAKE_ENV": "${DONOR_ENV}"},
            }
        )
        policy_path = self.hub / "policies" / "tool-scopes.json"
        policy_path.parent.mkdir(exist_ok=True)
        policy_path.write_text(
            json.dumps(
                {
                    "secrets": {
                        "targets": {"codex": {"allow_secret_donors": True}}
                    }
                }
            ),
            encoding="utf-8",
        )
        helpers = mock.Mock()
        helpers.secret_donors_allowed.return_value = True
        helpers.collect_donors.return_value = {
            "mineru": {
                "env": {"DONOR_ENV": "donor-environment", "DONOR_ARG": "donor-argument"}
            }
        }
        helpers.donor_lookup_map.return_value = {
            "DONOR_ENV": "donor-environment",
            "DONOR_ARG": "donor-argument",
        }
        helpers.resolve_value.side_effect = lambda value, mapping: (
            mapping.get(value[2:-1])
            if isinstance(value, str) and value.startswith("${") and value.endswith("}")
            else value
        )
        helpers.is_placeholder.side_effect = lambda value: (
            isinstance(value, str)
            and value.startswith("${")
            and value.endswith("}")
        )

        with mock.patch.object(MCP_DEPS, "_load_merge_mcp", return_value=helpers):
            result = MCP_DEPS.smoke_dependency(
                self.hub, "mineru", target="codex", timeout=2
            )

        self.assertEqual(result["status"], "healthy")
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["extra_arg"], "donor-argument")
        self.assertEqual(record["fake_env"], "donor-environment")
        helpers.collect_donors.assert_called_once_with()
        helpers.donor_lookup_map.assert_called_once()

    def test_bounds_graceful_shutdown_then_terminates_and_kills_process_group(self):
        started = time.monotonic()

        result = self.smoke("hang-after-tools", timeout=0.3)

        self.assertEqual(result["status"], "healthy")
        self.assertLess(time.monotonic() - started, 2)


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


class TrackingBody(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.read_called = False

    def read(self, size=-1):
        self.read_called = True
        return super().read(size)


if __name__ == "__main__":
    unittest.main()
