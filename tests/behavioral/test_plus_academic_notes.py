from __future__ import annotations

from pathlib import Path
import unittest

from graphium.domain.edit_history import DeltaHistory, EditKind, ViewState
from graphium.application.document_session import DocumentSession
from graphium.application.native_editor import NativeEditorController
from tests.behavioral._native_test_support import NativeTestBuffer
from graphium_plus.academic_notes import (
    ScholarlyNoteError,
    ScholarlyNotesMap,
    diagnostics_text,
    plan_insert_footnote,
    plan_insert_inline_note,
    plan_note_navigation,
)


ROOT = Path(__file__).resolve().parents[2]


def replay(source: str, operations) -> str:
    text = source
    for operation in operations:
        if operation.kind is EditKind.INSERT:
            text = text[: operation.offset] + operation.text + text[operation.offset :]
        else:
            actual = text[operation.offset : operation.offset + len(operation.text)]
            if actual != operation.text:
                raise AssertionError((operation.offset, operation.text, actual))
            text = text[: operation.offset] + text[operation.offset + len(operation.text) :]
    return text


class ScholarlyNotesMapTests(unittest.TestCase):
    def test_definition_marker_is_not_counted_as_reference(self):
        text = "Claim.[^1]\n\n[^1]: Evidence.\n"
        note_map = ScholarlyNotesMap.from_text(text)
        self.assertEqual(["1"], [item.label for item in note_map.references])
        self.assertEqual(["1"], [item.label for item in note_map.definitions])
        self.assertEqual((), note_map.diagnostics)

    def test_multiline_definition_is_one_bounded_block(self):
        text = "Text[^long].\n\n[^long]: First paragraph.\n    continuation\n\n    second paragraph\nOutside\n"
        note_map = ScholarlyNotesMap.from_text(text)
        definition = note_map.definitions[0]
        block = text[definition.block.start : definition.block.end]
        self.assertIn("second paragraph", block)
        self.assertNotIn("Outside", block)
        self.assertEqual(text.index("First paragraph"), definition.content_start)

    def test_fenced_code_is_opaque_for_all_note_syntax(self):
        text = "```md\n[^x]: fake\nvalue[^x] ^[inline]\n```\nReal[^r].\n\n[^r]: yes\n"
        note_map = ScholarlyNotesMap.from_text(text)
        self.assertEqual(["r"], [item.label for item in note_map.references])
        self.assertEqual(["r"], [item.label for item in note_map.definitions])
        self.assertEqual((), note_map.inline_notes)

    def test_inline_code_and_escaped_reference_are_not_notes(self):
        text = r"`[^code]` and \[^escaped] and real[^ok]." + "\n\n[^ok]: yes\n"
        note_map = ScholarlyNotesMap.from_text(text)
        self.assertEqual(["ok"], [item.label for item in note_map.references])
        self.assertEqual((), note_map.diagnostics)

    def test_inline_notes_preserve_nested_brackets_and_escapes(self):
        text = r"A ^[note with [nested] and \] literal] end"
        note_map = ScholarlyNotesMap.from_text(text)
        self.assertEqual(1, len(note_map.inline_notes))
        note = note_map.inline_notes[0]
        self.assertEqual(r"note with [nested] and \] literal", text[note.content.start : note.content.end])

    def test_duplicate_and_unresolved_are_diagnostics_not_rewrites(self):
        text = "A[^missing] B[^dup].\n\n[^dup]: one\n[^dup]: two\n"
        note_map = ScholarlyNotesMap.from_text(text)
        self.assertEqual(
            ["unresolved_reference", "duplicate_definition"],
            [item.kind for item in note_map.diagnostics],
        )
        report = diagnostics_text(note_map)
        self.assertIn("has no definition", report)
        self.assertIn("more than one definition", report)
        self.assertTrue(note_map.matches_text(text))
        self.assertFalse(note_map.matches_text(text + "x"))


