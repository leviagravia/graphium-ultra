from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

from graphium_plus.surface_state import SurfaceVisibilityStore


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / "graphium_plus/adapters/gtk/window.py"


def segment(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    return ast.get_source_segment(source, node) or ""


class SurfaceVisibilityStoreTests(unittest.TestCase):
    def test_fail_soft_defaults_and_atomic_boolean_payload(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state" / "surface.json"
            store = SurfaceVisibilityStore(path)
            self.assertTrue(store.load())
            store.save(False)
            self.assertFalse(store.load())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"schema": 1, "visible": False})
            path.write_text("not-json", encoding="utf-8")
            self.assertTrue(store.load())


class PlusUltraSurfaceModularityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WINDOW.read_text(encoding="utf-8")

    def test_toolbar_outline_and_markdown_toolbar_have_independent_stateful_actions(self):
        cases = (
            ("_install_toolbar_visibility_control", "toolbar-visible", "Toolbar"),
            ("_install_outline_visibility_control", "outline-visible", "Outline"),
            ("_install_markdown_toolbar_visibility_control", "markdown-toolbar-visible", "Markdown Toolbar"),
        )
        for method, action_name, label in cases:
            part = segment(self.source, method)
            self.assertIn("Gio.SimpleAction.new_stateful", part)
            self.assertIn(f'"{action_name}"', part)
            self.assertIn('action.connect("change-state"', part)
            self.assertIn(f'Gtk.CheckMenuItem(label="{label}")', part)
            self.assertIn(f'win.{action_name}', part)

    def test_each_optional_surface_has_its_own_persisted_visibility_file(self):
        init = segment(self.source, "__init__")
        for filename in (
            "toolbar-visible.json",
            "outline-visible.json",
            "markdown-toolbar-visible.json",
        ):
            self.assertIn(filename, init)
        self.assertIn("workspace-panel.json", init)

    def test_hidden_outline_does_not_reparse_or_schedule_background_refresh(self):
        effective = segment(self.source, "_outline_effectively_visible")
        changed = segment(self.source, "_on_outliner_buffer_changed")
        schedule = segment(self.source, "_schedule_outliner_refresh")
        refresh = segment(self.source, "_refresh_outliner_now")
        project = segment(self.source, "_project_outline_visibility")
        self.assertIn("self._outline_preferred_visible", effective)
        self.assertIn("self.outliner_panel.widget.get_visible()", effective)
        self.assertIn("self._outline_effectively_visible()", changed)
        self.assertIn("if not self._outline_effectively_visible()", schedule)
        self.assertIn("if not self._outline_effectively_visible()", refresh)
        self.assertIn("self._cancel_outliner_refresh()", project)
        self.assertIn("panel.hide()", project)

    def test_focus_mode_suspends_outline_refresh_and_resumes_once_if_stale(self):
        enter = segment(self.source, "_enter_focus_mode")
        exit_focus = segment(self.source, "_exit_focus_mode")
        self.assertIn("self._cancel_outliner_refresh()", enter)
        self.assertIn("snapshot.outliner_visible and self._outliner_dirty", exit_focus)
        self.assertIn("self._schedule_outliner_refresh()", exit_focus)

    def test_focus_mode_is_temporary_override_for_all_optional_surfaces(self):
        enter = segment(self.source, "_enter_focus_mode")
        exit_focus = segment(self.source, "_exit_focus_mode")
        for action_name in (
            "workspace-visible", "toolbar-visible", "outline-visible", "markdown-toolbar-visible"
        ):
            self.assertIn(action_name, enter)
            self.assertIn(action_name, exit_focus)
        self.assertNotIn(".save(", enter)
        self.assertNotIn(".save(", exit_focus)

    def test_markdown_toolbar_buttons_expose_labels_for_gtk_overflow_proxies(self):
        builder = segment(self.source, "_build_markdown_toolbar")
        self.assertIn("toolbar.set_show_arrow(True)", builder)
        self.assertIn("button.set_label(spec.label)", builder)
        self.assertIn("button.set_action_name", builder)

    def test_true_gtk_boundary_covers_modularity_and_overflow_proxy(self):
        scenario = (ROOT / "tests/desktop/scenarios/plus_surface_modularity.py").read_text(encoding="utf-8")
        self.assertIn('outline.change_state(GLib.Variant.new_boolean(False))', scenario)
        self.assertIn('toolbar.change_state(GLib.Variant.new_boolean(False))', scenario)
        self.assertIn('markdown_toolbar.change_state(GLib.Variant.new_boolean(False))', scenario)
        self.assertIn('window.show_all()', scenario)
        self.assertIn('bold.retrieve_proxy_menu_item()', scenario)
        self.assertIn('proxy.activate()', scenario)
        self.assertIn('MARKDOWN_TOOLBAR_OVERFLOW_PROXY=PASS', scenario)


if __name__ == "__main__":
    unittest.main()
