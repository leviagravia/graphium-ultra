from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PlusGtkOutlinerContractTests(unittest.TestCase):
    def test_panel_is_thin_projection_adapter_without_parser_or_document_authority(self):
        path = ROOT / "graphium_plus/adapters/gtk/outliner_panel.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        self.assertIn("graphium_plus.outliner", imported)
        self.assertNotIn("graphium_plus.markdown", imported)
        for forbidden in (
            "build_markdown_document_map", "DocumentSession", "NativeEditorController",
            "GuardedFileWriter", "open(", "write_text", "sqlite", "threading",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('self.tree.set_activate_on_single_click(True)', source)
        self.assertIn('self._on_activate(int(self.store[tree_iter][_COL_CONTENT_START]))', source)

    def test_window_separates_structural_rebuild_from_cursor_tracking(self):
        path = ROOT / "graphium_plus/adapters/gtk/window.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        methods = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        rebuild = methods["_refresh_outliner_now"]
        cursor = methods["_on_outliner_cursor_changed"] + methods["_sync_outliner_cursor"]
        navigate = methods["_navigate_outliner"]
        self.assertIn("build_markdown_document_map", rebuild)
        self.assertIn("build_outline_projection", rebuild)
        self.assertNotIn("build_markdown_document_map", cursor)
        self.assertIn("entry_at_or_before", cursor)
        self.assertIn("projection.matches_text", navigate)
        self.assertIn("self._project_view", navigate)
        self.assertIn("GLib.timeout_add", methods["_schedule_outliner_refresh"])
        self.assertIn("GLib.source_remove", methods["_schedule_outliner_refresh"])
        self.assertNotIn("timeout_add_seconds", source)
        self.assertNotIn("threading", source)
        self.assertIn("self.content_paned.pack1(self.outliner_panel.widget", source)
        self.assertIn("self.editor_box.pack_start(self._editor_scroller, True, True, 0)", source)
        self.assertIn("self.content_paned.pack2(self.editor_companion_paned", source)
        self.assertIn("self.workspace_paned.pack2(self.content_paned", source)


if __name__ == "__main__":
    unittest.main()
