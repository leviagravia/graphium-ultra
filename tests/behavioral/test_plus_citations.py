from pathlib import Path
import unittest

from graphium.domain.edit_history import ViewState
from graphium_plus.citations import (
    CitationDocumentMap,
    CitationError,
    citation_diagnostics,
    citation_lookup_at,
    format_pandoc_citation,
    plan_insert_citation,
)
from graphium_plus.references import ReferenceRecord


def _records():
    return (
        ReferenceRecord(key="alpha2020", title="Alpha", authors=("Alpha, A",), year="2020"),
        ReferenceRecord(key="beta2021", title="Beta", authors=("Beta, B",), year="2021"),
    )


class PlusCitationAuthorityTests(unittest.TestCase):
    def records(self):
        return _records()

    def test_parses_grouped_textual_and_bracketed_citations(self):
        text = "One [@alpha2020, p. 2]; two [see @beta2021; @gamma2022, ch. 4]; @alpha2020 says."
        cmap = CitationDocumentMap.from_text(text)
        self.assertEqual(tuple(cluster.keys for cluster in cmap.clusters), (
            ("alpha2020",), ("beta2021", "gamma2022"), ("alpha2020",),
        ))
        self.assertEqual(cmap.cited_keys, ("alpha2020", "beta2021", "gamma2022"))
        self.assertTrue(cmap.matches_text(text))
        self.assertFalse(cmap.matches_text(text + "x"))

    def test_code_exclusion_reuses_plus_markdown_authority(self):
        text = (
            "Visible [@alpha2020].\n"
            "`inline [@beta2021]`\n"
            "```markdown\n[@gamma2022]\n```\n"
            "Visible @beta2021.\n"
        )
        self.assertEqual(CitationDocumentMap.from_text(text).cited_keys, ("alpha2020", "beta2021"))

    def test_lookup_under_key_or_group_is_deterministic(self):
        text = "Read [@alpha2020; @beta2021, p. 9]."
        alpha = citation_lookup_at(text, text.index("alpha") + 2)
        beta = citation_lookup_at(text, text.index("beta") + 2)
        between = citation_lookup_at(text, text.index(";"))
        self.assertEqual((alpha.status, alpha.key), ("unique", "alpha2020"))
        self.assertEqual((beta.status, beta.key), ("unique", "beta2021"))
        self.assertEqual((between.status, between.keys), ("ambiguous", ("alpha2020", "beta2021")))

    def test_missing_reference_diagnostics_ignore_code_and_preserve_offsets(self):
        text = "[@alpha2020] [@missing] `[@coded]`"
        cmap = CitationDocumentMap.from_text(text)
        diagnostics = citation_diagnostics(cmap, self.records())
        self.assertEqual(tuple((item.kind, item.key) for item in diagnostics), (("missing-reference", "missing"),))
        self.assertEqual(text[diagnostics[0].offset:diagnostics[0].offset + len("missing")], "missing")

    def test_format_and_insert_plan_are_literal_bounded_and_caret_only(self):
        self.assertEqual(format_pandoc_citation("alpha2020"), "[@alpha2020]")
        self.assertEqual(format_pandoc_citation("alpha2020", " , p. 42\n"), "[@alpha2020, p. 42]")
        before = ViewState(6, 6)
        plan = plan_insert_citation(
            source_text="Claim.", source_state_id=8, before_view=before,
            key="alpha2020", locator="p. 42", records=_records(),
        )
        self.assertEqual(plan.final_text, "Claim.[@alpha2020, p. 42]")
        self.assertEqual(plan.target_view, ViewState(len(plan.final_text), len(plan.final_text)))
        self.assertEqual(len(plan.operations), 1)
        self.assertEqual(plan.operations[0].text, "[@alpha2020, p. 42]")

        with self.assertRaises(CitationError):
            plan_insert_citation(
                source_text="Claim", source_state_id=8, before_view=ViewState(0, 5),
                key="alpha2020", records=_records(),
            )
        with self.assertRaises(CitationError):
            plan_insert_citation(
                source_text="Claim", source_state_id=8, before_view=ViewState(5, 5),
                key="missing", records=_records(),
            )

    def test_citation_module_has_no_second_markdown_code_scanner_or_io(self):
        source = Path(__file__).parents[2] / "graphium_plus" / "citations.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("build_markdown_document_map", text)
        for forbidden in ("_FENCE_RE", "sqlite3", "open(", "os.", "threading", "subprocess"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()

class PlusCitationNativeEditorIntegrationTests(unittest.TestCase):
    def test_insert_citation_is_one_existing_undo_group_and_stale_safe(self):
        from graphium.application.document_session import DocumentSession
        from graphium.application.native_editor import NativeEditorController
        from graphium.domain.edit_history import DeltaHistory
        from tests.behavioral._native_test_support import NativeTestBuffer

        source = "Claim."
        session = DocumentSession()
        history = DeltaHistory()
        buffer = NativeTestBuffer(source)
        editor = NativeEditorController(session=session, history=history, buffer=buffer)
        editor.initialize_new_text(source, clean=True)
        buffer.insert = buffer.bound = len(source)
        snap = editor.capture_programmatic_source()
        plan = plan_insert_citation(
            source_text=snap.text,
            source_state_id=snap.state_id,
            before_view=ViewState(snap.insert_offset, snap.selection_bound_offset),
            key="alpha2020",
            records=_records(),
        )
        expected = "Claim.[@alpha2020]"
        self.assertEqual(plan.final_text, expected)
        editor.apply_prevalidated_programmatic_group(
            operations=plan.operations,
            expected_source_state_id=plan.source_state_id,
            final_text=plan.final_text,
            before_view=plan.before_view,
            target_view=plan.target_view,
        )
        self.assertEqual(buffer.text, expected)
        self.assertTrue(session.modified)
        self.assertEqual(len(history.undo_stack), 1)
        editor.undo()
        self.assertEqual(buffer.text, source)
        self.assertFalse(session.modified)
        editor.redo()
        self.assertEqual(buffer.text, expected)

        stale = editor.capture_programmatic_source()
        stale_plan = plan_insert_citation(
            source_text=stale.text,
            source_state_id=stale.state_id,
            before_view=ViewState(stale.insert_offset, stale.selection_bound_offset),
            key="beta2021",
            records=_records(),
        )
        buffer.user_insert(editor, len(buffer.text), " changed")
        with self.assertRaisesRegex(RuntimeError, "stale programmatic"):
            editor.apply_prevalidated_programmatic_group(
                operations=stale_plan.operations,
                expected_source_state_id=stale_plan.source_state_id,
                final_text=stale_plan.final_text,
                before_view=stale_plan.before_view,
                target_view=stale_plan.target_view,
            )
