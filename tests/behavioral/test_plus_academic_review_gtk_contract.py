from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest

from graphium.infrastructure.guarded_file_writer import GuardedFileWriter
from graphium_plus.references import MarkdownReferenceLibraryStore

ROOT = Path(__file__).parents[2]


class PlusAcademicReviewGtkContractTests(unittest.TestCase):
    def setUp(self):
        self.window = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text(encoding="utf-8")
        self.dialogs = (ROOT / "graphium_plus/adapters/gtk/review_dialog.py").read_text(encoding="utf-8")

    def test_commands_review_adds_one_academic_review_action_without_new_diagnostics(self):
        self.assertIn('("academic-review", self._action_academic_review)', self.window)
        self.assertIn('("Academic Review…", "academic-review")', self.window)
        self.assertIn("report = build_academic_review(source.text, references)", self.window)
        for forbidden in (
            "multiple-internal-spaces", "trailing-whitespace", "citation-gap",
            "grammar", "style-score", "auto-fix",
        ):
            self.assertNotIn(forbidden, self.dialogs)

    def test_navigation_revalidates_document_and_reference_token_before_projection(self):
        method = self.window[self.window.index("    def _action_academic_review"):]
        method = method[:method.index("\n    def _action_import_bibliography")]
        self.assertIn("current_source = self.core.editor.capture_programmatic_source()", method)
        self.assertIn("current_references = self.reference_store.load()", method)
        self.assertIn("if not report.matches(current_source.text, current_references.token):", method)
        self.assertLess(method.index("if not report.matches"), method.index("self._project_view(ViewState(end, start))"))
        self.assertLess(method.index("if not report.matches"), method.index("show_reference_source_target("))
        for forbidden in ("buffer.insert(", "buffer.delete(", "apply_prevalidated_programmatic_group"):
            self.assertNotIn(forbidden, method)

    def test_dialog_is_projection_only_and_returns_existing_report_item(self):
        tree = ast.parse(self.dialogs)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for forbidden in ("ScholarlyNotesMap", "CitationDocumentMap", "build_markdown_document_map"):
            self.assertNotIn(forbidden, self.dialogs)
        self.assertIn("for index, item in enumerate(report.diagnostics)", self.dialogs)
        self.assertIn("return report.diagnostics[index]", self.dialogs)
        self.assertNotIn("Gtk.Entry", self.dialogs)
        self.assertNotIn("Gtk.TextBuffer()", self.dialogs)
        self.assertNotIn("GuardedFileWriter", self.dialogs)

    def test_reference_source_projection_is_read_only_exact_line_and_stale_fenced(self):
        self.assertGreaterEqual(self.dialogs.count("reference_file_token(target) != expected_token"), 2)
        self.assertIn("view.set_editable(False)", self.dialogs)
        self.assertIn("view.set_cursor_visible(False)", self.dialogs)
        self.assertIn("start = buffer.get_iter_at_line(min(line - 1, max_line))", self.dialogs)
        self.assertIn("buffer.select_range(end, start)", self.dialogs)
        self.assertNotIn("write_text", self.dialogs)
        self.assertNotIn("write_bytes", self.dialogs)

    def test_user_guide_records_projection_stale_refusal_and_non_scope(self):
        guide = (ROOT / "docs/user/GRAPHIUM_PLUS_USER_GUIDE.txt").read_text(encoding="utf-8")
        for marker in (
            "31. ACADEMIC REVIEW — GRAPHIUM PLUS",
            "Commands -> Review -> Academic Review",
            "revalidates both the document digest and the Reference Library token",
            "read-only source projection at the exact",
            "diagnostic line; they do not replace the current document",
            "performs no automatic fixes",
        ):
            self.assertIn(marker, guide)

    def test_malformed_reference_fixture_establishes_fresh_xdg_parent_before_raw_injection(self):
        raw = (
            "# Graphium Plus References v1\n\n"
            "## dup\n"
            "Type: article\n"
            "Title: First\n\n"
            "## dup\n"
            "Type: article\n"
            "Title: Second\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "graphium-plus" / "references.md"
            self.assertFalse(path.parent.exists())
            store = MarkdownReferenceLibraryStore(GuardedFileWriter(), path)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            path.write_text(raw, encoding="utf-8")
            path.chmod(0o600)
            snapshot = store.load()
            self.assertTrue(snapshot.diagnostics)
            self.assertEqual(snapshot.diagnostics[0].kind, "duplicate-key")
            self.assertEqual(snapshot.diagnostics[0].line, 7)


    def test_desktop_scenario_freezes_document_and_reference_navigation_preconditions(self):
        scenario = (ROOT / "tests/desktop/scenarios/plus_academic_review.py").read_text(encoding="utf-8")
        for marker in (
            'window.core.editor.initialize_new_text("Alpha  beta.\\n", clean=True)',
            'category="Writing Hygiene"',
            'message_contains="Multiple consecutive spaces"',
            'assert selected_text == "  "',
            'category="References"',
            'title="Reference Library Source"',
            'assert selected_line == expected_reference_line',
            'assert text_of(window.text_view) == "Clean.\\n"',
        ):
            self.assertIn(marker, scenario)
        self.assertNotIn('.emit("clicked")', scenario)
        self.assertIn('reference_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)', scenario)
        self.assertIn('reference_path.write_text(raw, encoding="utf-8")', scenario)
        self.assertNotIn('window.core.writer.observe_target(str(window.reference_store.path))', scenario)
        self.assertNotIn('window.core.writer.commit(observation, raw.encode("utf-8"))', scenario)


if __name__ == "__main__":
    unittest.main()
