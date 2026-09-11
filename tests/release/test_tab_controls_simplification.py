from __future__ import annotations

import ast
import unittest
from pathlib import Path

from graphium.application.commands import COMMANDS
from graphium.application.view_settings import (
    DEFAULT_TAB_WIDTH,
    MAX_TAB_WIDTH,
    MIN_TAB_WIDTH,
    ViewSettings,
)
from tests.release._common import ROOT


class TabControlsSimplificationTests(unittest.TestCase):
    def test_edit_surface_has_direct_tab_controls_and_no_preferences(self):
        edit = [c for c in COMMANDS if c.menu == "Edit" and c.submenu is None]
        actions = [c.action for c in edit]
        self.assertNotIn("preferences", actions)
        self.assertEqual(actions.count("tab-width"), 1)
        self.assertEqual(actions.count("insert-spaces"), 1)
        tab = next(c for c in edit if c.action == "tab-width")
        spaces = next(c for c in edit if c.action == "insert-spaces")
        self.assertEqual(tab.label, "Tab Width")
        self.assertEqual(tab.choices, (("2", "2"), ("3", "3"), ("4", "4"), ("8", "8"), ("Other…", "other")))
        self.assertIsNone(tab.accelerator)
        self.assertEqual((spaces.label, spaces.stateful, spaces.accelerator), ("Insert Spaces Instead of Tabs", True, None))

    def test_existing_settings_authority_and_domain_are_unchanged(self):
        self.assertEqual((DEFAULT_TAB_WIDTH, MIN_TAB_WIDTH, MAX_TAB_WIDTH), (8, 1, 32))
        settings = ViewSettings()
        self.assertEqual((settings.tab_width, settings.insert_spaces), (8, False))
        store_source = (ROOT / "graphium/infrastructure/view_settings_store.py").read_text(encoding="utf-8")
        self.assertIn('"tab_width": settings.tab_width', store_source)
        self.assertIn('"insert_spaces": settings.insert_spaces', store_source)
        self.assertNotIn("migration", store_source.lower())

    def test_generic_preferences_surface_is_erased(self):
        window = (ROOT / "graphium/adapters/gtk/window.py").read_text(encoding="utf-8")
        dialogs = (ROOT / "graphium/adapters/gtk/dialogs.py").read_text(encoding="utf-8")
        commands = (ROOT / "graphium/application/commands.py").read_text(encoding="utf-8")
        for forbidden in ("choose_preferences", "_action_preferences", "_commit_preferences"):
            self.assertNotIn(forbidden, window)
            self.assertNotIn(forbidden, dialogs)
        self.assertNotIn('CommandSpec("preferences"', commands)

    def test_only_narrow_other_chooser_survives(self):
        dialogs = (ROOT / "graphium/adapters/gtk/dialogs.py").read_text(encoding="utf-8")
        tree = ast.parse(dialogs)
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("choose_tab_width", funcs)
        node = funcs["choose_tab_width"]
        text = ast.get_source_segment(dialogs, node) or ""
        self.assertIn("Tab Width", text)
        self.assertIn("lower=1.0", text)
        self.assertIn("upper=32.0", text)
        self.assertNotIn("CheckButton", text)
        self.assertNotIn("insert_spaces", text)

    def test_window_owns_transactional_direct_actions_without_new_authority(self):
        source = (ROOT / "graphium/adapters/gtk/window.py").read_text(encoding="utf-8")
        for required in (
            '"tab-width": self._action_tab_width',
            '"insert-spaces": self._action_insert_spaces',
            "def _action_tab_width",
            "def _action_insert_spaces",
        ):
            self.assertIn(required, source)
        for forbidden in ("TabSettingsController", "GSettings", "EditorConfig", "GtkSource"):
            self.assertNotIn(forbidden, source)

    def test_help_has_direct_controls_and_no_preferences_reference(self):
        guide = (ROOT / "docs/user/GRAPHIUM_USER_GUIDE.txt").read_text(encoding="utf-8")
        shortcuts = (ROOT / "docs/user/GRAPHIUM_KEYBOARD_SHORTCUTS.txt").read_text(encoding="utf-8")
        self.assertIn("Edit -> Tab Width", guide)
        self.assertIn("Insert Spaces Instead of Tabs", guide)
        self.assertNotIn("Edit -> Preferences", guide)
        self.assertNotIn("Preferences…", shortcuts)


if __name__ == "__main__":
    unittest.main()
