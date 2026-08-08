from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "scripts" / "fix-preview.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fix_preview", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FixPreviewTests(unittest.TestCase):
    def test_healthy_report_has_no_phantom_repairs(self):
        output = load_module().render_preview(
            {"overall": "HEALTHY", "findings": []}
        )

        self.assertEqual(output, "No repair actions required.")
        self.assertNotIn("would sync", output.lower())

    def test_drift_report_lists_only_detected_findings(self):
        output = load_module().render_preview(
            {
                "overall": "ATTENTION",
                "findings": [
                    {
                        "message": "Codex skill coverage is incomplete.",
                        "scope": "managed",
                        "owner": "hub",
                        "repair": "skills",
                    },
                    {
                        "message": "Claude has a local-only failure.",
                        "scope": "local",
                        "owner": "claude",
                    },
                ],
            }
        )

        self.assertIn("Repair candidate: Codex skill coverage is incomplete.", output)
        self.assertIn("Manual review: Claude has a local-only failure.", output)
        self.assertNotIn("sync missing shared MCP", output)

    def test_hub_owned_source_error_is_not_claimed_as_automatic(self):
        output = load_module().render_preview(
            {
                "overall": "ATTENTION",
                "findings": [
                    {
                        "message": "Capability ownership policy is invalid.",
                        "scope": "policy",
                        "owner": "hub",
                    }
                ],
            }
        )

        self.assertEqual(
            output,
            "  - Manual review: Capability ownership policy is invalid.",
        )


if __name__ == "__main__":
    unittest.main()
