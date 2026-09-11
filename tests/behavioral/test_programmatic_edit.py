from __future__ import annotations
import unittest
from graphium.application.document_session import DocumentSession
from graphium.application.native_editor import NativeEditorController
from graphium.domain.edit_history import DeltaHistory, EditKind, ReplayOperation, ViewState

from tests.behavioral._native_test_support import NativeTestBuffer

class EndGroupFailureHistory(DeltaHistory):
    fail_end = False

    def end_group(self, after_view, *, saved_state_id=None):
        value = super().end_group(after_view, saved_state_id=saved_state_id)
        if self.fail_end:
            raise RuntimeError('injected history commit failure')
        return value

def make(text='abc', *, history=None):
    s = DocumentSession()
    h = history or DeltaHistory()
    b = NativeTestBuffer(text)
    e = NativeEditorController(session=s, history=h, buffer=b)
    e.initialize_new_text(text, clean=True)
    return (s, h, b, e)

class ProgrammaticEditTests(unittest.TestCase):

    def test_programmatic_group_is_one_state_and_one_undo(self):
        s, h, b, e = make('one one')
        old = h.current_state_id
        ops = (ReplayOperation(EditKind.DELETE, 4, 'one'), ReplayOperation(EditKind.INSERT, 4, 'X'), ReplayOperation(EditKind.DELETE, 0, 'one'), ReplayOperation(EditKind.INSERT, 0, 'X'))
        new = e.apply_prevalidated_programmatic_group(operations=ops, expected_source_state_id=old, final_text='X X', before_view=ViewState(7, 7), target_view=ViewState(3, 3))
        self.assertEqual(b.text, 'X X')
        self.assertGreater(new, old)
        self.assertTrue(s.modified)
        self.assertEqual(len(h.undo_stack), 1)
        e.undo()
        self.assertEqual(b.text, 'one one')
        self.assertFalse(s.modified)
        e.redo()
        self.assertEqual(b.text, 'X X')
        self.assertTrue(s.modified)

    def test_stale_plan_fails_before_buffer_mutation(self):
        s, h, b, e = make('abc')
        current = h.current_state_id
        with self.assertRaisesRegex(RuntimeError, 'stale programmatic'):
            e.apply_prevalidated_programmatic_group(operations=(ReplayOperation(EditKind.DELETE, 0, 'a'),), expected_source_state_id=current + 1, final_text='bc', before_view=ViewState(), target_view=ViewState())
        self.assertEqual(b.text, 'abc')
        self.assertEqual(h.current_state_id, current)

    def test_buffer_operation_failure_rolls_back_every_authority(self):
        s, h, b, e = make('abc abc')
        before_s = s.snapshot()
        before_h = h.checkpoint()
        before_text = b.text
        b.fail_after_operations = 2
        ops = (ReplayOperation(EditKind.DELETE, 4, 'abc'), ReplayOperation(EditKind.INSERT, 4, 'X'), ReplayOperation(EditKind.DELETE, 0, 'abc'), ReplayOperation(EditKind.INSERT, 0, 'X'))
        with self.assertRaisesRegex(RuntimeError, 'injected operation'):
            e.apply_prevalidated_programmatic_group(operations=ops, expected_source_state_id=h.current_state_id, final_text='X X', before_view=ViewState(7, 7), target_view=ViewState(3, 3))
        self.assertEqual(b.text, before_text)
        self.assertEqual(s.snapshot(), before_s)
        self.assertEqual(h.current_state_id, before_h.current_state_id)
        self.assertFalse(h.group_active)

    def test_post_buffer_history_failure_rolls_buffer_back(self):
        h = EndGroupFailureHistory()
        s, h, b, e = make('abc', history=h)
        before = s.snapshot()
        old = h.current_state_id
        h.fail_end = True
        with self.assertRaisesRegex(RuntimeError, 'history commit'):
            e.apply_prevalidated_programmatic_group(operations=(ReplayOperation(EditKind.DELETE, 0, 'a'), ReplayOperation(EditKind.INSERT, 0, 'X')), expected_source_state_id=old, final_text='Xbc', before_view=ViewState(3, 3), target_view=ViewState(3, 3))
        self.assertEqual(b.text, 'abc')
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(h.current_state_id, old)
        self.assertFalse(h.group_active)

    def test_undo_payload_budget_rejects_before_mutation(self):
        h = DeltaHistory(max_payload_chars=3)
        s, h, b, e = make('abc', history=h)
        old = h.current_state_id
        with self.assertRaisesRegex(RuntimeError, 'bounded Undo payload'):
            e.apply_prevalidated_programmatic_group(operations=(ReplayOperation(EditKind.DELETE, 0, 'abc'), ReplayOperation(EditKind.INSERT, 0, 'XYZ')), expected_source_state_id=old, final_text='XYZ', before_view=ViewState(), target_view=ViewState())
        self.assertEqual(b.text, 'abc')
        self.assertEqual(h.current_state_id, old)
if __name__ == '__main__':
    unittest.main()
