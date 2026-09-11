from __future__ import annotations
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from graphium.application.renderability import InteractiveRenderabilityError, MAX_INTERACTIVE_LINE_CHARS
from graphium.application.text_transform import MAX_TRANSFORM_CHANGED_SPANS, TransformInputError, TransformScaleError, build_transformation_plan, plan_duplicate_line_selection, plan_lowercase, plan_move_lines_down, plan_move_lines_up, plan_trim_trailing_spaces, plan_uppercase
from graphium.domain.edit_history import EditKind, ReplayOperation, ViewState
ROOT = Path(__file__).resolve().parents[2]
def apply_ops(source: str, operations: tuple[ReplayOperation, ...]) -> str:
    text = source
    for op in operations:
        if op.kind is EditKind.INSERT:
            text = text[:op.offset] + op.text + text[op.offset:]
        else:
            assert text[op.offset:op.offset + len(op.text)] == op.text
            text = text[:op.offset] + text[op.offset + len(op.text):]
    return text
class PlannerSemanticsTests(unittest.TestCase):
    def test_01_uppercase_without_selection_is_exact_noop(self):
        view = ViewState(2, 2)
        plan = plan_uppercase(source_text='abc', source_state_id=7, before_view=view)
        self.assertEqual(plan.operations, ())
        self.assertEqual(plan.final_text, 'abc')
        self.assertEqual(plan.target_view, view)
        self.assertEqual(plan.changed_span_count, 0)
    def test_02_uppercase_unicode_expansion_preserves_reversed_selection(self):
        view = ViewState(8, 2)
        plan = plan_uppercase(source_text='a straße z', source_state_id=1, before_view=view)
        self.assertEqual(plan.final_text, 'a STRASSE z')
        self.assertEqual(plan.target_view, ViewState(9, 2))
        self.assertEqual(apply_ops(plan.source_text, plan.operations), plan.final_text)
    def test_03_lowercase_dotted_i_expands_and_preserves_forward_selection(self):
        view = ViewState(0, 1)
        plan = plan_lowercase(source_text='İX', source_state_id=1, before_view=view)
        self.assertEqual(plan.final_text, 'i̇X')
        self.assertEqual(plan.target_view, ViewState(0, 2))
    def test_04_unicode_dotless_i_and_combining_marks_are_not_normalized(self):
        p1 = plan_uppercase(source_text='ı', source_state_id=1, before_view=ViewState(1, 0))
        self.assertEqual(p1.final_text, 'I')
        p2 = plan_uppercase(source_text='é', source_state_id=1, before_view=ViewState(2, 0))
        self.assertEqual(p2.final_text, 'É')
    def test_05_already_uppercase_selection_is_exact_noop(self):
        view = ViewState(3, 0)
        plan = plan_uppercase(source_text='ABC', source_state_id=2, before_view=view)
        self.assertFalse(plan.changed)
        self.assertEqual(plan.target_view, view)
    def test_06_duplicate_selection_is_inserted_after_and_selected(self):
        plan = plan_duplicate_line_selection(source_text='abcde', source_state_id=1, before_view=ViewState(1, 4))
        self.assertEqual(plan.final_text, 'abcdbcde')
        self.assertEqual(plan.target_view, ViewState(4, 7))
    def test_07_duplicate_reversed_selection_preserves_direction(self):
        plan = plan_duplicate_line_selection(source_text='abcde', source_state_id=1, before_view=ViewState(4, 1))
        self.assertEqual(plan.final_text, 'abcdbcde')
        self.assertEqual(plan.target_view, ViewState(7, 4))
    def test_08_duplicate_terminated_current_line_keeps_same_column(self):
        plan = plan_duplicate_line_selection(source_text='aa\nbbb\ncc\n', source_state_id=1, before_view=ViewState(5, 5))
        self.assertEqual(plan.final_text, 'aa\nbbb\nbbb\ncc\n')
        self.assertEqual(plan.target_view, ViewState(9, 9))
    def test_09_duplicate_final_line_without_lf_preserves_no_final_lf(self):
        plan = plan_duplicate_line_selection(source_text='aa\nbbb', source_state_id=1, before_view=ViewState(5, 5))
        self.assertEqual(plan.final_text, 'aa\nbbb\nbbb')
        self.assertEqual(plan.target_view, ViewState(9, 9))
    def test_10_duplicate_empty_document_creates_one_lf_and_moves_to_new_empty_line(self):
        plan = plan_duplicate_line_selection(source_text='', source_state_id=1, before_view=ViewState(0, 0))
        self.assertEqual(plan.final_text, '\n')
        self.assertEqual(plan.target_view, ViewState(1, 1))
    def test_11_duplicate_terminal_empty_line_adds_one_more_lf(self):
        plan = plan_duplicate_line_selection(source_text='a\n', source_state_id=1, before_view=ViewState(2, 2))
        self.assertEqual(plan.final_text, 'a\n\n')
        self.assertEqual(plan.target_view, ViewState(3, 3))
    def test_12_move_up_middle_line(self):
        plan = plan_move_lines_up(source_text='a\nb\nc\n', source_state_id=1, before_view=ViewState(2, 2))
        self.assertEqual(plan.final_text, 'b\na\nc\n')
        self.assertEqual(plan.target_view, ViewState(0, 0))
    def test_13_move_down_middle_line_preserves_final_eol(self):
        plan = plan_move_lines_down(source_text='a\nb\nc\n', source_state_id=1, before_view=ViewState(2, 2))
        self.assertEqual(plan.final_text, 'a\nc\nb\n')
        self.assertTrue(plan.final_text.endswith('\n'))
    def test_14_move_down_into_final_line_preserves_no_final_eol(self):
        plan = plan_move_lines_down(source_text='a\nb\nc', source_state_id=1, before_view=ViewState(2, 2))
        self.assertEqual(plan.final_text, 'a\nc\nb')
        self.assertFalse(plan.final_text.endswith('\n'))
    def test_15_move_up_final_line_without_lf_preserves_no_final_eol(self):
        plan = plan_move_lines_up(source_text='a\nb', source_state_id=1, before_view=ViewState(2, 2))
        self.assertEqual(plan.final_text, 'b\na')
        self.assertFalse(plan.final_text.endswith('\n'))
    def test_16_move_boundary_is_exact_noop(self):
        up = plan_move_lines_up(source_text='a\nb', source_state_id=1, before_view=ViewState(0, 0))
        down = plan_move_lines_down(source_text='a\nb', source_state_id=1, before_view=ViewState(2, 2))
        self.assertFalse(up.changed)
        self.assertFalse(down.changed)
    def test_17_move_terminal_sentinel_is_exact_noop(self):
        view = ViewState(4, 4)
        for planner in (plan_move_lines_up, plan_move_lines_down):
            plan = planner(source_text='a\nb\n', source_state_id=1, before_view=view)
            self.assertFalse(plan.changed)
            self.assertEqual(plan.target_view, view)
    def test_18_move_multiline_forward_selection_preserves_geometry(self):
        plan = plan_move_lines_down(source_text='aa\nbbb\ncccc\ndd\n', source_state_id=1, before_view=ViewState(4, 9))
        self.assertEqual(plan.final_text, 'aa\ndd\nbbb\ncccc\n')
        self.assertEqual(plan.target_view, ViewState(7, 12))

    def test_19_move_multiline_reversed_selection_preserves_direction(self):
        plan = plan_move_lines_up(source_text='aa\nbbb\ncccc\ndd\n', source_state_id=1, before_view=ViewState(4, 9))
        self.assertEqual(plan.final_text, 'bbb\ncccc\naa\ndd\n')
        self.assertEqual(plan.target_view, ViewState(1, 6))
        self.assertLess(plan.target_view.insert_offset, plan.target_view.selection_bound_offset)

    def test_20_move_selection_high_endpoint_at_next_line_col0_keeps_block_affinity(self):
        plan = plan_move_lines_down(source_text='aa\nbb\ncc\n', source_state_id=1, before_view=ViewState(3, 1))
        self.assertEqual(plan.final_text, 'bb\naa\ncc\n')
        self.assertEqual(plan.target_view, ViewState(6, 4))

    def test_21_trim_whole_document_removes_only_space_and_tab(self):
        plan = plan_trim_trailing_spaces(source_text='a  \n b\t\n c\xa0 \n', source_state_id=1, before_view=ViewState(0, 0))
        self.assertEqual(plan.final_text, 'a\n b\n c\xa0\n')
        self.assertEqual(plan.changed_span_count, 3)
        self.assertEqual(apply_ops(plan.source_text, plan.operations), plan.final_text)

    def test_22_trim_selection_ending_next_line_col0_excludes_next_line(self):
        plan = plan_trim_trailing_spaces(source_text='a  \nb  \n', source_state_id=1, before_view=ViewState(4, 0))
        self.assertEqual(plan.final_text, 'a\nb  \n')
        self.assertEqual(plan.changed_span_count, 1)

    def test_23_trim_reversed_endpoint_inside_removed_whitespace_clamps_and_preserves_direction(self):
        plan = plan_trim_trailing_spaces(source_text='abc   \nxyz\n', source_state_id=1, before_view=ViewState(4, 1))
        self.assertEqual(plan.final_text, 'abc\nxyz\n')
        self.assertEqual(plan.target_view, ViewState(3, 1))

    def test_24_trim_no_change_is_exact_noop(self):
        view = ViewState(2, 2)
        plan = plan_trim_trailing_spaces(source_text='abc\ndef\n', source_state_id=1, before_view=view)
        self.assertFalse(plan.changed)
        self.assertEqual(plan.target_view, view)

    def test_25_trim_50001_changed_runs_fails_closed_before_operations_materialize(self):
        text = 'x \n' * (MAX_TRANSFORM_CHANGED_SPANS + 1)
        with self.assertRaisesRegex(TransformScaleError, 'changed-span planning cap'):
            plan_trim_trailing_spaces(source_text=text, source_state_id=1, before_view=ViewState())

    def test_26_dispatcher_rejects_unknown_action(self):
        with self.assertRaises(TransformInputError):
            build_transformation_plan('title-case', source_text='abc', source_state_id=1, before_view=ViewState())

    def test_27_invalid_view_offset_is_rejected_before_planning(self):
        with self.assertRaisesRegex(TransformInputError, 'offset exceeds'):
            plan_uppercase(source_text='a', source_state_id=1, before_view=ViewState(2, 0))

    def test_28_trim_50000_changed_runs_is_exact_boundary_pass(self):
        text = 'x \n' * MAX_TRANSFORM_CHANGED_SPANS
        plan = plan_trim_trailing_spaces(source_text=text, source_state_id=1, before_view=ViewState())
        self.assertEqual(plan.changed_span_count, MAX_TRANSFORM_CHANGED_SPANS)
        self.assertEqual(len(plan.operations), MAX_TRANSFORM_CHANGED_SPANS)

    def test_29_uppercase_expansion_that_breaks_renderer_limit_fails_before_plan_return(self):
        text = 'ß' * (MAX_INTERACTIVE_LINE_CHARS // 2 + 1)
        with self.assertRaises(InteractiveRenderabilityError):
            plan_uppercase(source_text=text, source_state_id=1, before_view=ViewState(len(text), 0))

    def test_30_duplicate_that_breaks_renderer_limit_fails_before_plan_return(self):
        text = 'x' * MAX_INTERACTIVE_LINE_CHARS
        with self.assertRaises(InteractiveRenderabilityError):
            plan_duplicate_line_selection(source_text=text, source_state_id=1, before_view=ViewState(len(text), 0))

    def test_31_plan_is_immutable(self):
        plan = plan_lowercase(source_text='ABC', source_state_id=1, before_view=ViewState(3, 0))
        with self.assertRaises(FrozenInstanceError):
            plan.final_text = 'x'
if __name__ == '__main__':
    unittest.main()
