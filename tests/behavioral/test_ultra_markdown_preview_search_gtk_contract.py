from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class UltraMarkdownPreviewSearchGtkContractTests(unittest.TestCase):
    def test_viewer_reuses_core_literal_search_authority_without_parser_or_index(self):
        pure = (ROOT / "graphium_ultra/markdown_viewer.py").read_text(encoding="utf-8")
        self.assertIn("from graphium.domain.text_search import SearchScaleError, find_all, validate_query", pure)
        self.assertIn("find_markdown_viewer_search_hits", pure)
        self.assertNotIn("re.compile", pure)
        adapter = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for forbidden in ("self.core.search", "build_markdown_document_map", "threading", "ThreadPool", "timeout_add("):
            self.assertNotIn(forbidden, adapter)

    def test_search_ui_is_viewer_local_searchbar_with_expected_keyboard_contract(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            "Gtk.SearchBar()", "Gtk.SearchEntry()", 'Gtk.Button(label="Previous")',
            'Gtk.Button(label="Next")', 'Gtk.CheckButton(label="Match Case")',
            "Gdk.KEY_F3", "Gdk.ModifierType.SHIFT_MASK", "Gdk.ModifierType.CONTROL_MASK",
            "_show_preview_search", "_close_preview_search",
        ):
            self.assertIn(marker, source)

    def test_visual_search_segments_include_real_buffer_slice_and_native_table_cells(self):
        source = (ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py").read_text(encoding="utf-8")
        for marker in (
            "self.buffer.get_slice(start, end, True)", "self._table_search_cells.append",
            "table_anchor_offset", "_translated_offset(table_start, self._replacement_ranges)",
            "find_markdown_viewer_search_hits",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("plan.text.find", source)

    def test_search_highlight_does_not_use_buffer_selection_and_explicit_search_cancels_restore(self):
        path = ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py"
        source = path.read_text(encoding="utf-8")
        start = source.index("    def _project_search_hit")
        end = source.index("    def _search_move", start)
        body = source[start:end]
        self.assertIn("self.buffer.apply_tag(self._search_tag", body)
        self.assertNotIn("self.buffer.select_range", body)
        self.assertIn("_cancel_reading_position_restore(clear_pending=True)", body)
        self.assertIn("label.select_region(hit.start, hit.end)", body)

    def test_render_reindexes_search_without_automatically_navigating(self):
        path = ROOT / "graphium_ultra/adapters/gtk/markdown_viewer.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        render = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "render")
        text = ast.unparse(render)
        self.assertIn("self._refresh_preview_search(reset_current=True)", text)
        self.assertNotIn("self._search_move", text)
        self.assertLess(text.index("_refresh_preview_search"), text.index("_schedule_reading_position_restore"))

    def test_true_gtk_scenario_covers_body_table_refresh_and_neutrality(self):
        scenario = (ROOT / "tests/desktop/scenarios/ultra_markdown_preview_search.py").read_text(encoding="utf-8")
        for marker in (
            "ULTRA_U1_7_TRUE_GTK_PREVIEW_SEARCH=PASS", "_show_preview_search()",
            "_search_entry.set_text", "_search_move(1)", "_search_selected_label",
            "viewer.render(plan2)", "get_char_count()",
        ):
            self.assertIn(marker, scenario)
        for forbidden in ("Gdk.test_simulate_button", "time.sleep", "mock.patch", "input("):
            self.assertNotIn(forbidden, scenario)
        runner = (ROOT / "tests/desktop/run.py").read_text(encoding="utf-8")
        self.assertIn("'ultra_markdown_preview_search'", runner)


if __name__ == "__main__":
    unittest.main()
