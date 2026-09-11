from __future__ import annotations

import ast
import unittest
from pathlib import Path

from graphium_plus.markdown import build_markdown_document_map
from graphium_ultra.markdown_viewer import (
    MarkdownViewerLinkActionKind,
    MarkdownViewerSpanKind,
    build_markdown_viewer_plan,
    resolve_markdown_viewer_link,
)


ROOT = Path(__file__).resolve().parents[2]


class UltraMarkdownLinkTests(unittest.TestCase):
    def _plan(self, text: str):
        return build_markdown_viewer_plan(text, build_markdown_document_map(text))

    def test_shared_map_adds_pandoc_compatible_automatic_heading_anchors(self):
        text = (
            "# Hello, World!\n"
            "## 33 Déjà vu & test_case.foo\n"
            "# Hello, World!\n"
            "# 123 !!!\n"
            "# STRAẞE\n"
        )
        model = build_markdown_document_map(text)
        self.assertEqual(
            [anchor.identifier for anchor in model.heading_anchors],
            ["hello-world", "déjà-vu-test_case.foo", "hello-world-1", "section", "straße"],
        )
        self.assertTrue(all(not anchor.explicit for anchor in model.heading_anchors))

    def test_heading_anchor_uses_rendered_inline_content_and_one_monotonic_scan(self):
        text = "# [Hello](https://example.invalid) *wide* **world**\n"
        model = build_markdown_document_map(text)
        self.assertEqual(model.heading_anchors[0].identifier, "hello-wide-world")
        source = (ROOT / "graphium_plus/markdown.py").read_text(encoding="utf-8")
        self.assertIn("span_index = 0", source)
        self.assertIn("span_index = probe", source)

    def test_valid_explicit_id_overrides_automatic_and_duplicate_explicit_is_ambiguous(self):
        plan = self._plan(
            "# First {#same}\n"
            "# Second {#same}\n"
            "# Automatic\n"
            "[ambiguous](#same) [auto](#automatic)\n"
        )
        self.assertEqual(
            [(anchor.identifier, anchor.explicit) for anchor in plan.heading_anchors],
            [("same", True), ("same", True), ("automatic", False)],
        )
        self.assertIsNone(resolve_markdown_viewer_link(plan, "#same"))
        action = resolve_markdown_viewer_link(plan, "#automatic")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, MarkdownViewerLinkActionKind.INTERNAL)
        self.assertEqual(action.offset, plan.heading_anchors[2].offset)

    def test_automatic_id_respects_prior_explicit_id_without_rewriting_explicit_duplicates(self):
        model = build_markdown_document_map("# One {#two}\n# Two\n# Two\n")
        self.assertEqual(
            [anchor.identifier for anchor in model.heading_anchors],
            ["two", "two-1", "two-2"],
        )

    def test_internal_fragments_support_percent_decoding_and_missing_target_is_inert(self):
        plan = self._plan("# Déjà vu\n[go](#d%C3%A9j%C3%A0-vu) [missing](#none)\n")
        action = resolve_markdown_viewer_link(plan, "#d%C3%A9j%C3%A0-vu")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, MarkdownViewerLinkActionKind.INTERNAL)
        self.assertIsNone(resolve_markdown_viewer_link(plan, "#none"))
        self.assertIsNone(resolve_markdown_viewer_link(plan, "#"))

    def test_external_policy_allows_only_absolute_http_https(self):
        plan = self._plan("# Head\n")
        for target in (
            "https://example.invalid/path?q=1#x",
            "http://example.invalid",
        ):
            action = resolve_markdown_viewer_link(plan, target)
            self.assertIsNotNone(action)
            self.assertEqual(action.kind, MarkdownViewerLinkActionKind.EXTERNAL)
            self.assertEqual(action.target, target)
            self.assertIsNone(action.offset)
        for target in (
            "file:///tmp/a",
            "data:text/plain,x",
            "javascript:alert(1)",
            "ftp://example.invalid/x",
            "mailto:test@example.invalid",
            "//example.invalid/x",
            "/relative/path",
            "relative.md",
            "https:///missing-host",
            "https://example.invalid\n/x",
            "x" * 4097,
        ):
            self.assertIsNone(resolve_markdown_viewer_link(plan, target), target)

    def test_body_and_table_links_reuse_existing_parsed_targets(self):
        text = (
            "# Target\n\n"
            "[jump](#target) [web](https://example.invalid)\n\n"
            "| Name | Go |\n"
            "| --- | --- |\n"
            "| A | [inside](#target) |\n"
        )
        plan = self._plan(text)
        body_links = [span for span in plan.spans if span.kind is MarkdownViewerSpanKind.LINK_LABEL]
        self.assertEqual([span.target for span in body_links], ["#target", "https://example.invalid"])
        table_links = [
            span
            for table in plan.tables
            for row in (table.header,) + table.rows
            for cell in row
            for span in cell.spans
            if span.kind is MarkdownViewerSpanKind.LINK_LABEL
        ]
        self.assertEqual([span.target for span in table_links], ["#target"])

    def test_link_resolution_is_gtk_io_and_subprocess_free(self):
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
        self.assertNotIn("subprocess", imports)
        self.assertNotIn("build_markdown_document_map", source)
        self.assertNotIn("launch_default_for_uri", source)


if __name__ == "__main__":
    unittest.main()
