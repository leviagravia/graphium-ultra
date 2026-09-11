from __future__ import annotations

from pathlib import Path
import unittest

from graphium.application.document_session import DocumentSession
from graphium.application.native_editor import NativeEditorController
from graphium.domain.edit_history import DeltaHistory, EditKind, ViewState
from graphium_plus.markdown_commands import (
    ACADEMIC_MARKDOWN_ACTIONS,
    MARKDOWN_COMMANDS,
    MARKDOWN_COMMAND_GROUPS,
    MarkdownCommandError,
    plan_markdown_command,
)
from tests.behavioral._native_test_support import NativeTestBuffer


ROOT = Path(__file__).resolve().parents[2]


def replay(source: str, operations) -> str:
    text = source
    for operation in operations:
        if operation.kind is EditKind.INSERT:
            text = text[:operation.offset] + operation.text + text[operation.offset:]
        else:
            actual = text[operation.offset:operation.offset + len(operation.text)]
            if actual != operation.text:
                raise AssertionError((operation.offset, operation.text, actual))
            text = text[:operation.offset] + text[operation.offset + len(operation.text):]
    return text


def plan(action: str, text: str, view: ViewState, state: int = 7):
    result = plan_markdown_command(
        action, source_text=text, source_state_id=state, before_view=view
    )
    assert replay(text, result.operations) == result.final_text
    return result


class MarkdownCommandCatalogTests(unittest.TestCase):
    def test_catalog_is_one_bounded_complete_pre_citation_surface(self):
        actions = [item.action for item in MARKDOWN_COMMANDS]
        self.assertEqual(len(actions), len(set(actions)))
        self.assertEqual(("Headings", "Inline", "Blocks", "Insert", "Academic"), MARKDOWN_COMMAND_GROUPS)
        for required in (
            "markdown-heading-1", "markdown-heading-6", "markdown-bold",
            "markdown-italic", "markdown-strikethrough", "markdown-inline-code",
            "markdown-blockquote", "markdown-unordered-list", "markdown-ordered-list",
            "markdown-task-list", "markdown-fenced-code", "markdown-thematic-break",
            "markdown-link", "markdown-image", "markdown-table",
            "insert-footnote", "insert-inline-note", "go-to-footnote", "check-footnotes",
        ):
            self.assertIn(required, actions)
        self.assertEqual(
            {"insert-footnote", "insert-inline-note", "go-to-footnote", "check-footnotes"},
            set(ACADEMIC_MARKDOWN_ACTIONS),
        )
        self.assertFalse(any("citation" in action for action in actions))

    def test_catalog_module_is_gtk_io_worker_and_framework_free(self):
        source = (ROOT / "graphium_plus/markdown_commands.py").read_text()
        for token in (
            "import gi", "Gtk.", "Gio.", "sqlite", "threading", "asyncio", "subprocess",
            "requests", "urllib", "Plugin", "ServiceLocator", "Registry",
        ):
            self.assertNotIn(token, source)
        self.assertIn("apply_prevalidated_programmatic_group", source)


class MarkdownInlineCommandTests(unittest.TestCase):
    def test_bold_wraps_selection_and_preserves_inner_selection_direction(self):
        result = plan("markdown-bold", "alpha beta", ViewState(5, 0))
        self.assertEqual("**alpha** beta", result.final_text)
        self.assertEqual((7, 2), (result.target_view.insert_offset, result.target_view.selection_bound_offset))

    def test_bold_removes_exact_surrounding_markup_instead_of_nesting_it(self):
        result = plan("markdown-bold", "**alpha**", ViewState(7, 2))
        self.assertEqual("alpha", result.final_text)
        self.assertEqual((5, 0), (result.target_view.insert_offset, result.target_view.selection_bound_offset))

    def test_empty_inline_command_inserts_pair_and_places_caret_between_markers(self):
        result = plan("markdown-italic", "ab", ViewState(1, 1))
        self.assertEqual("a**b", result.final_text)
        self.assertEqual((2, 2), (result.target_view.insert_offset, result.target_view.selection_bound_offset))

    def test_inline_code_uses_a_longer_backtick_fence_when_selection_contains_backticks(self):
        source = "a `b` c"
        result = plan("markdown-inline-code", source, ViewState(len(source), 0))
        self.assertEqual("``a `b` c``", result.final_text)

    def test_inline_commands_reject_multiline_selection(self):
        with self.assertRaises(MarkdownCommandError):
            plan_markdown_command(
                "markdown-bold", source_text="a\nb", source_state_id=1,
                before_view=ViewState(3, 0),
            )


