import importlib.util
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "mcp-deps.py"
REPO = Path(__file__).parents[1]
ENTRYPOINT = REPO / "bin" / "agent-sync"
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


class McpDependencyCliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        self.hub = root / "hub"
        self.home.mkdir()
        (self.hub / "mcp").mkdir(parents=True)
        (self.hub / "manifest.yaml").write_text("skills: []\n", encoding="utf-8")
        self.shared_path = self.hub / "mcp" / "shared-servers.json"
        self.shared_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "mineru": {
                            "command": "uvx",
                            "args": ["mineru-open-mcp==1.0.21"],
                            "env": {"SAFE_VALUE": "PRIVATE-HUB-VALUE"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.cache_path = self.home / ".cache" / "agent-sync" / "mcp-deps.json"
        self.fetch_calls = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def fetch_json(self, url):
        self.fetch_calls.append(url)
        return {
            "info": {
                "version": "1.0.22",
                "api_token": "PRIVATE-REGISTRY-VALUE",
            },
            "releases": {"1.0.22": [{"yanked": False}]},
        }

    def safe_cached_update(self, checked_at=None):
        return {
            "checked_at": time.time() if checked_at is None else checked_at,
            "dependencies": [
                {
                    "server": "mineru",
                    "ecosystem": "pypi",
                    "package": "mineru-open-mcp",
                    "current": "1.0.21",
                    "latest": "1.0.22",
                    "pin_state": "exact",
                    "status": "update-available",
                    "deprecated": False,
                    "metadata_url": "https://pypi.org/pypi/mineru-open-mcp/json",
                }
            ],
        }

    def write_cache(self, payload):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_path.parent, 0o700)
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(self.cache_path, 0o600)

    def write_raw_cache(self, payload):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_path.parent, 0o700)
        self.cache_path.write_bytes(payload)
        os.chmod(self.cache_path, 0o600)

    def invoke_main(self, *args, fetch_json=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
        }
        with mock.patch.dict(os.environ, environment, clear=False), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            rc = MCP_DEPS.main(
                list(args),
                fetch_json=self.fetch_json if fetch_json is None else fetch_json,
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    def run_entrypoint(self, *args, cache_payload=None, timeout=None):
        if cache_payload is not None:
            self.write_cache(cache_payload)
        environment = os.environ | {
            "HOME": str(self.home),
            "AGENT_HUB_ROOT": str(self.hub),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }
        return subprocess.run(
            [str(ENTRYPOINT), *args],
            cwd=REPO,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    def initialize_clean_hub(self):
        subprocess.run(["git", "init", "-q", str(self.hub)], check=True)
        subprocess.run(
            ["git", "-C", str(self.hub), "config", "user.name", "Test User"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.hub),
                "config",
                "user.email",
                "test@example.invalid",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.hub), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.hub), "commit", "-q", "-m", "initial"],
            check=True,
        )

    def assert_cache_miss_refreshes(self, payload, *, raw=False):
        if raw:
            self.write_raw_cache(payload)
        else:
            self.write_cache(payload)
        calls_before = len(self.fetch_calls)

        rc, stdout, stderr = self.invoke_main("check", "--json")

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(len(self.fetch_calls), calls_before + 1)
        self.assertIn("dependencies", json.loads(stdout))
        self.assertNotIn("PRIVATE-CACHE-VALUE", stdout + stderr)

    def test_check_json_fetches_and_caches_only_safe_dependency_records(self):
        rc, stdout, stderr = self.invoke_main("check", "--json")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("dependencies", json.loads(stdout))
        self.assertEqual(len(self.fetch_calls), 1)
        cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(set(cache), {"checked_at", "dependencies"})
        self.assertEqual(cache["dependencies"], json.loads(stdout)["dependencies"])
        self.assertEqual(stat.S_IMODE(self.cache_path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.cache_path.stat().st_mode), 0o600)
        serialized = stdout + stderr + json.dumps(cache)
        self.assertNotIn("PRIVATE-HUB-VALUE", serialized)
        self.assertNotIn("PRIVATE-REGISTRY-VALUE", serialized)

    def test_check_reuses_fresh_cache_and_refresh_forces_registry_fetch(self):
        first = self.invoke_main("check", "--json")
        second = self.invoke_main("check", "--json")
        refreshed = self.invoke_main("check", "--json", "--refresh")

        self.assertEqual([first[0], second[0], refreshed[0]], [0, 0, 0])
        self.assertEqual(len(self.fetch_calls), 2)
        self.assertEqual(
            json.loads(first[1])["dependencies"],
            json.loads(second[1])["dependencies"],
        )

    def test_check_rejects_stale_malformed_unknown_and_sensitive_cache_fields(self):
        unsafe_payloads = [
            self.safe_cached_update(time.time() - 86401),
            {"checked_at": "recent", "dependencies": []},
            self.safe_cached_update() | {"unexpected": "PRIVATE-CACHE-VALUE"},
        ]
        for term in ("token", "secret", "password", "header", "oauth", "vault", "path"):
            unsafe_payloads.append(
                self.safe_cached_update()
                | {f"unsafe_{term}_field": "PRIVATE-CACHE-VALUE"}
            )

        for payload in unsafe_payloads:
            with self.subTest(keys=sorted(payload)):
                self.write_cache(payload)
                calls_before = len(self.fetch_calls)

                rc, stdout, stderr = self.invoke_main("check", "--json")

                self.assertEqual(rc, 0, stderr)
                self.assertEqual(len(self.fetch_calls), calls_before + 1)
                self.assertIn("dependencies", json.loads(stdout))
                self.assertNotIn("PRIVATE-CACHE-VALUE", stdout + stderr)

    def test_cache_ttl_accepts_exact_boundary_and_rejects_stale_or_future(self):
        now = 1000000.0
        boundary = self.safe_cached_update(now - 86400)
        self.write_cache(boundary)
        with mock.patch.object(MCP_DEPS.time, "time", return_value=now):
            rc, stdout, stderr = self.invoke_main("check", "--cached", "--quiet")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("MCP dependency update available", stderr)

        for checked_at in (now - 86400.001, now + 0.001):
            with self.subTest(checked_at=checked_at):
                self.write_cache(self.safe_cached_update(checked_at))
                with mock.patch.object(MCP_DEPS.time, "time", return_value=now):
                    rc, stdout, stderr = self.invoke_main(
                        "check", "--cached", "--quiet"
                    )
                self.assertEqual(rc, 0)
                self.assertEqual(stdout + stderr, "")

    def test_cache_rejects_duplicate_json_keys_and_oversized_or_nonfinite_numbers(self):
        checked_at = time.time()
        record = json.dumps(self.safe_cached_update()["dependencies"][0])
        duplicate = (
            '{"checked_at":'
            f"{checked_at},"
            '"checked_at":'
            f"{checked_at},"
            f'"dependencies":[{record}]}}'
        ).encode("utf-8")
        huge_integer = (
            b'{"checked_at":'
            + (b"9" * 5000)
            + b',"dependencies":[]}'
        )
        for label, payload in (
            ("duplicate key", duplicate),
            ("huge integer", huge_integer),
            ("NaN", b'{"checked_at":NaN,"dependencies":[]}'),
            ("Infinity", b'{"checked_at":Infinity,"dependencies":[]}'),
        ):
            with self.subTest(label=label):
                self.assert_cache_miss_refreshes(payload, raw=True)

    def test_cache_rejects_oversized_bytes_and_version_components_silently(self):
        oversized_document = json.dumps(self.safe_cached_update()).encode("utf-8")
        oversized_document += b" " * (MCP_DEPS._MAX_CACHE_BYTES + 1)
        self.assert_cache_miss_refreshes(oversized_document, raw=True)

        oversized_version = self.safe_cached_update()
        oversized_version["dependencies"][0]["latest"] = "1." + ("9" * 5000)
        self.write_cache(oversized_version)

        rc, stdout, stderr = self.invoke_main("check", "--cached", "--quiet")

        self.assertEqual(rc, 0)
        self.assertEqual(stdout + stderr, "")

    def test_cache_rejects_impossible_dependency_records_and_nested_sensitive_keys(self):
        fixtures = []
        exact_without_current = self.safe_cached_update()
        exact_without_current["dependencies"][0].update(
            {"current": None, "latest": None, "status": "unknown"}
        )
        fixtures.append(exact_without_current)
        for pin_state in ("latest", "unversioned"):
            invalid = self.safe_cached_update()
            invalid["dependencies"][0].update(
                {"pin_state": pin_state, "status": "unpinned"}
            )
            fixtures.append(invalid)
        deprecated_without_latest = self.safe_cached_update()
        deprecated_without_latest["dependencies"][0].update(
            {"latest": None, "deprecated": True, "status": "unknown"}
        )
        fixtures.append(deprecated_without_latest)
        deprecated_pypi = self.safe_cached_update()
        deprecated_pypi["dependencies"][0].update(
            {"deprecated": True, "status": "deprecated"}
        )
        fixtures.append(deprecated_pypi)
        nested_sensitive = self.safe_cached_update()
        nested_sensitive["dependencies"][0]["current"] = {
            "items": [{"OAuthHeader": "PRIVATE-CACHE-VALUE"}]
        }
        fixtures.append(nested_sensitive)

        for payload in fixtures:
            with self.subTest(record=payload["dependencies"][0]):
                self.assert_cache_miss_refreshes(payload)

    def test_cache_rejects_duplicate_servers_and_package_version_pairs(self):
        duplicate_server = self.safe_cached_update()
        duplicate_server["dependencies"].append(
            dict(duplicate_server["dependencies"][0])
        )
        duplicate_pair = self.safe_cached_update()
        second = dict(duplicate_pair["dependencies"][0])
        second["server"] = "mineru-copy"
        duplicate_pair["dependencies"].append(second)

        for payload in (duplicate_server, duplicate_pair):
            with self.subTest(servers=[item["server"] for item in payload["dependencies"]]):
                self.assert_cache_miss_refreshes(payload)

    def test_empty_dependency_cache_is_valid_and_does_not_fetch(self):
        self.write_cache({"checked_at": time.time(), "dependencies": []})

        rc, stdout, stderr = self.invoke_main(
            "check",
            "--json",
            fetch_json=lambda _url: self.fail("valid empty cache must not fetch"),
        )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(json.loads(stdout), {"dependencies": []})

    def test_quiet_advisory_deduplicates_each_package_version_pair(self):
        first = self.safe_cached_update()["dependencies"][0]
        second = dict(first)
        second["server"] = "mineru-copy"
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            MCP_DEPS._print_dependency_check(
                [first, second], as_json=False, quiet=True
            )

        self.assertEqual(
            stderr.getvalue(),
            "MCP dependency update available: mineru/mineru-open-mcp 1.0.21 -> 1.0.22\n",
        )

    def test_cached_quiet_advises_from_safe_cache_without_fetching(self):
        self.write_cache(self.safe_cached_update())

        rc, stdout, stderr = self.invoke_main(
            "check",
            "--cached",
            "--quiet",
            fetch_json=lambda _url: self.fail("cached check must not fetch"),
        )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "MCP dependency update available: mineru/mineru-open-mcp 1.0.21 -> 1.0.22\n",
        )

    def test_cached_quiet_ignores_invalid_cache_without_leaking_or_fetching(self):
        self.write_cache(
            self.safe_cached_update()
            | {"access_token": "PRIVATE-CACHE-VALUE"}
        )

        rc, stdout, stderr = self.invoke_main(
            "check",
            "--cached",
            "--quiet",
            fetch_json=lambda _url: self.fail("cached check must not fetch"),
        )

        self.assertEqual(rc, 0)
        self.assertEqual(stdout + stderr, "")

    def test_cached_quiet_rejects_cache_file_symlinks(self):
        target = self.home / "outside-cache.json"
        target.write_text(json.dumps(self.safe_cached_update()), encoding="utf-8")
        os.chmod(target, 0o600)
        self.cache_path.parent.mkdir(parents=True)
        os.chmod(self.cache_path.parent, 0o700)
        self.cache_path.symlink_to(target)

        rc, stdout, stderr = self.invoke_main("check", "--cached", "--quiet")

        self.assertEqual(rc, 0)
        self.assertEqual(stdout + stderr, "")

    def test_cached_quiet_rejects_symlinked_cache_directory(self):
        cache_root = self.cache_path.parent.parent
        cache_root.mkdir(parents=True)
        target = self.home / "outside-cache-directory"
        target.mkdir(mode=0o700)
        target_cache = target / "mcp-deps.json"
        target_cache.write_text(
            json.dumps(self.safe_cached_update()), encoding="utf-8"
        )
        os.chmod(target_cache, 0o600)
        self.cache_path.parent.symlink_to(target, target_is_directory=True)

        rc, stdout, stderr = self.invoke_main("check", "--cached", "--quiet")

        self.assertEqual(rc, 0)
        self.assertEqual(stdout + stderr, "")

    def test_cached_quiet_rejects_unsafe_directory_and_file_modes(self):
        fixtures = ((0o777, 0o600), (0o700, 0o644))
        for directory_mode, file_mode in fixtures:
            with self.subTest(directory_mode=directory_mode, file_mode=file_mode):
                self.write_cache(self.safe_cached_update())
                os.chmod(self.cache_path.parent, directory_mode)
                os.chmod(self.cache_path, file_mode)

                rc, stdout, stderr = self.invoke_main(
                    "check", "--cached", "--quiet"
                )

                self.assertEqual(rc, 0)
                self.assertEqual(stdout + stderr, "")

    def test_check_accepts_owned_existing_cache_directory_not_writable_by_others(self):
        self.cache_path.parent.mkdir(parents=True, mode=0o755)
        os.chmod(self.cache_path.parent, 0o755)

        rc, stdout, stderr = self.invoke_main("check", "--json", "--refresh")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("dependencies", json.loads(stdout))
        self.assertEqual(stat.S_IMODE(self.cache_path.stat().st_mode), 0o600)

    def test_cached_quiet_rejects_hardlinked_cache_file(self):
        self.write_cache(self.safe_cached_update())
        os.link(self.cache_path, self.cache_path.with_name("cache-hardlink"))

        rc, stdout, stderr = self.invoke_main("check", "--cached", "--quiet")

        self.assertEqual(rc, 0)
        self.assertEqual(stdout + stderr, "")

    def test_cached_quiet_rejects_cache_not_owned_by_effective_user(self):
        self.write_cache(self.safe_cached_update())

        with mock.patch.object(MCP_DEPS.os, "getuid", return_value=os.getuid() + 1):
            rc, stdout, stderr = self.invoke_main(
                "check", "--cached", "--quiet"
            )

        self.assertEqual(rc, 0)
        self.assertEqual(stdout + stderr, "")

    def test_cached_quiet_does_not_block_on_fifo(self):
        self.cache_path.parent.mkdir(parents=True)
        os.chmod(self.cache_path.parent, 0o700)
        os.mkfifo(self.cache_path, 0o600)

        result = self.run_entrypoint(
            "deps", "check", "--cached", "--quiet", timeout=1
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_cached_quiet_does_not_block_on_fifo_cache_directory(self):
        cache_root = self.cache_path.parent.parent
        cache_root.mkdir(parents=True)
        os.mkfifo(self.cache_path.parent, 0o700)

        result = self.run_entrypoint(
            "deps", "check", "--cached", "--quiet", timeout=1
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_cached_quiet_rejects_device_descriptor_and_closes_it(self):
        self.write_cache(self.safe_cached_update())
        real_open = os.open
        cache_file_descriptors = []

        def open_device(path, flags, mode=0o777, *, dir_fd=None):
            if path == "mcp-deps.json" and dir_fd is not None:
                descriptor = real_open("/dev/null", os.O_RDONLY | os.O_NONBLOCK)
                cache_file_descriptors.append(descriptor)
                return descriptor
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(MCP_DEPS.os, "open", side_effect=open_device):
            rc, stdout, stderr = self.invoke_main(
                "check", "--cached", "--quiet"
            )

        self.assertEqual(rc, 0)
        self.assertEqual(stdout + stderr, "")
        self.assertTrue(cache_file_descriptors)
        for descriptor in cache_file_descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_cache_read_closes_valid_directory_and_file_descriptors(self):
        self.write_cache(self.safe_cached_update())
        real_open = os.open
        opened = []

        def recording_open(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            opened.append(descriptor)
            return descriptor

        with mock.patch.object(MCP_DEPS.os, "open", side_effect=recording_open):
            rc, stdout, stderr = self.invoke_main(
                "check", "--cached", "--quiet"
            )

        self.assertEqual(rc, 0, stderr)
        self.assertIn("MCP dependency update available", stderr)
        self.assertEqual(len(opened), 2)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_cache_read_uses_validated_directory_descriptor_after_path_swap(self):
        self.write_cache(self.safe_cached_update())
        original_directory = self.cache_path.parent
        held_directory = original_directory.with_name("agent-sync-held")
        real_open_cache_dir = MCP_DEPS._open_cache_dir
        swapped = False

        def open_then_swap(*, create):
            nonlocal swapped
            descriptor = real_open_cache_dir(create=create)
            if descriptor is not None and not swapped:
                original_directory.rename(held_directory)
                original_directory.mkdir(mode=0o700)
                replacement = self.safe_cached_update()
                replacement["dependencies"][0]["latest"] = "9.9.9"
                replacement["dependencies"][0]["status"] = "update-available"
                replacement_path = original_directory / "mcp-deps.json"
                replacement_path.write_text(json.dumps(replacement), encoding="utf-8")
                os.chmod(replacement_path, 0o600)
                swapped = True
            return descriptor

        with mock.patch.object(
            MCP_DEPS, "_open_cache_dir", side_effect=open_then_swap
        ):
            rc, stdout, stderr = self.invoke_main(
                "check", "--cached", "--quiet"
            )

        self.assertEqual(rc, 0, stderr)
        self.assertIn("1.0.21 -> 1.0.22", stderr)
        self.assertNotIn("9.9.9", stdout + stderr)

    def test_cache_write_uses_validated_directory_descriptor_after_path_swap(self):
        self.cache_path.parent.mkdir(parents=True)
        os.chmod(self.cache_path.parent, 0o700)
        original_directory = self.cache_path.parent
        held_directory = original_directory.with_name("agent-sync-held")
        real_open_cache_dir = MCP_DEPS._open_cache_dir
        swapped = False

        def open_then_swap(*, create):
            nonlocal swapped
            descriptor = real_open_cache_dir(create=create)
            if descriptor is not None and create and not swapped:
                original_directory.rename(held_directory)
                original_directory.mkdir(mode=0o700)
                swapped = True
            return descriptor

        with mock.patch.object(
            MCP_DEPS, "_open_cache_dir", side_effect=open_then_swap
        ):
            rc, stdout, stderr = self.invoke_main("check", "--json", "--refresh")

        self.assertEqual(rc, 0, stderr)
        self.assertFalse((original_directory / "mcp-deps.json").exists())
        written = json.loads(
            (held_directory / "mcp-deps.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written["dependencies"][0]["latest"], "1.0.22")

    def test_concurrent_cache_writers_leave_one_complete_safe_record(self):
        first = self.safe_cached_update()
        second = self.safe_cached_update()
        second["dependencies"][0].update(
            {"current": "1.0.22", "latest": "1.0.22", "status": "current"}
        )
        errors = []

        def write(payload):
            try:
                MCP_DEPS._write_cache(payload["dependencies"], payload["checked_at"])
            except Exception as error:
                errors.append(error)

        environment = {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        with mock.patch.dict(os.environ, environment, clear=False):
            threads = [
                threading.Thread(target=write, args=(payload,))
                for payload in (first, second) * 10
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            cached = MCP_DEPS._read_cache()

        self.assertEqual(errors, [])
        self.assertIn(cached, (first, second))
        self.assertEqual(list(self.cache_path.parent.glob(".mcp-deps.json.*")), [])

    def test_concurrent_cache_readers_never_observe_partial_atomic_writes(self):
        first = self.safe_cached_update()
        second = self.safe_cached_update()
        second["dependencies"][0].update(
            {"current": "1.0.22", "latest": "1.0.22", "status": "current"}
        )
        observations = []
        errors = []

        def write_many():
            try:
                for payload in (first, second) * 20:
                    MCP_DEPS._write_cache(
                        payload["dependencies"], payload["checked_at"]
                    )
            except Exception as error:
                errors.append(error)

        def read_many():
            try:
                for _attempt in range(80):
                    observations.append(MCP_DEPS._read_cache())
            except Exception as error:
                errors.append(error)

        environment = {"HOME": str(self.home), "AGENT_HUB_ROOT": str(self.hub)}
        with mock.patch.dict(os.environ, environment, clear=False):
            MCP_DEPS._write_cache(first["dependencies"], first["checked_at"])
            threads = [threading.Thread(target=write_many)] + [
                threading.Thread(target=read_many) for _attempt in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        self.assertTrue(observations)
        self.assertTrue(
            all(item is None or item in (first, second) for item in observations)
        )
        self.assertTrue(any(item in (first, second) for item in observations))

    def test_cache_update_is_atomic_and_sanitizes_replace_failures(self):
        secret = "PRIVATE-REPLACE-FAILURE"
        with mock.patch.object(MCP_DEPS.os, "replace", side_effect=OSError(secret)):
            rc, stdout, stderr = self.invoke_main("check", "--json")

        self.assertNotEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertNotIn(secret, stderr)
        self.assertFalse(self.cache_path.exists())
        self.assertEqual(list(self.cache_path.parent.glob(".mcp-deps.json.*")), [])

    def test_pin_dry_run_allows_dirty_hub_and_prints_only_requirement_change(self):
        self.initialize_clean_hub()
        (self.hub / "dirty-marker").write_text("dirty\n", encoding="utf-8")
        before = self.shared_path.read_bytes()

        rc, stdout, stderr = self.invoke_main(
            "pin", "mineru", "1.0.22", "--dry-run"
        )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(
            stdout,
            "mineru: mineru-open-mcp==1.0.21 -> mineru-open-mcp==1.0.22\n",
        )
        self.assertNotIn("PRIVATE-HUB-VALUE", stdout + stderr)
        self.assertNotIn("mcpServers", stdout + stderr)
        self.assertEqual(self.shared_path.read_bytes(), before)

    def test_pin_apply_refuses_dirty_hub_before_registry_or_mutation(self):
        self.initialize_clean_hub()
        (self.hub / "dirty-marker").write_text("dirty\n", encoding="utf-8")
        before = self.shared_path.read_bytes()

        rc, stdout, stderr = self.invoke_main(
            "pin",
            "mineru",
            "1.0.22",
            "--apply",
            fetch_json=lambda _url: self.fail("dirty apply must not fetch"),
        )

        self.assertNotEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertIn("clean git worktree", stderr)
        self.assertEqual(self.shared_path.read_bytes(), before)

    def test_pin_apply_rechecks_clean_hub_after_registry_fetch(self):
        self.initialize_clean_hub()
        before = self.shared_path.read_bytes()

        def dirty_during_fetch(url):
            (self.hub / "concurrent-unrelated-file").write_text(
                "dirty\n", encoding="utf-8"
            )
            return self.fetch_json(url)

        rc, stdout, stderr = self.invoke_main(
            "pin",
            "mineru",
            "1.0.22",
            "--apply",
            fetch_json=dirty_during_fetch,
        )

        self.assertNotEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertIn("clean git worktree", stderr)
        self.assertEqual(self.shared_path.read_bytes(), before)

    def test_pin_apply_retains_target_identity_check_during_registry_fetch(self):
        before = self.shared_path.read_bytes()
        concurrent = before.replace(b"PRIVATE-HUB-VALUE", b"CONCURRENT-VALUE")

        def replace_target(url):
            self.shared_path.write_bytes(concurrent)
            return self.fetch_json(url)

        with self.assertRaisesRegex(
            DependencyError, "MCP dependency config changed during pinning"
        ):
            MCP_DEPS.pin_dependency(
                self.hub,
                "mineru",
                "1.0.22",
                apply=True,
                fetch_json=replace_target,
            )

        self.assertEqual(self.shared_path.read_bytes(), concurrent)

    def test_pin_apply_changes_a_clean_hub_and_keeps_output_secret_free(self):
        self.initialize_clean_hub()

        rc, stdout, stderr = self.invoke_main("pin", "mineru", "1.0.22", "--apply")

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(
            stdout,
            "mineru: mineru-open-mcp==1.0.21 -> mineru-open-mcp==1.0.22\n",
        )
        self.assertNotIn("PRIVATE-HUB-VALUE", stdout + stderr)
        self.assertIn("mineru-open-mcp==1.0.22", self.shared_path.read_text())

    def test_shell_dispatches_deps_without_exposing_registry_injection(self):
        checked = self.run_entrypoint(
            "deps", "check", "--json", cache_payload=self.safe_cached_update()
        )
        bad_pin = self.run_entrypoint("deps", "pin", "mineru", "latest", "--apply")

        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("dependencies", json.loads(checked.stdout))
        self.assertNotEqual(bad_pin.returncode, 0)

    def test_deps_help_and_sanitized_usage_errors_use_conventional_exit_codes(self):
        marker = "PRIVATE-CLI-SECRET"
        help_result = self.run_entrypoint("deps", "--help")
        usage_errors = (
            self.run_entrypoint("deps"),
            self.run_entrypoint("deps", "pin"),
            self.run_entrypoint(
                "deps",
                "pin",
                "mineru",
                "1.0.22",
                "--dry-run",
                "--apply",
            ),
            self.run_entrypoint(
                "deps", "smoke", "mineru", "--timeout", marker
            ),
            self.run_entrypoint("deps", "check", f"--{marker}"),
            self.run_entrypoint("deps", marker),
        )
        operational_error = self.run_entrypoint(
            "deps", "pin", "mineru", marker, "--dry-run"
        )

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("usage:", help_result.stdout)
        self.assertEqual(help_result.stderr, "")
        for result in usage_errors:
            with self.subTest(args=result.args):
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid arguments", result.stderr)
                self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(operational_error.returncode, 1)
        self.assertNotIn(
            marker, operational_error.stdout + operational_error.stderr
        )

    def test_only_all_prints_cached_dependency_advisory_without_mutating_hub(self):
        self.shared_path.write_text('{"mcpServers": {}}', encoding="utf-8")
        before = self.shared_path.read_bytes()

        sync = self.run_entrypoint(
            "sync", cache_payload=self.safe_cached_update()
        )
        all_run = self.run_entrypoint(
            "all", cache_payload=self.safe_cached_update()
        )

        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        self.assertNotIn(
            "MCP dependency update available", sync.stdout + sync.stderr
        )
        self.assertEqual(all_run.returncode, 0, all_run.stdout + all_run.stderr)
        self.assertIn("MCP dependency update available", all_run.stderr)
        self.assertEqual(self.shared_path.read_bytes(), before)

    def test_all_does_not_block_on_fifo_dependency_cache(self):
        self.shared_path.write_text('{"mcpServers": {}}', encoding="utf-8")
        self.cache_path.parent.mkdir(parents=True)
        os.chmod(self.cache_path.parent, 0o700)
        os.mkfifo(self.cache_path, 0o600)
        before = self.shared_path.read_bytes()

        result = self.run_entrypoint("all", timeout=10)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(
            "MCP dependency update available", result.stdout + result.stderr
        )
        self.assertEqual(self.shared_path.read_bytes(), before)


class RecordedInput(io.BytesIO):
    def __init__(self, events, close_failures=0):
        super().__init__()
        self.events = events
        self.close_failures = close_failures

    def close(self):
        if not self.closed:
            self.events.append("close-stdin")
        if self.close_failures:
            self.close_failures -= 1
            raise OSError("PRIVATE-CLOSE-OUTPUT")
        super().close()


class ScriptedProcess:
    def __init__(
        self,
        events,
        wait_results,
        *,
        terminate_error=None,
        kill_error=None,
        stdin_close_failures=0,
    ):
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "scripted", "version": "1.0"},
            },
        }
        tools = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": "status"}]},
        }
        stdout = (json.dumps(initialize) + "\n" + json.dumps(tools) + "\n").encode()
        self.events = events
        self.wait_results = list(wait_results)
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.pid = 424242
        self.stdin = RecordedInput(events, stdin_close_failures)
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO()

    def wait(self, timeout):
        self.events.append("wait")
        result = self.wait_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def terminate(self):
        self.events.append("terminate")
        if self.terminate_error is not None:
            raise self.terminate_error

    def kill(self):
        self.events.append("kill")
        if self.kill_error is not None:
            raise self.kill_error


class BlockingStream:
    def __init__(self):
        self.closed = False
        self.released = threading.Event()

    def read(self, _size=-1):
        self.released.wait(2)
        return b""

    def close(self):
        self.closed = True
        self.released.set()


class SetupFailureProcess:
    def __init__(self, events):
        self.events = events
        self.pid = 434343
        self.stdin = RecordedInput(events)
        self.stdout = BlockingStream()
        self.stderr = BlockingStream()
        self.wait_count = 0
        self.reaped = False

    def wait(self, timeout):
        self.events.append("wait")
        self.wait_count += 1
        if self.wait_count == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        self.reaped = True
        return 0

    def terminate(self):
        self.events.append("terminate")

    def kill(self):
        self.events.append("kill")


class StartFailureThread:
    name = "mcp-smoke-reader-stderr"

    def start(self):
        raise RuntimeError("PRIVATE-THREAD-START-OUTPUT")

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


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

                def persist():
                    record_path.write_text(json.dumps({
                        "messages": messages,
                        "extra_arg": extra_arg,
                        "fake_env": os.environ.get("FAKE_ENV"),
                    }))

                def receive():
                    line = sys.stdin.readline()
                    if not line:
                        return None
                    message = json.loads(line)
                    messages.append(message)
                    persist()
                    return message

                def send(message):
                    sys.stdout.write(json.dumps(message) + "\\n")
                    sys.stdout.flush()

                def send_fragmented(message):
                    payload = (json.dumps(message, ensure_ascii=False) + "\\n").encode()
                    marker = "伪".encode()
                    split_at = payload.index(marker) + 1
                    sys.stdout.buffer.write(payload[:split_at])
                    sys.stdout.buffer.flush()
                    time.sleep(0.02)
                    sys.stdout.buffer.write(payload[split_at:])
                    sys.stdout.buffer.flush()

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
                if scenario == "invalid-notification":
                    send({
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": [],
                    })
                if scenario == "infinity-notification":
                    sys.stdout.write(
                        '{"jsonrpc":"2.0","method":"notifications/progress",'
                        '"params":{"progress":Infinity}}\\n'
                    )
                    sys.stdout.flush()
                if scenario == "server-ping":
                    send({
                        "jsonrpc": "2.0",
                        "id": "server-ping-1",
                        "method": "ping",
                        "params": {},
                    })
                    ping_response = receive()
                if scenario == "unsupported-server-request":
                    send({
                        "jsonrpc": "2.0",
                        "id": "server-tools-call",
                        "method": "tools/call",
                        "params": {"name": "status", "arguments": {}},
                    })
                    unsupported_response = receive()
                response_id = 999 if scenario == "wrong-id" else initialize["id"]
                if scenario == "boolean-id":
                    response_id = True
                if scenario == "rpc-error":
                    send({
                        "jsonrpc": "2.0",
                        "id": response_id,
                        "error": {"code": -32000, "message": "PRIVATE-RAW-OUTPUT"},
                    })
                    time.sleep(5)
                protocol = "2024-11-05" if scenario == "protocol-mismatch" else "2025-06-18"
                capabilities = {"tools": {"listChanged": True}}
                server_info = {"name": "fake-mcp", "version": "1.0"}
                if scenario == "missing-capabilities":
                    capabilities = None
                elif scenario == "malformed-capabilities":
                    capabilities = []
                elif scenario == "missing-tools-capability":
                    capabilities = {}
                elif scenario == "malformed-tools-capability":
                    capabilities = {"tools": []}
                elif scenario == "malformed-tools-flags":
                    capabilities = {"tools": {"listChanged": 1}}
                elif scenario == "missing-server-info":
                    server_info = None
                elif scenario == "malformed-server-info":
                    server_info = {"name": "", "version": 1}
                elif scenario == "whitespace-server-info":
                    server_info = {"name": "   ", "version": "1.0"}
                initialize_result = {
                    "protocolVersion": protocol,
                    "capabilities": capabilities,
                    "serverInfo": server_info,
                }
                if scenario == "nan-initialize":
                    initialize_result["extension"] = float("nan")
                if scenario == "fragmented-utf8":
                    initialize_result["serverInfo"]["name"] = "伪服务"
                if capabilities is None:
                    initialize_result.pop("capabilities")
                if server_info is None:
                    initialize_result.pop("serverInfo")
                initialize_response = {
                    "jsonrpc": "2.0",
                    "id": response_id,
                    "result": initialize_result,
                }
                if scenario == "fragmented-utf8":
                    send_fragmented(initialize_response)
                elif scenario == "coalesced-frames":
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {"progress": 1},
                    }
                    payload = (
                        json.dumps(notification)
                        + "\\n"
                        + json.dumps(initialize_response)
                        + "\\n"
                    )
                    sys.stdout.write(payload)
                    sys.stdout.flush()
                else:
                    send(initialize_response)
                initialized = receive()
                tools_request = receive()
                next_cursor = None
                if scenario == "non-list-tools":
                    tools = {"name": "extract"}
                elif scenario == "invalid-tool":
                    tools = [{"name": ""}]
                elif scenario == "unsafe-tool":
                    tools = [{"name": "extract\\nPRIVATE-RAW-OUTPUT"}]
                elif scenario in (
                    "paginated",
                    "repeated-cursor",
                    "pagination-timeout",
                    "pagination-output-cap",
                    "opaque-empty-cursor",
                    "opaque-control-cursor",
                    "opaque-unicode-cursor",
                ):
                    description = (
                        "x" * 140000
                        if scenario == "pagination-output-cap"
                        else None
                    )
                    tools = [
                        {"name": "one", "description": description},
                        {"name": "status"},
                    ]
                    if scenario == "opaque-empty-cursor":
                        next_cursor = ""
                    elif scenario == "opaque-control-cursor":
                        next_cursor = "\\x00\\n"
                    elif scenario == "opaque-unicode-cursor":
                        next_cursor = "页/二"
                    else:
                        next_cursor = "opaque-page-2"
                elif scenario == "malformed-cursor":
                    tools = [{"name": "one"}]
                    next_cursor = 7
                elif scenario == "negative-infinity-tools":
                    tools = [{"name": "status", "weight": float("-inf")}]
                else:
                    tools = [
                        {"name": "status", "description": "private description"},
                        {"name": "extract"},
                        {"name": "status"},
                    ]
                tools_result = {"tools": tools}
                if next_cursor is not None:
                    tools_result["nextCursor"] = next_cursor
                send({
                    "jsonrpc": "2.0",
                    "id": tools_request["id"],
                    "result": tools_result,
                })
                if scenario in (
                    "paginated",
                    "repeated-cursor",
                    "pagination-timeout",
                    "pagination-output-cap",
                    "opaque-empty-cursor",
                    "opaque-control-cursor",
                    "opaque-unicode-cursor",
                ):
                    next_tools_request = receive()
                    if scenario == "pagination-timeout":
                        time.sleep(5)
                    next_tools_result = {
                        "tools": [
                            {
                                "name": "extract",
                                "description": (
                                    "x" * 140000
                                    if scenario == "pagination-output-cap"
                                    else None
                                ),
                            },
                            {"name": "one"},
                        ],
                    }
                    if scenario == "repeated-cursor":
                        next_tools_result["nextCursor"] = "opaque-page-2"
                    send({
                        "jsonrpc": "2.0",
                        "id": next_tools_request["id"],
                        "result": next_tools_result,
                    })
                if scenario == "late-stderr-cap":
                    sys.stderr.write("PRIVATE-RAW-OUTPUT" * 20000)
                    sys.stderr.flush()
                persist()
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

    def test_cli_smoke_reports_only_the_safe_health_summary(self):
        self.write_config(self.fake_config())
        environment = {
            "FAKE_COMMAND": sys.executable,
            "FAKE_SCRIPT": str(self.fake_child),
            "RECORD_FILE": str(self.record_path),
            "ARG_VALUE": "resolved-argument",
            "ENV_VALUE": "resolved-environment",
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch.dict(os.environ, environment, clear=False), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            rc = MCP_DEPS.main(
                [
                    "smoke",
                    "mineru",
                    "--hub",
                    str(self.hub),
                    "--timeout",
                    "2",
                ]
            )

        self.assertEqual(rc, 0, stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "mineru: healthy (2 tools)\n")
        self.assertNotIn("private description", stdout.getvalue() + stderr.getvalue())
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

    def test_replies_only_to_server_ping_requests(self):
        result = self.smoke("server-ping")

        self.assertEqual(result["status"], "healthy")
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        ping_responses = [
            message
            for message in record["messages"]
            if message.get("id") == "server-ping-1" and "method" not in message
        ]
        self.assertEqual(
            ping_responses,
            [{"jsonrpc": "2.0", "id": "server-ping-1", "result": {}}],
        )
        self.assertNotIn(
            "tools/call",
            [message.get("method") for message in record["messages"]],
        )

    def test_rejects_boolean_response_ids_and_malformed_notifications(self):
        for scenario in ("boolean-id", "invalid-notification"):
            with self.subTest(scenario=scenario):
                with self.assertRaises(DependencyError):
                    self.smoke(scenario, timeout=0.3)

    def test_rejects_server_requests_other_than_ping_without_responding(self):
        with self.assertRaises(DependencyError):
            self.smoke("unsupported-server-request", timeout=0.3)

        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertNotIn(
            "server-tools-call",
            [
                message.get("id")
                for message in record["messages"]
                if "method" not in message
            ],
        )

    def test_handles_fragmented_utf8_and_coalesced_stdio_frames(self):
        for scenario in ("fragmented-utf8", "coalesced-frames"):
            with self.subTest(scenario=scenario):
                result = self.smoke(scenario)

                self.assertEqual(result["status"], "healthy")
                self.assertEqual(result["tools"], ["extract", "status"])

    def test_rejects_nonstandard_json_numeric_constants(self):
        for scenario in (
            "nan-initialize",
            "infinity-notification",
            "negative-infinity-tools",
        ):
            with self.subTest(scenario=scenario):
                with self.assertRaises(DependencyError) as raised:
                    self.smoke(scenario, timeout=0.3)

                message = str(raised.exception)
                self.assertNotIn("NaN", message)
                self.assertNotIn("Infinity", message)

    def test_requires_complete_tool_capable_initialization_before_operation(self):
        scenarios = (
            "missing-capabilities",
            "malformed-capabilities",
            "missing-tools-capability",
            "malformed-tools-capability",
            "malformed-tools-flags",
            "missing-server-info",
            "malformed-server-info",
            "whitespace-server-info",
        )

        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with self.assertRaises(DependencyError):
                    self.smoke(scenario, timeout=0.3)

                record = json.loads(self.record_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    [message["method"] for message in record["messages"]],
                    ["initialize"],
                )

    def test_follows_opaque_tool_list_cursors_and_aggregates_all_pages(self):
        result = self.smoke("paginated")

        self.assertEqual(result["tool_count"], 3)
        self.assertEqual(result["tools"], ["extract", "one", "status"])
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [message["method"] for message in record["messages"]],
            [
                "initialize",
                "notifications/initialized",
                "tools/list",
                "tools/list",
            ],
        )
        self.assertEqual(
            [
                message.get("id")
                for message in record["messages"]
                if message["method"] == "tools/list"
            ],
            [2, 3],
        )
        self.assertEqual(
            record["messages"][3]["params"], {"cursor": "opaque-page-2"}
        )
        self.assertNotIn(
            "tools/call", [message["method"] for message in record["messages"]]
        )

    def test_rejects_malformed_and_repeated_tool_list_cursors(self):
        for scenario in ("malformed-cursor", "repeated-cursor"):
            with self.subTest(scenario=scenario):
                with self.assertRaises(DependencyError):
                    self.smoke(scenario, timeout=0.3)
                if scenario == "repeated-cursor":
                    record = json.loads(
                        self.record_path.read_text(encoding="utf-8")
                    )
                    self.assertEqual(
                        [
                            message["method"]
                            for message in record["messages"]
                            if message["method"] == "tools/list"
                        ],
                        ["tools/list", "tools/list"],
                    )

    def test_pagination_shares_one_deadline_and_output_budget(self):
        for scenario in ("pagination-timeout", "pagination-output-cap"):
            with self.subTest(scenario=scenario):
                started = time.monotonic()
                with self.assertRaises(DependencyError):
                    self.smoke(scenario, timeout=0.3)

                self.assertLess(time.monotonic() - started, 1)

    def test_echoes_every_opaque_string_cursor_exactly(self):
        fixtures = (
            ("opaque-empty-cursor", ""),
            ("opaque-control-cursor", "\x00\n"),
            ("opaque-unicode-cursor", "页/二"),
        )

        for scenario, expected_cursor in fixtures:
            with self.subTest(scenario=scenario):
                result = self.smoke(scenario)

                record = json.loads(self.record_path.read_text(encoding="utf-8"))
                tool_requests = [
                    message
                    for message in record["messages"]
                    if message.get("method") == "tools/list"
                ]
                self.assertEqual(
                    [message["id"] for message in tool_requests], [2, 3]
                )
                self.assertEqual(
                    tool_requests[1]["params"], {"cursor": expected_cursor}
                )
                self.assertNotIn(
                    "tools/call",
                    [message.get("method") for message in record["messages"]],
                )
                self.assertNotIn("cursor", json.dumps(result))

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

    def test_cleanup_uses_direct_fallbacks_and_reaps_before_success(self):
        self.write_config({"command": sys.executable, "args": []})
        events = []
        timeout_error = subprocess.TimeoutExpired("fake", 0.1)
        process = ScriptedProcess(
            events, [timeout_error, timeout_error, 0]
        )

        def killpg(_pid, requested_signal):
            events.append(
                "group-terminate"
                if requested_signal == signal.SIGTERM
                else "group-kill"
            )
            raise OSError("PRIVATE-RAW-OUTPUT")

        with mock.patch.object(MCP_DEPS.os, "killpg", side_effect=killpg):
            result = MCP_DEPS.smoke_dependency(
                self.hub, "mineru", runner=lambda *_args, **_kwargs: process
            )

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(
            events,
            [
                "close-stdin",
                "wait",
                "group-terminate",
                "terminate",
                "wait",
                "group-kill",
                "kill",
                "wait",
            ],
        )
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertFalse(
            any(
                thread.name.startswith("mcp-smoke-reader-")
                for thread in threading.enumerate()
            )
        )

    def test_cleanup_fails_closed_on_wait_or_signal_failure_without_raw_output(self):
        self.write_config({"command": sys.executable, "args": []})
        timeout_error = subprocess.TimeoutExpired("fake", 0.1)
        fixtures = (
            (
                "wait-error",
                [OSError("PRIVATE-WAIT-OUTPUT"), 0],
                None,
                None,
                0,
            ),
            (
                "unreaped",
                [timeout_error, timeout_error, timeout_error],
                OSError("PRIVATE-TERM-OUTPUT"),
                OSError("PRIVATE-KILL-OUTPUT"),
                0,
            ),
            (
                "stdin-close",
                [0],
                None,
                None,
                1,
            ),
        )

        for label, waits, terminate_error, kill_error, close_failures in fixtures:
            with self.subTest(label=label):
                events = []
                process = ScriptedProcess(
                    events,
                    waits,
                    terminate_error=terminate_error,
                    kill_error=kill_error,
                    stdin_close_failures=close_failures,
                )
                with mock.patch.object(
                    MCP_DEPS.os,
                    "killpg",
                    side_effect=OSError("PRIVATE-GROUP-OUTPUT"),
                ):
                    with self.assertRaises(DependencyError) as raised:
                        MCP_DEPS.smoke_dependency(
                            self.hub,
                            "mineru",
                            runner=lambda *_args, value=process, **_kwargs: value,
                        )

                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertTrue(process.stdin.closed)
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stderr.closed)

    def test_rejects_non_posix_platform_before_launch(self):
        self.write_config({"command": sys.executable, "args": []})
        launches = []

        def runner(*_args, **_kwargs):
            launches.append(True)
            raise AssertionError("runner must not be called")

        with mock.patch.object(MCP_DEPS.os, "name", "nt"):
            with self.assertRaisesRegex(
                DependencyError, "^MCP smoke probe requires POSIX$"
            ):
                MCP_DEPS.smoke_dependency(
                    self.hub, "mineru", runner=runner
                )

        self.assertEqual(launches, [])

    def test_reader_setup_failures_cleanup_child_pipes_and_started_threads(self):
        self.write_config({"command": sys.executable, "args": []})
        real_thread = threading.Thread

        def construction_failure(*_args, **_kwargs):
            raise RuntimeError("PRIVATE-THREAD-CONSTRUCTION-OUTPUT")

        thread_calls = []

        def second_start_failure(*args, **kwargs):
            thread_calls.append(True)
            if len(thread_calls) == 1:
                return real_thread(*args, **kwargs)
            return StartFailureThread()

        fixtures = (
            ("first-construction", construction_failure),
            ("second-start", second_start_failure),
        )

        for label, thread_factory in fixtures:
            with self.subTest(label=label):
                events = []
                process = SetupFailureProcess(events)
                thread_calls.clear()
                try:
                    with mock.patch.object(
                        MCP_DEPS.threading, "Thread", side_effect=thread_factory
                    ), mock.patch.object(
                        MCP_DEPS.os,
                        "killpg",
                        side_effect=lambda _pid, _signal: events.append("group-signal"),
                    ):
                        with self.assertRaises(DependencyError) as raised:
                            MCP_DEPS.smoke_dependency(
                                self.hub,
                                "mineru",
                                runner=lambda *_args, value=process, **_kwargs: value,
                            )
                finally:
                    process.stdin.close()
                    process.stdout.close()
                    process.stderr.close()
                    for thread in threading.enumerate():
                        if thread.name.startswith("mcp-smoke-reader-"):
                            thread.join(timeout=0.2)

                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertTrue(process.reaped)
                self.assertTrue(process.stdin.closed)
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stderr.closed)
                self.assertFalse(
                    any(
                        thread.name.startswith("mcp-smoke-reader-")
                        for thread in threading.enumerate()
                    )
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


class TrackingBody(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.read_called = False

    def read(self, size=-1):
        self.read_called = True
        return super().read(size)


if __name__ == "__main__":
    unittest.main()
