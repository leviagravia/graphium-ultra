from __future__ import annotations

import ast
from pathlib import Path
import unittest

from graphium_plus.markdown_commands import MARKDOWN_COMMANDS


ROOT = Path(__file__).resolve().parents[2]


class MarkdownToolbarCatalogTests(unittest.TestCase):
    def test_toolbar_is_a_subset_projection_of_the_single_markdown_catalog(self):
        toolbar = [spec for spec in MARKDOWN_COMMANDS if spec.toolbar_markup is not None]
        self.assertEqual(
            [spec.action for spec in toolbar],
            [
                "markdown-heading-1",
                "markdown-heading-2",
                "markdown-bold",
                "markdown-italic",
                "markdown-strikethrough",
                "markdown-inline-code",
                "markdown-blockquote",
                "markdown-unordered-list",
                "markdown-ordered-list",
                "markdown-task-list",
                "markdown-fenced-code",
                "markdown-link",
                "markdown-image",
                "insert-footnote",
            ],
        )
        self.assertEqual(len({spec.action for spec in toolbar}), len(toolbar))
        self.assertTrue(all(spec.label and spec.group and spec.toolbar_markup for spec in toolbar))

    def test_toolbar_builder_binds_actions_and_contains_no_command_callbacks(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        builder = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_build_markdown_toolbar"
        )
        segment = ast.get_source_segment(source, builder) or ""
        self.assertIn("for spec in MARKDOWN_COMMANDS", segment)
        self.assertIn("if spec.toolbar_markup is None", segment)
        self.assertIn('button.set_action_name(f"win.{spec.action}")', segment)
        self.assertIn("button.set_label(spec.label)", segment)
        self.assertIn("button.set_tooltip_text(spec.label)", segment)
        self.assertIn('toolbar.get_style_context().add_class("inline-toolbar")', segment)
        self.assertNotRegex(segment, r'connect\s*\(\s*["\'](?:clicked|activate)["\']')
        self.assertNotIn("plan_markdown_command(", segment)

    def test_toolbar_is_editor_local_below_the_authoritative_scroller(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertIn("self.editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL", source)
        self.assertIn("self.editor_box.pack_start(self._editor_scroller, True, True, 0)", source)
        self.assertIn("self.editor_box.pack_end(self.markdown_toolbar, False, False, 0)", source)
        self.assertIn("self.editor_companion_paned.pack1(self.editor_box, resize=True, shrink=False)", source)
        self.assertIn('"markdown-toolbar-visible"', source)
        self.assertIn('Gtk.CheckMenuItem(label="Markdown Toolbar")', source)
        self.assertIn('menu_item.set_action_name("win.markdown-toolbar-visible")', source)


    def test_desktop_semantic_oracle_activates_the_internal_actionable_button(self):
        scenario = (ROOT / "tests/desktop/scenarios/plus_markdown_toolbar.py").read_text(encoding="utf-8")
        self.assertNotIn('bold.emit("clicked")', scenario)
        self.assertIn("click_target = bold.get_child()", scenario)
        self.assertIn("isinstance(click_target, Gtk.Button)", scenario)
        self.assertIn('click_target.get_action_name() == "win.markdown-bold"', scenario)
        self.assertIn("click_target.clicked()", scenario)
        self.assertNotIn('click_target.emit("clicked")', scenario)
        self.assertNotIn("Gtk.test_widget_click", scenario)
        self.assertNotIn("Gdk.test_simulate_button", scenario)

    def test_desktop_tooltip_oracle_follows_gtk_toolitem_child_semantics(self):
        scenario = (ROOT / "tests/desktop/scenarios/plus_markdown_toolbar.py").read_text(encoding="utf-8")
        self.assertNotIn("button.get_tooltip_text()", scenario)
        self.assertIn("button.get_child() for button in buttons", scenario)
        self.assertIn("child.get_tooltip_text()", scenario)


if __name__ == "__main__":
    unittest.main()
