from __future__ import annotations

import ast
import unittest
from pathlib import Path

from graphium_plus.markdown import (
    MarkdownBlockKind,
    MarkdownTableAlignment,
    build_markdown_document_map,
)
from graphium_ultra.markdown_viewer import (
    MarkdownViewerSpanKind,
    build_markdown_viewer_plan,
)


ROOT = Path(__file__).resolve().parents[2]


class UltraMarkdownTableAuthorityTests(unittest.TestCase):
    def test_shared_map_adds_table_metadata_without_reclassifying_plus_blocks(self):
        text = (
            "| Name | Qty | Note |\n"
            "| :--- | ---: | :---: |\n"
            "| Apples | 3 | Fresh |\n"
        )
        model = build_markdown_document_map(text)
        self.assertEqual(len(model.tables), 1)
        table = model.tables[0]
        self.assertEqual(
            table.alignments,
            (
                MarkdownTableAlignment.LEFT,
                MarkdownTableAlignment.RIGHT,
                MarkdownTableAlignment.CENTER,
            ),
        )
        self.assertEqual(
            [text[cell.content_start:cell.content_end] for cell in table.header],
            ["Name", "Qty", "Note"],
        )
        self.assertEqual(
            [text[cell.content_start:cell.content_end] for cell in table.rows[0]],
            ["Apples", "3", "Fresh"],
        )
        self.assertTrue(all(block.kind is MarkdownBlockKind.PARAGRAPH for block in model.blocks))

    def test_optional_outer_pipes_escaped_pipe_and_code_pipe_are_bounded_cells(self):
        text = (
            r"A \| literal | Code" "\n"
            "--- | ---\n"
            r"left \| value | `a|b`" "\n"
        )
        model = build_markdown_document_map(text)
        self.assertEqual(len(model.tables), 1)
        table = model.tables[0]
        self.assertEqual(len(table.header), 2)
        plan = build_markdown_viewer_plan(text, model)
        self.assertEqual(plan.text, "[Table]")
        self.assertEqual(len(plan.tables), 1)
        row = plan.tables[0].rows[0]
        self.assertEqual(row[0].text, r"left \| value")
        self.assertEqual(row[1].text, "a|b")
        self.assertEqual(row[1].spans[0].kind, MarkdownViewerSpanKind.INLINE_CODE)

    def test_viewer_table_plan_removes_pipe_syntax_and_reuses_inline_spans(self):
        text = (
            "Before\n\n"
            "| Name | Value |\n"
            "| :--- | ---: |\n"
            "| **Alpha** | `42` |\n"
            "| [Beta](https://example.invalid) | *fine* |\n"
            "\nAfter\n"
        )
        model = build_markdown_document_map(text)
        plan = build_markdown_viewer_plan(text, model, source_state_id=73)
        self.assertEqual(plan.text, "Before\n\n[Table]\n\nAfter")
        self.assertEqual(len(plan.tables), 1)
        table = plan.tables[0]
        self.assertEqual([cell.text for cell in table.header], ["Name", "Value"])
        self.assertEqual([cell.text for cell in table.rows[0]], ["Alpha", "42"])
        self.assertEqual([cell.text for cell in table.rows[1]], ["Beta", "fine"])
        self.assertEqual(table.rows[0][0].spans[0].kind, MarkdownViewerSpanKind.STRONG)
        self.assertEqual(table.rows[0][1].spans[0].kind, MarkdownViewerSpanKind.INLINE_CODE)
        self.assertEqual(table.rows[1][0].spans[0].kind, MarkdownViewerSpanKind.LINK_LABEL)
        self.assertEqual(table.rows[1][1].spans[0].kind, MarkdownViewerSpanKind.EMPHASIS)
        self.assertTrue(plan.matches_source(text, 73))

    def test_malformed_mismatched_and_fenced_lookalikes_fail_closed(self):
        cases = (
            "| A | B |\n| -- | --- |\n| x | y |\n",
            "| A | B |\n| --- | --- |\n| x | y | extra |\n",
            "```md\n| A | B |\n| --- | --- |\n```\n",
        )
        for text in cases:
            with self.subTest(text=text):
                model = build_markdown_document_map(text)
                if "extra" in text:
                    # The valid header+delimiter is a table; the mismatched row is preserved after it.
                    self.assertEqual(len(model.tables), 1)
                    plan = build_markdown_viewer_plan(text, model)
                    self.assertIn("extra", plan.text)
                else:
                    self.assertEqual(model.tables, ())

    def test_image_inside_candidate_table_preserves_u13_ordinary_text_path(self):
        text = (
            "| Figure | Note |\n"
            "| --- | --- |\n"
            "| ![local](figure.png) | kept by U1.3 |\n"
        )
        model = build_markdown_document_map(text)
        self.assertEqual(model.tables, ())
        plan = build_markdown_viewer_plan(text, model)
        self.assertIn("[Image: local]", plan.text)
        self.assertTrue(any(
            span.kind is MarkdownViewerSpanKind.IMAGE_PLACEHOLDER for span in plan.spans
        ))

    def test_hostile_table_bounds_fail_closed_without_truncation(self):
        over_columns = (
            "|" + "|".join(f" h{i} " for i in range(33)) + "|\n" +
            "|" + "|".join(" --- " for _ in range(33)) + "|\n"
        )
        over_cell = "| H |\n| --- |\n| " + ("x" * 8193) + " |\n"
        many_rows = ["| H |\n", "| --- |\n"] + ["| x |\n"] * 512
        over_rows = "".join(many_rows)
        wide_cell = "x" * 8192
        over_source = (
            "| " + " | ".join("H" for _ in range(32)) + " |\n" +
            "| " + " | ".join("---" for _ in range(32)) + " |\n" +
            "".join(
                "| " + " | ".join(wide_cell for _ in range(32)) + " |\n"
                for _ in range(2)
            )
        )
        for name, text in (
            ("columns", over_columns),
            ("cell", over_cell),
            ("rows", over_rows),
            ("source", over_source),
        ):
            with self.subTest(bound=name):
                model = build_markdown_document_map(text)
                self.assertEqual(model.tables, ())
                plan = build_markdown_viewer_plan(text, model)
                self.assertNotEqual(plan.text, "[Table]")

    def test_ultra_plan_contains_no_table_parser_or_gtk(self):
        path = ROOT / "graphium_ultra/markdown_viewer.py"
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
            "re.compile", "_TABLE_DELIMITER_CELL_RE", "split('|')", 'split("|")',
            "Gtk.Grid", "Gtk.TreeView", "Gtk.ListStore",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
