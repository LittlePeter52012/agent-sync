import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
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