class ScholarlyNoteEditTests(unittest.TestCase):
    def test_insert_footnote_uses_lowest_free_numeric_label_and_one_small_program(self):
        source = "Alpha[^1]. Beta"
        before = ViewState(len(source), len(source))
        plan = plan_insert_footnote(source_text=source, source_state_id=7, before_view=before)
        self.assertEqual("2", plan.label)
        self.assertEqual(source + "[^2]\n\n[^2]: ", plan.final_text)
        self.assertEqual(plan.final_text, replay(source, plan.operations))
        self.assertEqual(2, len(plan.operations))
        self.assertTrue(all(op.kind is EditKind.INSERT for op in plan.operations))
        self.assertEqual(len(plan.final_text), plan.target_view.insert_offset)

    def test_insert_footnote_skips_unresolved_numeric_label_too(self):
        source = "A[^1] B[^3]"
        plan = plan_insert_footnote(
            source_text=source, source_state_id=2,
            before_view=ViewState(len(source), len(source)),
        )
        self.assertEqual("2", plan.label)

    def test_insert_footnote_after_selection_does_not_replace_selected_prose(self):
        source = "selected prose remains"
        before = ViewState(8, 0)
        plan = plan_insert_footnote(source_text=source, source_state_id=3, before_view=before)
        self.assertTrue(plan.final_text.startswith("selected[^1] prose remains"))
        self.assertEqual(plan.final_text, replay(source, plan.operations))

    def test_insert_footnote_in_empty_document_creates_literal_markdown(self):
        plan = plan_insert_footnote(
            source_text="", source_state_id=1, before_view=ViewState(0, 0)
        )
        self.assertEqual("[^1]\n\n[^1]: ", plan.final_text)
        self.assertEqual(plan.final_text, replay("", plan.operations))

    def test_inline_note_wraps_selection_and_preserves_selection_direction(self):
        source = "alpha beta"
        plan = plan_insert_inline_note(
            source_text=source, source_state_id=4, before_view=ViewState(5, 0)
        )
        self.assertEqual("^[alpha] beta", plan.final_text)
        self.assertEqual(plan.final_text, replay(source, plan.operations))
        self.assertEqual((7, 2), (plan.target_view.insert_offset, plan.target_view.selection_bound_offset))

    def test_empty_inline_note_places_caret_between_brackets(self):
        plan = plan_insert_inline_note(
            source_text="abc", source_state_id=4, before_view=ViewState(1, 1)
        )
        self.assertEqual("a^[]bc", plan.final_text)
        self.assertEqual((3, 3), (plan.target_view.insert_offset, plan.target_view.selection_bound_offset))

    def test_multiline_selection_is_rejected_not_silently_flattened(self):
        with self.assertRaises(ScholarlyNoteError):
            plan_insert_inline_note(
                source_text="a\nb", source_state_id=4, before_view=ViewState(3, 0)
            )

    def test_bad_state_or_view_is_rejected_before_programmatic_edit(self):
        with self.assertRaises(ScholarlyNoteError):
            plan_insert_footnote(source_text="x", source_state_id=0, before_view=ViewState(0, 0))
        with self.assertRaises(ScholarlyNoteError):
            plan_insert_footnote(source_text="x", source_state_id=1, before_view=ViewState(2, 2))


class ScholarlyNoteNavigationTests(unittest.TestCase):
    def test_reference_navigates_to_definition_body(self):
        text = "A[^n].\n\n[^n]: body\n"
        caret = text.index("[^n]") + 1
        plan = plan_note_navigation(source_text=text, before_view=ViewState(caret, caret))
        self.assertEqual("reference_to_definition", plan.direction)
        self.assertEqual(text.index("body"), plan.target_view.insert_offset)

    def test_definition_navigates_to_first_reference(self):
        text = "A[^n], again[^n].\n\n[^n]: body\n"
        caret = text.index("body")
        plan = plan_note_navigation(source_text=text, before_view=ViewState(caret, caret))
        self.assertEqual("definition_to_reference", plan.direction)
        self.assertEqual(text.index("[^n]"), plan.target_view.insert_offset)

    def test_unresolved_reference_has_no_navigation_target(self):
        text = "A[^missing]."
        caret = text.index("missing")
        plan = plan_note_navigation(source_text=text, before_view=ViewState(caret, caret))
        self.assertIsNone(plan.target_view)
        self.assertTrue(plan.matches_text(text))
        self.assertFalse(plan.matches_text(text + "x"))


