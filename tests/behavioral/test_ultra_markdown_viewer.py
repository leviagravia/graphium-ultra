from __future__ import annotations

import ast
import unittest
from pathlib import Path

from graphium_plus.markdown import build_markdown_document_map
from graphium_ultra.markdown_viewer import (
    MarkdownViewerSpanKind,
    build_markdown_viewer_plan,
)


ROOT = Path(__file__).resolve().parents[2]


class UltraMarkdownViewerPlanTests(unittest.TestCase):
    def test_basic_native_projection_uses_shared_map(self):
        text = (
            "# Heading *em* and **strong**\n\n"
            "- item `code`\n"
            "> quoted\n\n"
            "```py\nprint('x')\n```\n\n"
            "---\n"
        )
        model = build_markdown_document_map(text)
        plan = build_markdown_viewer_plan(text, model, source_state_id=41)
        self.assertEqual(
            plan.text,
            "Heading em and strong\n\n• item code\nquoted\n\nprint('x')\n\n────────────",
        )
        kinds = {span.kind for span in plan.spans}
        self.assertTrue({
            MarkdownViewerSpanKind.HEADING,
            MarkdownViewerSpanKind.EMPHASIS,
            MarkdownViewerSpanKind.STRONG,
            MarkdownViewerSpanKind.INLINE_CODE,
            MarkdownViewerSpanKind.LIST_ITEM,
            MarkdownViewerSpanKind.BLOCKQUOTE,
            MarkdownViewerSpanKind.CODE_BLOCK,
            MarkdownViewerSpanKind.THEMATIC_BREAK,
        }.issubset(kinds))
        heading = next(span for span in plan.spans if span.kind is MarkdownViewerSpanKind.HEADING)
        self.assertEqual(heading.level, 1)
        self.assertTrue(plan.matches_source(text, 41))
        self.assertFalse(plan.matches_source(text, 42))

    def test_links_are_labels_and_images_are_inert_placeholders(self):
        text = "See [Graphium](https://example.invalid) and ![figure](figure.png).\n"
        model = build_markdown_document_map(text)
        plan = build_markdown_viewer_plan(text, model)
        self.assertEqual(plan.text, "See Graphium and [Image: figure].")
        link = next(span for span in plan.spans if span.kind is MarkdownViewerSpanKind.LINK_LABEL)
        image = next(span for span in plan.spans if span.kind is MarkdownViewerSpanKind.IMAGE_PLACEHOLDER)
        self.assertEqual(link.target, "https://example.invalid")
        self.assertEqual(image.target, "figure.png")

    def test_ordered_marker_is_preserved_without_reparsing(self):
        text = "12) twelve\n3. three\n"
        model = build_markdown_document_map(text)
        plan = build_markdown_viewer_plan(text, model)
        self.assertEqual(plan.text, "12) twelve\n3. three")

    def test_map_identity_mismatch_is_refused(self):
        model = build_markdown_document_map("# Alpha\n")
        with self.assertRaises(ValueError):
            build_markdown_viewer_plan("# Beta\n", model)

    def test_empty_and_blank_source_remain_empty_presentation(self):
        for text in ("", "\n\n"):
            model = build_markdown_document_map(text)
            plan = build_markdown_viewer_plan(text, model)
            self.assertEqual(plan.text, "")
            self.assertEqual(plan.spans, ())
            self.assertTrue(plan.matches_source(text))

    def test_inline_projection_uses_one_monotonic_cursor_not_per_block_rescans(self):
        source = (ROOT / "graphium_ultra/markdown_viewer.py").read_text(encoding="utf-8")
        self.assertIn("class _InlineCursor", source)
        self.assertIn("self.index = index", source)
        self.assertNotIn("for span in document_map.inline_spans", source)
        text = "".join(f"line {i} with *em* and **strong**\n" for i in range(2000))
        model = build_markdown_document_map(text)
        plan = build_markdown_viewer_plan(text, model)
        self.assertEqual(plan.text.count("em"), 2000)
        self.assertEqual(plan.text.count("strong"), 2000)

    def test_viewer_plan_module_is_gtk_io_and_parser_free(self):
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
        self.assertFalse(any(name in {"os", "pathlib", "subprocess", "re"} for name in imports))
        self.assertNotIn("build_markdown_document_map", source)
        self.assertNotIn("open(", source)


if __name__ == "__main__":
    unittest.main()
