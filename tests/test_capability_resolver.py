from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.capability_resolver import (
    McpIdentity,
    active_plugin_sources,
    mcp_identity_matches,
    normalize_mcp_identity,
    resolve_target_policy,
)


class CapabilityResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.hub = self.root / "hub"
        (self.hub / "mcp").mkdir(parents=True)
        (self.hub / "policies").mkdir(parents=True)
        (self.hub / "manifest.yaml").write_text(
            "version: 1\nskills:\n  - cloudflare\n  - shared-skill\n",
            encoding="utf-8",
        )
        (self.hub / "mcp" / "shared-servers.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "cloudflare-local": {
                            "type": "http",
                            "url": "https://mcp.cloudflare.com/mcp",
                        },
                        "shared-mcp": {
                            "type": "http",
                            "url": "https://example.com/mcp/",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.hub / "policies" / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "plugins": {
                        "cursor": {
                            "prefer_marketplace": {
                                "cloudflare": {
                                    "skills": ["cloudflare"],
                                    "mcp": [],
                                }
                            }
                        }
                    },
                    "distribution": {
                        "cursor": {
                            "exclude_skills": ["shared-skill"],
                            "exclude_mcp": [],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def cursor_plugin(
        self,
        name: str,
        revision: str,
        *,
        skill: str | None = None,
        servers: dict | None = None,
    ) -> Path:
        root = (
            self.home
            / ".cursor"
            / "plugins"
            / "cache"
            / "cursor-public"
            / name
            / revision
        )
        root.mkdir(parents=True)
        if skill:
            path = root / "skills" / skill
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text(f"# {skill}\n", encoding="utf-8")
        if servers is not None:
            (root / ".mcp.json").write_text(
                json.dumps({"mcpServers": servers}),
                encoding="utf-8",
            )
        return root

    def enable_cursor_plugin(self, name: str, revision: str) -> None:
        log = (
            self.home
            / "Library"
            / "Application Support"
            / "Cursor"
            / "logs"
            / "latest"
            / "Cursor Plugins.log"
        )
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            f"BackendMarketplaceClient: Adding enabled plugin: "
            f"{name} from {revision} at undefined\n",
            encoding="utf-8",
        )

    def write_capability_policy(
        self,
        *,
        selection: str = "auto",
        plugin_id: str = "cloudflare",
        skills: list[str] | None = None,
        mcp: list[str] | None = None,
    ) -> None:
        (self.hub / "policies" / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "capability_ownership": {
                        "defaults": {"selection": "auto"},
                        "capabilities": {
                            "cloudflare": {
                                "hub": {
                                    "skills": skills or ["cloudflare"],
                                    "mcp": mcp or ["cloudflare-local"],
                                },
                                "targets": {
                                    "cursor": {
                                        "official_plugins": [plugin_id],
                                        "selection": selection,
                                    }
                                },
                            }
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_cache_only_cursor_plugin_is_not_active(self):
        self.cursor_plugin("cloudflare", "abc", skill="cloudflare")

        sources = active_plugin_sources("cursor", self.home)

        self.assertEqual(sources, [])

    def test_cursor_log_activates_matching_revision_and_capabilities(self):
        self.cursor_plugin(
            "cloudflare",
            "abc",
            skill="cloudflare",
            servers={
                "shared-alias": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                }
            },
        )
        self.enable_cursor_plugin("cloudflare", "abc")

        sources = active_plugin_sources("cursor", self.home)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].plugin_id, "cloudflare")
        self.assertEqual(sources[0].skills, frozenset({"cloudflare"}))
        self.assertIn("shared-alias", sources[0].mcp_servers)
        self.assertEqual(sources[0].evidence, "cursor-log")

    def test_explicit_cursor_local_plugin_is_active_without_log(self):
        root = self.home / ".cursor" / "plugins" / "local" / "memory"
        root.mkdir(parents=True)
        (root / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared-mcp": {
                            "type": "http",
                            "url": "https://example.com/mcp",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        sources = active_plugin_sources("cursor", self.home)

        self.assertEqual([source.plugin_id for source in sources], ["memory"])
        self.assertEqual(sources[0].evidence, "cursor-local")

    def test_codex_uses_enabled_installed_cli_records(self):
        plugin = self.root / "codex-plugin"
        skill = plugin / "skills" / "shared-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# shared\n", encoding="utf-8")
        payload = {
            "installed": [
                {
                    "pluginId": "enabled@market",
                    "installed": True,
                    "enabled": True,
                    "source": {"path": str(plugin)},
                },
                {
                    "pluginId": "disabled@market",
                    "installed": True,
                    "enabled": False,
                    "source": {"path": str(plugin)},
                },
            ]
        }

        def runner(command, **kwargs):
            self.assertEqual(command[:3], ["codex", "plugin", "list"])
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        sources = active_plugin_sources("codex", self.home, runner=runner)

        self.assertEqual([source.plugin_id for source in sources], ["enabled@market"])
        self.assertEqual(sources[0].skills, frozenset({"shared-skill"}))
        self.assertEqual(sources[0].evidence, "codex-cli")

    def test_mcp_identity_normalizes_url_and_stdio_shape(self):
        self.assertEqual(
            normalize_mcp_identity(
                {"type": "http", "url": "HTTPS://Example.COM/mcp/?token=hidden"}
            ),
            McpIdentity("http", "https://example.com/mcp", ()),
        )
        self.assertEqual(
            normalize_mcp_identity(
                {"command": "/usr/local/bin/tool", "args": ["serve", "/private/path"]}
            ),
            McpIdentity("stdio", "tool", ("serve", "<absolute-path>")),
        )

    def test_canonical_placeholder_argument_matches_local_resolved_path(self):
        self.assertTrue(
            mcp_identity_matches(
                {
                    "command": "npx",
                    "args": [
                        "package",
                        "/tmp/local-vault",
                    ],
                },
                {
                    "command": "npx",
                    "args": [
                        "package",
                        "${OBSIDIAN_VAULT}",
                    ],
                },
            )
        )

    def test_resolution_combines_exact_and_policy_suppression(self):
        self.cursor_plugin(
            "cloudflare",
            "abc",
            skill="cloudflare",
            servers={
                "shared-alias": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                }
            },
        )
        self.enable_cursor_plugin("cloudflare", "abc")

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertEqual(
            resolution.suppressed_skills,
            frozenset({"cloudflare", "shared-skill"}),
        )
        self.assertEqual(
            resolution.suppressed_mcp,
            frozenset({"shared-mcp"}),
        )
        self.assertEqual(
            resolution.skill_owners["cloudflare"],
            "plugin:cloudflare",
        )
        self.assertEqual(
            resolution.skill_owners["shared-skill"],
            "excluded",
        )
        self.assertEqual(
            resolution.mcp_owners["shared-mcp"],
            "plugin:cloudflare",
        )
        self.assertEqual(
            resolution.mcp_collisions,
            frozenset(),
        )
        self.assertNotIn("cloudflare-local", resolution.suppressed_mcp)

    def test_unmapped_plugin_is_not_probed_or_treated_as_collision(self):
        plugin = self.home / ".cursor/plugins/local/conflict"
        plugin.mkdir(parents=True)
        (plugin / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared-mcp": {
                            "type": "http",
                            "url": "https://different.example/mcp",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertNotIn("shared-mcp", resolution.suppressed_mcp)
        self.assertNotIn("shared-mcp", resolution.mcp_owners)
        self.assertEqual(resolution.mcp_collisions, frozenset())

    def test_explicit_marketplace_preference_resolves_same_name_collision(self):
        plugin = self.home / ".cursor/plugins/local/conflict"
        plugin.mkdir(parents=True)
        (plugin / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared-mcp": {
                            "type": "http",
                            "url": "https://different.example/mcp",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.hub / "policies" / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "plugins": {
                        "cursor": {
                            "prefer_marketplace": {
                                "conflict": {
                                    "skills": [],
                                    "mcp": ["shared-mcp"],
                                }
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertEqual(
            resolution.suppressed_mcp,
            frozenset({"shared-mcp"}),
        )
        self.assertEqual(
            resolution.mcp_owners["shared-mcp"],
            "plugin:conflict",
        )
        self.assertEqual(
            resolution.mcp_collisions,
            frozenset(),
        )

    def test_plugin_disappearance_restores_conditional_capability(self):
        self.cursor_plugin("cloudflare", "abc", skill="cloudflare")

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertNotIn("cloudflare", resolution.suppressed_skills)
        self.assertIn("shared-skill", resolution.suppressed_skills)
        self.assertEqual(resolution.skill_owners["shared-skill"], "excluded")

    def test_auto_selects_active_reviewed_plugin_and_suppresses_full_hub_bundle(self):
        self.write_capability_policy()
        self.cursor_plugin(
            "cloudflare",
            "abc",
            skill="cloudflare",
            servers={
                "cloudflare-docs": {
                    "type": "http",
                    "url": "https://docs.mcp.cloudflare.com/mcp",
                }
            },
        )
        self.enable_cursor_plugin("cloudflare", "abc")

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertEqual(
            resolution.suppressed_skills,
            frozenset({"cloudflare"}),
        )
        self.assertEqual(
            resolution.suppressed_mcp,
            frozenset({"cloudflare-local"}),
        )
        decision = resolution.capabilities["cloudflare"]
        self.assertEqual(decision.state, "plugin-owned")
        self.assertEqual(decision.effective_owner, "plugin:cloudflare")
        self.assertEqual(decision.fallback_owner, "hub")

    def test_auto_restores_hub_when_plugin_disappears(self):
        self.write_capability_policy()

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertEqual(resolution.suppressed_skills, frozenset())
        self.assertEqual(resolution.suppressed_mcp, frozenset())
        decision = resolution.capabilities["cloudflare"]
        self.assertEqual(decision.state, "hub-managed")
        self.assertEqual(decision.effective_owner, "hub")
        self.assertEqual(decision.fallback_owner, "hub")

    def test_explicit_hub_reports_active_plugin_conflict_without_suppression(self):
        self.write_capability_policy(selection="hub")
        self.cursor_plugin(
            "cloudflare",
            "abc",
            skill="cloudflare",
            servers={
                "cloudflare-docs": {
                    "type": "http",
                    "url": "https://docs.mcp.cloudflare.com/mcp",
                }
            },
        )
        self.enable_cursor_plugin("cloudflare", "abc")

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertEqual(resolution.suppressed_skills, frozenset())
        self.assertEqual(resolution.suppressed_mcp, frozenset())
        decision = resolution.capabilities["cloudflare"]
        self.assertEqual(decision.state, "ownership-conflict")
        self.assertEqual(decision.desired_owner, "hub")
        self.assertEqual(decision.effective_owner, "hub")

    def test_explicit_plugin_missing_does_not_silently_fall_back_to_hub(self):
        self.write_capability_policy(selection="plugin:cloudflare")

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertEqual(
            resolution.suppressed_skills,
            frozenset({"cloudflare"}),
        )
        self.assertEqual(
            resolution.suppressed_mcp,
            frozenset({"cloudflare-local"}),
        )
        decision = resolution.capabilities["cloudflare"]
        self.assertEqual(decision.state, "owner-missing")
        self.assertEqual(decision.desired_owner, "plugin:cloudflare")
        self.assertIsNone(decision.effective_owner)

    def test_disabled_selection_suppresses_declared_hub_bundle(self):
        self.write_capability_policy(selection="disabled")

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertEqual(
            resolution.suppressed_skills,
            frozenset({"cloudflare"}),
        )
        self.assertEqual(
            resolution.suppressed_mcp,
            frozenset({"cloudflare-local"}),
        )
        self.assertEqual(
            resolution.capabilities["cloudflare"].state,
            "disabled-by-policy",
        )

    def test_unknown_hub_component_is_reported_without_suppression(self):
        self.write_capability_policy(mcp=["not-in-hub"])
        self.cursor_plugin("cloudflare", "abc", skill="cloudflare", servers={})
        self.enable_cursor_plugin("cloudflare", "abc")

        resolution = resolve_target_policy("cursor", self.home, self.hub)

        self.assertNotIn("not-in-hub", resolution.suppressed_mcp)
        self.assertTrue(
            any("not-in-hub" in finding for finding in resolution.findings)
        )

    def test_profile_target_is_passed_intact_to_plugin_adapter(self):
        (self.hub / "policies" / "tool-scopes.json").write_text(
            json.dumps(
                {
                    "capability_ownership": {
                        "defaults": {"selection": "auto"},
                        "capabilities": {
                            "cloudflare": {
                                "hub": {
                                    "skills": ["cloudflare"],
                                    "mcp": ["cloudflare-local"],
                                },
                                "targets": {
                                    "vscode": {
                                        "official_plugins": ["vendor.sample"],
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
        database = (
            self.home
            / "Library/Application Support/Code/User/profiles/research/globalStorage/state.vscdb"
        )
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
            connection.commit()
        finally:
            connection.close()

        def runner(command, **kwargs):
            self.assertEqual(
                command,
                [
                    "code",
                    "--profile",
                    "research",
                    "--list-extensions",
                    "--show-versions",
                ],
            )
            return subprocess.CompletedProcess(
                command, 0, "vendor.sample@1.0.0\n", ""
            )

        resolution = resolve_target_policy(
            "vscode:research", self.home, self.hub, runner=runner
        )

        self.assertEqual(
            resolution.suppressed_skills, frozenset({"cloudflare"})
        )
        self.assertEqual(
            resolution.suppressed_mcp, frozenset({"cloudflare-local"})
        )
        self.assertEqual(resolution.uncertain, ())


if __name__ == "__main__":
    unittest.main()
