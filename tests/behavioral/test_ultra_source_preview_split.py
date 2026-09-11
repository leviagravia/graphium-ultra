from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / "graphium_ultra/adapters/gtk/window.py"
VIEWER = ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py"


class UltraSourcePreviewSplitContractTests(unittest.TestCase):
    def test_shared_viewer_surface_is_reused_by_window_and_split(self):
        source = VIEWER.read_text(encoding="utf-8")
        self.assertIn("class MarkdownViewerSurface(Gtk.Box):", source)
        self.assertIn("class MarkdownViewerWindow(Gtk.Window):", source)
        self.assertIn("self.surface = MarkdownViewerSurface", source)
        self.assertEqual(source.count("Gtk.TextView.new_with_buffer"), 1)
        self.assertEqual(source.count("def render(self, plan: MarkdownViewerPlan"), 2)
        wrapper = source[source.index("class MarkdownViewerWindow"):]
        self.assertIn("self.surface.render(plan", wrapper)
        self.assertNotIn("build_markdown_viewer_plan", wrapper)

    def test_split_is_one_stateful_native_paned_surface_without_persistence(self):
        source = WINDOW.read_text(encoding="utf-8")
        for marker in (
            'Gio.SimpleAction.new_stateful(\n            "source-preview-split"',
            'Gtk.CheckMenuItem(label="Source | Preview")',
            'split_item.set_action_name("win.source-preview-split")',
            "self.source_preview_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)",
            "self.source_preview_paned.pack1(self.editor_box, resize=True, shrink=False)",
            "self.source_preview_paned.pack2(host, resize=True, shrink=True)",
            "MarkdownViewerSurface(is_effectively_visible=host.get_visible)",
        ):
            self.assertIn(marker, source)
        for forbidden in (
            "SurfaceVisibilityStore", "source-preview-split.json", "GSettings",
            "XDG_STATE_HOME", "XDG_CONFIG_HOME", "Gtk.Notebook", "Gtk.Stack",
            "WebKit", "GtkSourceView",
        ):
            self.assertNotIn(forbidden, source)

    def test_split_close_routes_through_unique_action_state(self):
        source = WINDOW.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(WINDOW))
        close = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_close_source_preview_split"
        )
        body = ast.unparse(close)
        self.assertIn("self._actions['source-preview-split'].change_state", body)
        self.assertNotIn(".hide()", body)
        self.assertNotIn("set_state", body)

    def test_one_live_snapshot_and_one_plan_feed_all_viewer_targets(self):
        source = WINDOW.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(WINDOW))
        refresh = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_refresh_markdown_viewer_now"
        )
        body = ast.unparse(refresh)
        self.assertEqual(body.count("capture_programmatic_source()"), 2)
        self.assertEqual(body.count("build_markdown_document_map(snapshot.text)"), 1)
        self.assertEqual(body.count("build_markdown_viewer_plan("), 1)
        self.assertIn("for viewer in targets", body)
        self.assertIn("viewer.render(plan", body)
        for forbidden in ("read_text", "read_bytes", "open(", "threading", "subprocess"):
            self.assertNotIn(forbidden, body)

    def test_focus_temporarily_hides_split_without_changing_action_preference(self):
        source = WINDOW.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(WINDOW))
        enter = ast.unparse(next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_enter_focus_mode"
        ))
        exit_focus = ast.unparse(next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_exit_focus_mode"
        ))
        self.assertIn("self._markdown_split_surface.remember_reading_position()", enter)
        self.assertIn("self._project_source_preview_split(False, remember=False)", enter)
        self.assertIn("self._actions['source-preview-split'].set_enabled(False)", enter)
        self.assertNotIn("change_state", enter)
        self.assertIn("self._actions['source-preview-split'].set_enabled(True)", exit_focus)
        self.assertIn("self._project_source_preview_split(True, remember=False)", exit_focus)
        self.assertNotIn("change_state", exit_focus)

    def test_split_surface_is_lazy_reused_and_has_no_second_document_authority(self):
        source = WINDOW.read_text(encoding="utf-8")
        self.assertIn("if self._markdown_split_surface is not None:", source)
        self.assertIn("return self._markdown_split_surface", source)
        for forbidden in (
            "GtkTextBufferPort", "initialize_new_text", "begin_user_action",
            "apply_transaction", "build_core(", "DocumentSession", "writer.save",
        ):
            self.assertNotIn(forbidden, source)

    def test_true_gtk_scenario_is_registered_and_covers_split_lifecycle(self):
        scenario = (ROOT / "tests/desktop/scenarios/ultra_source_preview_split.py").read_text(encoding="utf-8")
        for marker in (
            "ULTRA_U1_8_TRUE_GTK_SOURCE_PREVIEW_SPLIT=PASS",
            'lookup_action("source-preview-split")',
            "window._markdown_split_surface",
            "window.source_preview_paned",
            "window._markdown_split_host",
            'lookup_action("focus-mode")',
        ):
            self.assertIn(marker, scenario)
        for forbidden in ("input(", "mock.patch", "unittest.mock", "time.sleep", "Gdk.test_simulate_button"):
            self.assertNotIn(forbidden, scenario)
        runner = (ROOT / "tests/desktop/run.py").read_text(encoding="utf-8")
        self.assertIn("'ultra_source_preview_split'", runner)

    def test_user_guide_records_optional_native_split_boundary(self):
        guide = (ROOT / "docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt").read_text(encoding="utf-8")
        prose = " ".join(guide.split())
        for marker in (
            "U1.8 Source | Preview split",
            "View -> Source | Preview",
            "same native Viewer surface",
            "editor Gtk.TextBuffer remains the only mutable document authority",
            "split geometry is session-only",
        ):
            self.assertIn(marker, prose)


if __name__ == "__main__":
    unittest.main()
