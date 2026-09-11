from __future__ import annotations

import ast
import unittest
from pathlib import Path

from graphium_plus.markdown import (
    MarkdownBlockKind,
    MarkdownInlineKind,
    build_markdown_document_map,
)


class PlusMarkdownStructureTests(unittest.TestCase):
    def test_atx_setext_fence_and_section_hierarchy(self):
        text = (
            "# Alpha\n"
            "body\n"
            "```md\n# hidden\n```\n"
            "## Child\nchild\n"
            "Setext One *em*\n===\n"
            "Setext Two\n---\n"
            "# Omega\n"
        )
        model = build_markdown_document_map(text)
        self.assertEqual([h.level for h in model.headings], [1, 2, 1, 2, 1])
        self.assertEqual(
            [text[h.content_start:h.content_end] for h in model.headings],
            ["Alpha", "Child", "Setext One *em*", "Setext Two", "Omega"],
        )
        self.assertEqual(model.headings[0].section_end, model.headings[2].source_start)
        self.assertEqual(model.headings[1].section_end, model.headings[2].source_start)
        self.assertEqual(model.headings[2].section_end, model.headings[4].source_start)
        self.assertEqual(model.headings[3].section_end, model.headings[4].source_start)
        self.assertEqual(model.headings[4].section_end, len(text))
        self.assertEqual(
            len([b for b in model.blocks if b.kind is MarkdownBlockKind.FENCED_CODE]), 1
        )
        self.assertIn(MarkdownInlineKind.EMPHASIS, {s.kind for s in model.inline_spans})

    def test_structural_authority_keeps_deferred_academic_markers_literal(self):
        text = "# Head {#literal}\nSee [^n].\n[^n]: Note\n"
        model = build_markdown_document_map(text)
        self.assertEqual(text[model.headings[0].content_start:model.headings[0].content_end], "Head {#literal}")
        self.assertFalse(any("footnote" in span.kind.value for span in model.inline_spans))
        self.assertFalse(any("footnote" in block.kind.value for block in model.blocks))

    def test_structure_module_is_gtk_io_and_presentation_free(self):
        path = Path(__file__).resolve().parents[2] / "graphium_plus/markdown.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        self.assertFalse(any(name == "gi" or name.startswith("gi.") for name in imports))
        self.assertFalse(any(name in {"os", "pathlib", "subprocess", "shlex"} for name in imports))
        for forbidden in (
            "DocumentSession", "NativeEditorController", "GuardedFileWriter",
            "MarkdownViewerPlan", "MarkdownSourcePresentationPlan", "build_viewer_plan",
            "build_source_presentation_plan",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