class ScholarlyNoteNativeEditorIntegrationTests(unittest.TestCase):
    @staticmethod
    def make_editor(text: str):
        session = DocumentSession()
        history = DeltaHistory()
        buffer = NativeTestBuffer(text)
        editor = NativeEditorController(session=session, history=history, buffer=buffer)
        editor.initialize_new_text(text, clean=True)
        return session, history, buffer, editor

    def test_insert_footnote_is_one_undoable_programmatic_group(self):
        source = "Claim."
        expected = "Claim.[^1]\n\n[^1]: "
        session, history, buffer, editor = self.make_editor(source)
        buffer.insert = buffer.bound = len(source)
        snap = editor.capture_programmatic_source()
        self.assertEqual((len(source), len(source)), (snap.insert_offset, snap.selection_bound_offset))
        plan = plan_insert_footnote(
            source_text=snap.text, source_state_id=snap.state_id,
            before_view=ViewState(snap.insert_offset, snap.selection_bound_offset),
        )
        self.assertEqual(expected, plan.final_text)
        self.assertEqual((len(expected), len(expected)),
                         (plan.target_view.insert_offset, plan.target_view.selection_bound_offset))
        editor.apply_prevalidated_programmatic_group(
            operations=plan.operations, expected_source_state_id=plan.source_state_id,
            final_text=plan.final_text, before_view=plan.before_view, target_view=plan.target_view,
        )
        self.assertEqual(expected, buffer.text)
        self.assertEqual((len(expected), len(expected)), (buffer.insert, buffer.bound))
        self.assertTrue(session.modified)
        self.assertEqual(1, len(history.undo_stack))
        editor.undo()
        self.assertEqual(source, buffer.text)
        self.assertEqual((len(source), len(source)), (buffer.insert, buffer.bound))
        self.assertFalse(session.modified)
        editor.redo()
        self.assertEqual(expected, buffer.text)
        self.assertEqual((len(expected), len(expected)), (buffer.insert, buffer.bound))

    def test_stale_note_edit_is_rejected_by_existing_editor_authority(self):
        source = "Claim."
        _session, history, buffer, editor = self.make_editor(source)
        snap = editor.capture_programmatic_source()
        plan = plan_insert_footnote(
            source_text=snap.text, source_state_id=snap.state_id,
            before_view=ViewState(snap.insert_offset, snap.selection_bound_offset),
        )
        buffer.user_insert(editor, len(source), " changed")
        with self.assertRaisesRegex(RuntimeError, "stale programmatic"):
            editor.apply_prevalidated_programmatic_group(
                operations=plan.operations, expected_source_state_id=plan.source_state_id,
                final_text=plan.final_text, before_view=plan.before_view, target_view=plan.target_view,
            )
        self.assertEqual("Claim. changed", buffer.text)
        self.assertEqual(1, len(history.undo_stack))



class AcademicGtkIntegrationSourceTests(unittest.TestCase):
    def test_plus_window_exposes_academic_actions_through_single_markdown_command_authority(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text()
        catalog = (ROOT / "graphium_plus/markdown_commands.py").read_text()
        for action in ("insert-footnote", "insert-inline-note", "go-to-footnote", "check-footnotes"):
            self.assertIn(f'MarkdownCommandSpec("{action}"', catalog)
        self.assertIn('commands_item = Gtk.MenuItem(label="Commands")', source)
        self.assertIn('markdown_item = Gtk.MenuItem(label="Markdown")', source)
        self.assertNotIn('Gtk.MenuItem(label="Academic")', source)
        self.assertIn("apply_prevalidated_programmatic_group", source)
        core_catalog = (ROOT / "graphium/application/commands.py").read_text()
        self.assertNotIn("insert-footnote", core_catalog)
        self.assertNotIn("insert-inline-note", core_catalog)

    def test_academic_authority_is_gtk_io_and_framework_free(self):
        source = (ROOT / "graphium_plus/academic_notes.py").read_text()
        forbidden = ("import gi", "Gtk.", "sqlite", "subprocess", "threading", "asyncio", "requests", "urllib")
        for token in forbidden:
            self.assertNotIn(token, source)
        self.assertIn("ScholarlyNotesMap", source)
        self.assertIn("ReplayOperation", source)


if __name__ == "__main__":
    unittest.main()
