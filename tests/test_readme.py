import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HERO_ASSETS = (
    REPO / "assets/readme/agent-sync-hero-light.svg",
    REPO / "assets/readme/agent-sync-hero-dark.svg",
)


class ReadmeContractTests(unittest.TestCase):
    def test_hero_assets_are_local_safe_svg(self):
        """Catch missing, malformed, remote, or private hero assets."""
        for asset in HERO_ASSETS:
            with self.subTest(asset=asset.name):
                root = ET.parse(asset).getroot()
                text = asset.read_text(encoding="utf-8")
                lowered = text.lower()

                self.assertTrue(root.tag.endswith("svg"))
                self.assertEqual(root.attrib.get("viewBox"), "0 0 1440 520")
                self.assertNotIn("<script", lowered)
                self.assertNotIn('href="http', lowered)
                self.assertNotIn("href='http", lowered)
                self.assertNotIn("/users/", lowered)
                self.assertNotIn("slavatang", lowered)
                self.assertIn("agent-sync", text)
                self.assertIn("Private Hub", text)
                self.assertIn("Skills · MCP · Rules · Ownership", text)


if __name__ == "__main__":
    unittest.main()
