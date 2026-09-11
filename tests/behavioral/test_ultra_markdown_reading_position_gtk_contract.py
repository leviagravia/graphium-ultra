from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class UltraMarkdownReadingPositionGtkContractTests(unittest.TestCase):
    def test_plan_owns_only_small_semantic_position_without_gtk_or_io(self):
        source = (ROOT / "graphium_ultra/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            "class MarkdownViewerReadingPosition",
            "capture_markdown_viewer_reading_position",
            "resolve_markdown_viewer_reading_position",
            "anchor_identifier",
            "section_fraction",
            "global_fraction",
        ):
            self.assertIn(marker, source)
        for forbidden in ("Gtk.Adjustment", "GSettings", "json.dump", "write_text", "sqlite"):
            self.assertNotIn(forbidden, source)

    def test_adapter_captures_top_visible_line_and_restores_via_mark(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            "self.text_view.get_visible_rect()",
            "self.text_view.get_line_at_y(int(visible.y))",
            "_plan_offset_from_buffer_offset",
            "capture_markdown_viewer_reading_position",
            "resolve_markdown_viewer_reading_position",
            "self.buffer.create_mark(None, iterator, True)",
            "self.text_view.scroll_to_mark",
            "GLib.idle_add(",
            'self.connect("show", self._on_viewer_show)',
        ):
            self.assertIn(marker, source)
        for forbidden in (
            "vadjustment.set_value", "get_vadjustment().set_value", "scroll_to_iter",
            "GSettings", "settings.json", "pickle", "sqlite",
        ):
            self.assertNotIn(forbidden, source)

    def test_refresh_captures_before_destructive_projection_and_restores_after(self):
        path = ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        render = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "render")
        text = ast.unparse(render)
        self.assertLess(text.index("_capture_reading_position"), text.index("self.buffer.set_text"))
        self.assertGreater(text.rindex("_schedule_reading_position_restore"), text.index("self.buffer.set_text"))

    def test_focus_toggle_remembers_position_before_hiding_viewer(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        start = source.index("    def _enter_focus_mode")
        end = source.index("    def _exit_focus_mode", start)
        body = source[start:end]
        self.assertIn("viewer.remember_reading_position()", body)
        self.assertLess(body.index("viewer.remember_reading_position()"), body.index("viewer.hide()"))

    def test_explicit_u15_navigation_cancels_passive_u16_restore(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        start = source.index("    def _activate_target")
        end = source.index("    def _on_label_activate_link", start)
        body = source[start:end]
        self.assertIn("_cancel_reading_position_restore(clear_pending=True)", body)
        self.assertLess(body.index("_cancel_reading_position_restore"), body.index("resolve_markdown_viewer_link"))

    def test_true_gtk_scenario_covers_refresh_and_focus_like_toggle(self):
        scenario = (ROOT / "tests/desktop/scenarios/ultra_markdown_reading_position.py").read_text(encoding="utf-8")
        for marker in (
            "ULTRA_U1_6_TRUE_GTK_READING_POSITION=PASS",
            "viewer.render(plan2)", "viewer.remember_reading_position()",
            "viewer.hide()", "viewer.render(plan3)", "viewer.show",
            "resolve_markdown_viewer_reading_position",
        ):
            self.assertIn(marker, scenario)
        for forbidden in ("input(", "mock.patch", "unittest.mock", "time.sleep"):
            self.assertNotIn(forbidden, scenario)
        runner = (ROOT / "tests/desktop/run.py").read_text(encoding="utf-8")
        self.assertIn("'ultra_markdown_reading_position'", runner)

    def test_reading_position_dies_with_viewer_and_has_no_parent_or_disk_persistence(self):
        adapter = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        self.assertIn('self.connect("destroy", self._on_viewer_destroy)', adapter)
        destroy_at = adapter.index("    def _on_viewer_destroy")
        self.assertIn("clear_pending=True", adapter[destroy_at:destroy_at + 260])
        window = (ROOT / "graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        for forbidden in (
            "_markdown_viewer_reading_position", "reading-position.json", "GSettings",
            "XDG_CONFIG_HOME", "XDG_STATE_HOME",
        ):
            self.assertNotIn(forbidden, window)


if __name__ == "__main__":
    unittest.main()
