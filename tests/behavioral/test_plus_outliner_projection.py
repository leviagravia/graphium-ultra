from __future__ import annotations

import ast
import unittest
from pathlib import Path

from graphium_plus.markdown import build_markdown_document_map
from graphium_plus.outliner import build_outline_projection


class PlusOutlinerProjectionTests(unittest.TestCase):
    def test_projection_uses_exact_heading_authority_and_literal_titles(self):
        text = "# Alpha\nbody\n### Deep *literal*\nmore\nSetext Two\n---\n"
        model = build_markdown_document_map(text)
        outline = build_outline_projection(text, model)
        self.assertEqual(
            [(e.level, e.title) for e in outline.entries],
            [(1, "Alpha"), (3, "Deep *literal*"), (2, "Setext Two")],
        )
        self.assertTrue(outline.matches_text(text))
        self.assertEqual(outline.entry_at_or_before(0), outline.entries[0])
        self.assertEqual(
            outline.entry_at_or_before(text.index("more")),
            outline.entries[1],
        )
        self.assertEqual(outline.entry_at_or_before(len(text)), outline.entries[-1])

    def test_desktop_fixture_freezes_setext_h2_projection_before_true_gtk(self):
        text = (
            "preface\n"
            "# Alpha\n"
            "body\n"
            "## Beta\n"
            "more\n"
            "Gamma\n"
            "---\n"
            "end\n"
        )
        model = build_markdown_document_map(text)
        outline = build_outline_projection(text, model)
        self.assertEqual(
            [(e.level, e.title) for e in outline.entries],
            [(1, "Alpha"), (2, "Beta"), (2, "Gamma")],
        )

    def test_before_first_heading_has_no_current_entry_and_stale_map_is_rejected(self):
        text = "preface\n# Head\nbody\n"
        model = build_markdown_document_map(text)
        outline = build_outline_projection(text, model)
        self.assertIsNone(outline.entry_at_or_before(0))
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_outline_projection(text + "x", model)
        self.assertFalse(outline.matches_text(text.replace("Head", "Other")))

    def test_outliner_is_projection_only_without_storage_or_document_authority(self):
        path = Path(__file__).resolve().parents[2] / "graphium_plus/outliner.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        self.assertFalse(any(name == "gi" or name.startswith("gi.") for name in imports))
        for forbidden in (
            "open(", "write_text", "sqlite", "DocumentSession", "NativeEditorController",
            "Gtk.", "Gio.", "thread", "worker", "timer",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
