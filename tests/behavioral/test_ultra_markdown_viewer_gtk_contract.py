from __future__ import annotations

import ast
import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class UltraMarkdownViewerGtkContractTests(unittest.TestCase):
    def test_native_viewer_is_read_only_textview_without_web_stack(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            "Gtk.TextView.new_with_buffer",
            "set_editable(False)",
            "set_cursor_visible(False)",
            "MarkdownViewerPlan",
        ):
            self.assertIn(marker, source)
        for forbidden in (
            "WebKit", "webkit", "GtkSourceView", "set_editable(True)",
            "button-release-event", "subprocess", "Pandoc",
        ):
            self.assertNotIn(forbidden, source)

    def test_window_owns_one_view_action_and_live_source_projection(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        for marker in (
            'Gio.SimpleAction.new("markdown-viewer", None)',
            'Gtk.MenuItem(label="Markdown Viewer")',
            'item.set_action_name("win.markdown-viewer")',
            "capture_programmatic_source()",
            "build_markdown_document_map(snapshot.text)",
            "build_markdown_viewer_plan(",
            "plan.matches_source(current.text, current.state_id)",
            "GLib.timeout_add(",
            "_MARKDOWN_VIEWER_REFRESH_DELAY_MS",
        ):
            self.assertIn(marker, source)

    def test_viewer_refresh_has_no_disk_or_document_write_authority(self):
        path = ROOT / "graphium_ultra/adapters/gtk/window.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_refresh_markdown_viewer_now"
        )
        text = ast.unparse(function)
        for forbidden in (
            "open(", "read_text", "read_bytes", "writer", "save", "set_text",
            "initialize_new_text", "apply_transaction", "begin_native_group",
        ):
            self.assertNotIn(forbidden, text)

    def test_general_markdown_authority_remains_single(self):
        definitions = []
        for path in (ROOT / "graphium_plus").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_markdown_document_map":
                    definitions.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(definitions, ["graphium_plus/markdown.py"])


    def test_u13_images_use_bounded_native_gdkpixbuf_and_no_network_stack(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            'gi.require_version("GdkPixbuf", "2.0")',
            "GdkPixbuf.Pixbuf.get_file_info",
            "GdkPixbuf.Pixbuf.new_from_file_at_scale",
            "self.buffer.insert_pixbuf",
            '_ALLOWED_IMAGE_FORMATS = frozenset({"png", "jpeg", "webp"})',
            "_MAX_SOURCE_IMAGE_PIXELS = 48_000_000",
        ):
            self.assertIn(marker, source)
        for forbidden in (
            "requests", "urllib.request", "urlopen", "Soup.Session", "WebKit", "subprocess",
        ):
            self.assertNotIn(forbidden, source)

    def test_u13_resolution_consumes_existing_image_span_target_without_markdown_reparse(self):
        source = (ROOT / "graphium_ultra/markdown_viewer_images.py").read_text(encoding="utf-8")
        self.assertIn("resolve_markdown_viewer_image", source)
        self.assertNotIn("build_markdown_document_map", source)
        self.assertNotIn("MarkdownInlineKind", source)
        window = (ROOT / "graphium_ultra/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertIn("viewer.render(plan, document_path=self.core.session.logical_path)", window)

    def test_u13_true_gtk_images_are_isolated_from_plus_lifecycle(self):
        images = (ROOT / "tests/desktop/scenarios/ultra_markdown_images.py").read_text(encoding="utf-8")
        for forbidden in (
            "begin_user_action", "buffer.insert(", "initialize_new_text",
            "confirm_unsaved_changes", "Gtk.ResponseType", "mock.patch",
            "unittest.mock", "setattr(", "input(",
        ):
            self.assertNotIn(forbidden, images)
        self.assertEqual(images.count("window.open_path("), 1)
        self.assertLess(
            images.index("window.open_path("),
            images.index('window.lookup_action("markdown-viewer")'),
        )
        runner = (ROOT / "tests/desktop/run.py").read_text(encoding="utf-8")
        self.assertIn("'ultra_markdown_images'", runner)
        plus_window = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.assertNotIn("markdown-viewer", plus_window)
        self.assertNotIn("_markdown_viewer", plus_window)


    def test_u14_tables_use_shared_map_and_native_disposable_grid(self):
        adapter = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            "Gtk.Grid(column_spacing=0, row_spacing=0)",
            "Gtk.Label()",
            "Gtk.Frame()",
            "self.buffer.create_child_anchor",
            "self.text_view.add_child_at_anchor",
            "self._embedded_widgets",
            "self._clear_embedded_widgets()",
        ):
            self.assertIn(marker, adapter)
        for forbidden in (
            "Gtk.TreeView", "Gtk.ListStore", "WebKit", "webkit", "re.compile",
            "_TABLE_DELIMITER_CELL_RE", "build_markdown_document_map",
        ):
            self.assertNotIn(forbidden, adapter)

        plan = (ROOT / "graphium_ultra/markdown_viewer.py").read_text(encoding="utf-8")
        self.assertIn("document_map.tables", plan)
        self.assertIn("MarkdownViewerTable", plan)
        self.assertIn("MarkdownViewerSpanKind.TABLE_PLACEHOLDER", plan)
        for forbidden in ("Gtk.Grid", "re.compile", "_TABLE_DELIMITER_CELL_RE"):
            self.assertNotIn(forbidden, plan)

        shared = (ROOT / "graphium_plus/markdown.py").read_text(encoding="utf-8")
        self.assertIn("class MarkdownTable", shared)
        self.assertIn("tables: tuple[MarkdownTable, ...]", shared)
        self.assertIn("_TABLE_MAX_COLUMNS = 32", shared)
        self.assertIn("_TABLE_MAX_ROWS = 512", shared)

    def test_u15_links_follow_mature_textview_routing_and_mark_scroll(self):
        adapter = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            '"viewer-link", underline=Pango.Underline.SINGLE',
            'self.text_view.connect("event-after", self._on_text_view_event_after)',
            'label.connect("activate-link", self._on_label_activate_link)',
            "event.get_button()", "event.get_coords()",
            "Gtk.TextWindowType.WIDGET", "text_view.window_to_buffer_coords",
            "text_view.get_iter_at_location", "self.buffer.get_selection_bounds()",
            "self.buffer.create_mark(None, iterator, True)",
            "self.text_view.scroll_to_mark", "self.buffer.delete_mark(mark)",
            "resolve_markdown_viewer_link", "Gio.AppInfo.launch_default_for_uri",
        ):
            self.assertIn(marker, adapter)
        for forbidden in (
            'self._link_tag.connect("event"', "_on_link_tag_event",
            "text_view.get_window_type", "self.text_view.scroll_to_iter",
            "WebKit", "webkit", "requests", "urllib.request", "urlopen",
            "subprocess", "Popen", "os.system", "Gtk.LinkButton",
        ):
            self.assertNotIn(forbidden, adapter)

        plan = (ROOT / "graphium_ultra/markdown_viewer.py").read_text(encoding="utf-8")
        self.assertIn('_ALLOWED_EXTERNAL_LINK_SCHEMES = frozenset({"http", "https"})', plan)
        self.assertIn("_MAX_LINK_TARGET_CHARS = 4096", plan)
        self.assertIn("plan.heading_anchors", plan)
        self.assertNotIn("Gio.AppInfo", plan)

        shared = (ROOT / "graphium_plus/markdown.py").read_text(encoding="utf-8")
        self.assertIn("class MarkdownHeadingAnchor", shared)
        self.assertIn("heading_anchors: tuple[MarkdownHeadingAnchor, ...]", shared)
        self.assertIn("_build_heading_anchors", shared)
        self.assertNotIn("import shlex", shared)

        desktop = (ROOT / "tests/desktop/scenarios/ultra_markdown_links.py").read_text(encoding="utf-8")
        self.assertIn("ULTRA_U1_5_TRUE_GTK_ACTIVE_LINKS=PASS", desktop)
        self.assertIn("Gdk.test_simulate_button", desktop)
        self.assertIn("get_visible_rect", desktop)
        self.assertIn("get_iter_location", desktop)
        self.assertNotIn("get_value()", desktop)
        self.assertNotIn('viewer._link_tag.emit("event"', desktop)
        self.assertIn('link_label.emit("activate-link", "#target")', desktop)
        for forbidden in ("mock.patch", "unittest.mock", "input("):
            self.assertNotIn(forbidden, desktop)
        runner = (ROOT / "tests/desktop/run.py").read_text(encoding="utf-8")
        self.assertIn("'ultra_markdown_links'", runner)

    def test_user_guide_records_u14_native_table_boundary(self):
        guide = (ROOT / "docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt").read_text(encoding="utf-8")
        prose = " ".join(guide.split())
        for marker in (
            "U1.4 native tables",
            "native read-only GTK grids",
            "header row",
            "left/center/right alignment",
            "Malformed, ambiguous or over-budget table candidates remain ordinary text",
            "candidate containing an image",
            "U1.5 active links",
            "Internal heading links",
            "HTTP/HTTPS",
        ):
            self.assertIn(marker, prose)

    def test_user_guide_records_u12_boundary_and_deferred_features(self):
        guide = (ROOT / "docs/user/GRAPHIUM_ULTRA_USER_GUIDE.txt").read_text(encoding="utf-8")
        prose = " ".join(guide.split())
        for marker in (
            "33. NATIVE MARKDOWN VIEWER — GRAPHIUM ULTRA",
            "View -> Markdown Viewer",
            "current unsaved editor text",
            "separate read-only native GTK window",
            "does not read the document from disk",
            "Local PNG/JPEG/WebP images are rendered by U1.3",
            "Remote/URI targets",
            "remain inert text placeholders",
            "U1.4 native tables",
            "U1.5 active links",
            "Internal heading links",
            "HTTP/HTTPS",
        ):
            self.assertIn(marker, prose)


if __name__ == "__main__":
    unittest.main()
