import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HERO_ASSETS = (
    REPO / "assets/readme/agent-sync-hero-light.svg",
    REPO / "assets/readme/agent-sync-hero-dark.svg",
)
CORE_COMMANDS = (
    "agent-sync sources",
    "agent-sync sync --from TOOL --dry-run",
    "agent-sync sync",
    "agent-sync all",
    "agent-sync doctor --runtime",
    "agent-sync ownership",
    "agent-sync verify",
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

    def test_english_readme_exposes_the_runnable_product_entry(self):
        """Catch a homepage that loses its language, hero, or CLI entry points."""
        text = (REPO / "README.md").read_text(encoding="utf-8")

        self.assertIn('<a href="README.zh-CN.md">简体中文</a>', text)
        self.assertIn('srcset="assets/readme/agent-sync-hero-dark.svg"', text)
        self.assertIn('srcset="assets/readme/agent-sync-hero-light.svg"', text)
        self.assertIn('src="assets/readme/agent-sync-hero-light.svg"', text)
        for command in CORE_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, text)
        lowered = text.lower()
        self.assertNotIn("/users/", lowered)
        self.assertNotIn("slavatang", lowered)

    def test_chinese_readme_is_complete_and_reciprocal(self):
        """Catch missing translation, broken reciprocity, or divergent examples."""
        english = (REPO / "README.md").read_text(encoding="utf-8")
        chinese_path = REPO / "README.zh-CN.md"
        chinese = chinese_path.read_text(encoding="utf-8")

        self.assertIn('<a href="README.md">English</a>', chinese)
        self.assertIn('srcset="assets/readme/agent-sync-hero-dark.svg"', chinese)
        self.assertIn('srcset="assets/readme/agent-sync-hero-light.svg"', chinese)
        for command in CORE_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, english)
                self.assertIn(command, chinese)
        lowered = chinese.lower()
        self.assertNotIn("/users/", lowered)
        self.assertNotIn("slavatang", lowered)

    def test_local_readme_links_resolve(self):
        """Catch broken local links without requiring network access."""
        patterns = (
            re.compile(r"\[[^\]]*\]\(([^)]+)\)"),
            re.compile(r'(?:href|src|srcset)="([^"]+)"'),
        )
        for readme_name in ("README.md", "README.zh-CN.md"):
            readme = REPO / readme_name
            text = readme.read_text(encoding="utf-8")
            for pattern in patterns:
                for target in pattern.findall(text):
                    target = target.strip().split()[0]
                    if target.startswith(("#", "http://", "https://", "mailto:")):
                        continue
                    local_path = target.split("#", 1)[0]
                    with self.subTest(readme=readme_name, target=target):
                        self.assertTrue((REPO / local_path).exists())


if __name__ == "__main__":
    unittest.main()
