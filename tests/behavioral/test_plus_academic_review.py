from __future__ import annotations

import ast
from pathlib import Path
import time
import unittest

from graphium_plus.references import (
    ReferenceDiagnostic,
    ReferenceFileToken,
    ReferenceLibrarySnapshot,
    ReferenceRecord,
)
from graphium_plus.review import academic_review_text, build_academic_review

ROOT = Path(__file__).parents[2]


def _snapshot(*records: ReferenceRecord, diagnostics=()):
    return ReferenceLibrarySnapshot(tuple(records), ReferenceFileToken(False), tuple(diagnostics))


class PlusAcademicReviewTests(unittest.TestCase):
    def test_aggregates_existing_authorities_with_exact_targets(self):
        text = (
            "# Draft\n"
            "Claim [@missing] with  two spaces.\n"
            "Footnote [^x].\n"
            "[^dup]: first\n"
            "[^dup]: second\n"
            "```\n"
            "ignored  spaces [@missing2]  \n"
        )
        snapshot = _snapshot(
            ReferenceRecord(key="known", title="Known", type="article"),
            diagnostics=(ReferenceDiagnostic(7, "duplicate-key", "Reference key is duplicated."),),
        )
        report = build_academic_review(text, snapshot)
        kinds = [(item.category, item.kind) for item in report.diagnostics]
        self.assertIn(("markdown", "unterminated-fenced-code"), kinds)
        self.assertIn(("citations", "missing-reference"), kinds)
        self.assertIn(("footnotes", "unresolved_reference"), kinds)
        self.assertIn(("footnotes", "duplicate_definition"), kinds)
        self.assertIn(("references", "duplicate-key"), kinds)
        self.assertIn(("writing-hygiene", "multiple-internal-spaces"), kinds)
        self.assertNotIn("missing2", [item.key for item in report.diagnostics])
        missing = next(item for item in report.diagnostics if item.category == "citations")
        self.assertEqual(text[missing.target.offset:missing.target.offset + missing.target.length], "missing")
        self.assertEqual(missing.target.line, 2)
        ref = next(item for item in report.diagnostics if item.category == "references")
        self.assertEqual((ref.target.scope, ref.target.line, ref.target.offset), ("reference-library", 7, None))

    def test_hygiene_ignores_code_and_preserves_markdown_hard_break(self):
        text = (
            "Normal  prose.\n"
            "Hard break  \n"
            "Single trailing \n"
            "Too many trailing   \n"
            "Inline `a  b` stays opaque.\n"
            "```text\n"
            "code  code   \n"
            "```\n"
        )
        report = build_academic_review(text, _snapshot())
        hygiene = [item for item in report.diagnostics if item.category == "writing-hygiene"]
        self.assertEqual(
            [(item.kind, item.target.line, item.target.length) for item in hygiene],
            [
                ("multiple-internal-spaces", 1, 2),
                ("trailing-whitespace", 3, 1),
                ("trailing-whitespace", 4, 3),
            ],
        )
        self.assertFalse(any(item.target.line == 2 for item in hygiene))
        self.assertFalse(any(item.target.line in {6, 7} for item in hygiene))

    def test_report_is_stale_fenced_by_document_and_reference_token(self):
        token = ReferenceFileToken(True, 3, "a" * 64)
        snapshot = ReferenceLibrarySnapshot((), token, ())
        report = build_academic_review("Text", snapshot)
        self.assertTrue(report.matches("Text", token))
        self.assertFalse(report.matches("Text changed", token))
        self.assertFalse(report.matches("Text", ReferenceFileToken(False)))

    def test_text_summary_is_deterministic_and_read_only(self):
        snapshot = _snapshot()
        clean = build_academic_review("Clean text.\n", snapshot)
        self.assertEqual(academic_review_text(clean), "Academic Review: no deterministic issues found.")
        report = build_academic_review("Bad  spacing.\n", snapshot)
        rendered = academic_review_text(report)
        self.assertIn("Academic Review: 0 errors, 1 warnings.", rendered)
        self.assertIn("Writing Hygiene:", rendered)
        self.assertIn("Multiple consecutive spaces", rendered)

    def test_review_module_has_no_gtk_io_writer_or_mutation_authority(self):
        path = ROOT / "graphium_plus/review.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for forbidden in ("gi", "Gtk", "subprocess", "threading", "os", "pathlib"):
            self.assertNotIn(forbidden, imported)
        for forbidden in ("GuardedFileWriter", ".commit(", "open(", "write_text", "write_bytes", "os.replace"):
            self.assertNotIn(forbidden, source)
        self.assertIn("build_markdown_document_map", source)
        self.assertIn("ScholarlyNotesMap.from_text", source)
        self.assertIn("CitationDocumentMap.from_text", source)
        self.assertNotIn("spaces-before-punctuation", source)
        self.assertNotIn("citation-gap", source)

    def test_complexity_falsification_large_prose_is_bounded(self):
        lines = [f"Paragraph {i}  with issue. [@known]\n" for i in range(20_000)]
        text = "".join(lines)
        snapshot = _snapshot(ReferenceRecord(key="known", title="Known", type="article"))
        start = time.monotonic()
        report = build_academic_review(text, snapshot)
        elapsed = time.monotonic() - start
        hygiene = [item for item in report.diagnostics if item.kind == "multiple-internal-spaces"]
        self.assertEqual(len(hygiene), 20_000)
        self.assertFalse(report.errors)
        self.assertLess(elapsed, 6.0, f"academic review unexpectedly slow: {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