class MarkdownBlockCommandTests(unittest.TestCase):
    def test_heading_command_canonicalizes_setext_to_requested_atx_level(self):
        source = "Title\n---\nbody"
        result = plan("markdown-heading-3", source, ViewState(2, 2))
        self.assertEqual("### Title\nbody", result.final_text)
        self.assertEqual((9, 9), (result.target_view.insert_offset, result.target_view.selection_bound_offset))

    def test_heading_command_rejects_multiline_selection(self):
        with self.assertRaises(MarkdownCommandError):
            plan_markdown_command(
                "markdown-heading-2", source_text="a\nb", source_state_id=1,
                before_view=ViewState(3, 0),
            )

    def test_blockquote_and_list_commands_normalize_only_selected_source_lines(self):
        source = "alpha\nbeta\ngamma"
        quoted = plan("markdown-blockquote", source, ViewState(10, 0))
        self.assertEqual("> alpha\n> beta\ngamma", quoted.final_text)
        numbered = plan("markdown-ordered-list", source, ViewState(10, 0))
        self.assertEqual("1. alpha\n2. beta\ngamma", numbered.final_text)
        tasks = plan("markdown-task-list", "- alpha\n* beta", ViewState(14, 0))
        self.assertEqual("- [ ] alpha\n- [ ] beta", tasks.final_text)
        indented = plan("markdown-blockquote", "  alpha", ViewState(7, 7))
        self.assertEqual("  > alpha", indented.final_text)

    def test_fenced_code_chooses_safe_marker_and_keeps_selected_body_selected(self):
        source = "line ``` literal"
        result = plan("markdown-fenced-code", source, ViewState(len(source), 0))
        self.assertEqual("````\nline ``` literal\n````", result.final_text)
        self.assertEqual("line ``` literal", result.final_text[
            min(result.target_view.insert_offset, result.target_view.selection_bound_offset):
            max(result.target_view.insert_offset, result.target_view.selection_bound_offset)
        ])
        with_trailing_newline = plan(
            "markdown-fenced-code", "one\ntwo\n", ViewState(8, 0)
        )
        self.assertEqual("```\none\ntwo\n```", with_trailing_newline.final_text)

    def test_thematic_break_and_table_are_insertions_not_hidden_document_authorities(self):
        hr = plan("markdown-thematic-break", "abc", ViewState(3, 0))
        self.assertEqual("abc\n---\n", hr.final_text)
        table = plan("markdown-table", "abc", ViewState(3, 3))
        self.assertEqual(
            "abc\n| Column 1 | Column 2 |\n| --- | --- |\n|  |  |\n",
            table.final_text,
        )


class MarkdownInsertCommandTests(unittest.TestCase):
    def test_link_and_image_wrap_single_line_selection_without_fetching_anything(self):
        link = plan("markdown-link", "Graphium", ViewState(8, 0))
        self.assertEqual("[Graphium](url)", link.final_text)
        image = plan("markdown-image", "figure", ViewState(6, 0))
        self.assertEqual("![figure](path)", image.final_text)

    def test_empty_link_inserts_literal_template(self):
        result = plan("markdown-link", "ab", ViewState(1, 1))
        self.assertEqual("a[link text](url)b", result.final_text)
        lo = min(result.target_view.insert_offset, result.target_view.selection_bound_offset)
        hi = max(result.target_view.insert_offset, result.target_view.selection_bound_offset)
        self.assertEqual("link text", result.final_text[lo:hi])

    def test_academic_actions_are_catalogued_but_not_reimplemented_by_generic_planner(self):
        for action in ACADEMIC_MARKDOWN_ACTIONS:
            with self.assertRaises(MarkdownCommandError):
                plan_markdown_command(
                    action, source_text="x", source_state_id=1, before_view=ViewState(1, 1)
                )


class MarkdownNativeEditorIntegrationTests(unittest.TestCase):
    def test_markdown_plan_uses_existing_one_group_undo_and_stale_authority(self):
        source = "alpha"
        session = DocumentSession()
        history = DeltaHistory()
        buffer = NativeTestBuffer(source)
        editor = NativeEditorController(session=session, history=history, buffer=buffer)
        editor.initialize_new_text(source, clean=True)
        buffer.insert = len(source)
        buffer.bound = 0
        snapshot = editor.capture_programmatic_source()
        result = plan_markdown_command(
            "markdown-bold", source_text=snapshot.text, source_state_id=snapshot.state_id,
            before_view=ViewState(snapshot.insert_offset, snapshot.selection_bound_offset),
        )
        editor.apply_prevalidated_programmatic_group(
            operations=result.operations, expected_source_state_id=result.source_state_id,
            final_text=result.final_text, before_view=result.before_view,
            target_view=result.target_view,
        )
        self.assertEqual("**alpha**", buffer.text)
        self.assertEqual(1, len(history.undo_stack))
        self.assertTrue(session.modified)
        editor.undo()
        self.assertEqual(source, buffer.text)
        self.assertFalse(session.modified)
        editor.redo()
        self.assertEqual("**alpha**", buffer.text)

        stale = result
        buffer.user_insert(editor, len(buffer.text), "!")
        with self.assertRaisesRegex(RuntimeError, "stale programmatic"):
            editor.apply_prevalidated_programmatic_group(
                operations=stale.operations, expected_source_state_id=stale.source_state_id,
                final_text=stale.final_text, before_view=stale.before_view,
                target_view=stale.target_view,
            )


class MarkdownGtkSourceContractTests(unittest.TestCase):
    def test_plus_window_builds_commands_markdown_from_one_catalog_and_removes_old_academic_menu(self):
        source = (ROOT / "graphium_plus/adapters/gtk/window.py").read_text()
        self.assertIn('commands_item = Gtk.MenuItem(label="Commands")', source)
        self.assertIn('markdown_item = Gtk.MenuItem(label="Markdown")', source)
        self.assertIn("for spec in MARKDOWN_COMMANDS", source)
        self.assertIn('action.connect("activate", self._action_markdown_edit)', source)
        self.assertNotIn('Gtk.MenuItem(label="Academic")', source)
        self.assertNotIn("_install_academic_notes_controls", source)
        core_catalog = (ROOT / "graphium/application/commands.py").read_text()
        self.assertNotIn("markdown-heading-1", core_catalog)
        self.assertNotIn("insert-footnote", core_catalog)


if __name__ == "__main__":
    unittest.main()
