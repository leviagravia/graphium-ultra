from __future__ import annotations

import unittest
from pathlib import Path

from tests.release._common import ROOT


class R1EDocumentationIdentityTests(unittest.TestCase):
    def test_keyboard_shortcut_authorities_are_edition_specific(self):
        core = (ROOT / "docs/user/GRAPHIUM_KEYBOARD_SHORTCUTS.txt").read_text(encoding="utf-8")
        plus = (ROOT / "docs/user/GRAPHIUM_PLUS_KEYBOARD_SHORTCUTS.txt").read_text(encoding="utf-8")
        ultra = (ROOT / "docs/user/GRAPHIUM_ULTRA_KEYBOARD_SHORTCUTS.txt").read_text(encoding="utf-8")

        self.assertIn("GRAPHIUM KEYBOARD SHORTCUTS", core)
        self.assertNotIn("F9              Focus Mode", core)
        self.assertNotIn("Ctrl+K          Command Palette", core)

        self.assertIn("GRAPHIUM PLUS KEYBOARD SHORTCUTS", plus)
        self.assertIn("F9              Focus Mode", plus)
        self.assertIn("Ctrl+K          Command Palette", plus)

        self.assertIn("GRAPHIUM ULTRA KEYBOARD SHORTCUTS", ultra)
        self.assertIn("F9              Focus Mode", ultra)
        self.assertIn("Ctrl+K          Command Palette", ultra)
        self.assertIn("Markdown Viewer has no dedicated accelerator", ultra)

    def test_each_edition_help_action_opens_its_own_shortcut_authority(self):
        core = (ROOT / "graphium/adapters/gtk/window.py").read_text(encoding="utf-8")
        plus = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        ultra = (ROOT / "graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertIn('path=self._help_path("GRAPHIUM_KEYBOARD_SHORTCUTS.txt")', core)
        self.assertIn('path=self._help_path("GRAPHIUM_PLUS_KEYBOARD_SHORTCUTS.txt")', plus)
        self.assertIn('path=self._help_path("GRAPHIUM_ULTRA_KEYBOARD_SHORTCUTS.txt")', ultra)

    def test_plus_and_ultra_guides_document_current_modularity_and_close_chrome(self):
        for name in ("GRAPHIUM_PLUS_USER_GUIDE.txt", "GRAPHIUM_ULTRA_USER_GUIDE.txt"):
            guide = (ROOT / "docs/user" / name).read_text(encoding="utf-8")
            for marker in (
                "Plus adds F9 for Focus Mode and Ctrl+K for Command Palette",
                "RIGHT COMPANION PANEL — GRAPHIUM PLUS",
                "shows Clips and Scratchpad simultaneously",
                "there is no Clips/Scratchpad selector",
                "View -> Companion Focus -> Clips / Scratchpad only moves",
                "the small X in the Workspace header",
                "the small X in the Outline header",
                "the small X in the panel header",
                "Workspace, compact Toolbar, Outline, Markdown Toolbar and Companion Panel are independent optional",
            ):
                self.assertIn(marker, guide, (name, marker))
            self.assertNotIn("Graphium Plus adds no new global accelerator", guide)
            self.assertNotIn("adds\nexactly those two bounded presentation surfaces", guide)

    def test_ultra_guide_uses_ultra_identity_for_running_application_operations(self):
        guide = (ROOT / "docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt").read_text(encoding="utf-8")
        for marker in (
            "opens that file inside Graphium Ultra",
            "With a Workspace item selected, Graphium Ultra requests",
            "If FileManager1 is unavailable, Graphium Ultra falls back",
            "Closing Graphium Ultra while Pandoc is active",
            "$XDG_DATA_HOME/graphium-ultra/references.md",
            "Graphium Ultra inherits the Plus Pandoc-output workflow",
        ):
            self.assertIn(marker, guide)
        # Inherited feature ownership remains explicitly Plus where that is the architectural truth.
        self.assertIn("26. MARKDOWN WRITING COMMANDS — GRAPHIUM PLUS", guide)
        self.assertIn("33. NATIVE MARKDOWN VIEWER — GRAPHIUM ULTRA", guide)
        self.assertNotIn("$XDG_DATA_HOME/graphium-plus/references.md", guide)

    def test_ultra_readme_links_to_ultra_shortcut_authority(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt", readme)
        self.assertIn("docs/user/GRAPHIUM_ULTRA_KEYBOARD_SHORTCUTS.txt", readme)
        self.assertNotIn("docs/user/GRAPHIUM_PLUS_KEYBOARD_SHORTCUTS.txt", readme)


if __name__ == "__main__":
    unittest.main()
