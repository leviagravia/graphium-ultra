from __future__ import annotations
import unittest
from graphium.domain.history import HistoryState, TextHistory
from graphium.domain.edit_history import DeleteDirection, DeltaHistory, EditKind, ViewState
class HistoryTests(unittest.TestCase):
    def test_state_ids_are_positive_monotonic_and_never_reused_after_branch(self):
        history = TextHistory()
        a = history.reset(HistoryState('A', 1, 1))
        history.commit(HistoryState('AB', 2, 2))
        ab = history.current
        self.assertIsNotNone(ab)
        self.assertGreater(ab.state_id, a.state_id)
        target = history.undo(ab)
        self.assertEqual(target.state_id, a.state_id)
        old_redo_id = history.redo_stack[-1].state_id
        history.commit(HistoryState('AX', 2, 2))
        branched = history.current
        self.assertGreater(branched.state_id, old_redo_id)
        self.assertEqual(history.redo_stack, [])
    def test_same_text_on_new_branch_has_distinct_identity(self):
        history = TextHistory()
        a = history.reset('A')
        history.commit('AB')
        saved_ab = history.current
        history.undo(saved_ab)
        history.commit('AB')
        branched_ab = history.current
        self.assertEqual(branched_ab.text, saved_ab.text)
        self.assertNotEqual(branched_ab.state_id, saved_ab.state_id)
        self.assertNotEqual(branched_ab.state_id, a.state_id)
    def test_caret_and_selection_refresh_preserve_text_state_identity_and_direction(self):
        history = TextHistory()
        initial = history.reset(HistoryState('abcdef', 2, 5))
        self.assertTrue(history.replace_current_view_state(HistoryState('abcdef', 5, 1)))
        current = history.current
        self.assertEqual(current.state_id, initial.state_id)
        self.assertEqual((current.insert_offset, current.selection_bound_offset), (5, 1))
        self.assertTrue(current.has_selection)
        self.assertFalse(history.can_undo)
    def test_commit_same_text_is_view_refresh_not_undo_step(self):
        history = TextHistory()
        initial = history.reset(HistoryState('abc', 0, 0))
        self.assertFalse(history.commit(HistoryState('abc', 3, 3)))
        self.assertEqual(history.current.state_id, initial.state_id)
        self.assertEqual(len(history.undo_stack), 1)
    def test_pruning_does_not_reuse_state_ids(self):
        history = TextHistory(max_steps=2, max_snapshot_chars=100, max_total_chars=100)
        first = history.reset('0')
        ids = [first.state_id]
        for value in ('1', '2', '3', '4'):
            history.commit(value)
            ids.append(history.current.state_id)
        self.assertEqual(ids, sorted(set(ids)))
        self.assertGreater(history.current.state_id, first.state_id)
        self.assertLessEqual(len(history.undo_stack), 3)
    def test_large_document_disables_snapshot_undo_but_advances_identity(self):
        history = TextHistory(max_snapshot_chars=4, max_total_chars=20)
        first = history.reset('12345')
        self.assertIsNotNone(history.disabled_reason)
        self.assertFalse(history.can_undo)
        self.assertTrue(history.commit('123456'))
        second = history.current
        self.assertGreater(second.state_id, first.state_id)
        self.assertEqual(len(history.undo_stack), 1)
        self.assertFalse(history.can_undo)
    def test_checkpoint_restore_is_exact(self):
        history = TextHistory()
        history.reset(HistoryState('A', 1, 0))
        history.commit(HistoryState('AB', 2, 1))
        checkpoint = history.checkpoint()
        history.commit('ABC')
        history.undo(history.current)
        allocated_next = history.checkpoint().next_state_id
        history.restore_checkpoint(checkpoint)
        restored = history.checkpoint()
        self.assertEqual(restored.undo_stack, checkpoint.undo_stack)
        self.assertEqual(restored.redo_stack, checkpoint.redo_stack)
        self.assertEqual(restored.disabled_reason, checkpoint.disabled_reason)
        self.assertEqual(restored.next_state_id, allocated_next)
        self.assertGreaterEqual(restored.next_state_id, checkpoint.next_state_id)
    def test_checkpoint_restore_does_not_reuse_speculatively_allocated_ids(self):
        history = TextHistory()
        first = history.reset('A')
        checkpoint = history.checkpoint()
        history.commit('AB')
        speculative_id = history.current.state_id
        history.restore_checkpoint(checkpoint)
        self.assertEqual(history.current.state_id, first.state_id)
        history.commit('AX')
        self.assertGreater(history.current.state_id, speculative_id)
    def test_offsets_are_clamped(self):
        state = HistoryState('abc', insert_offset=99, selection_bound_offset=-3)
        self.assertEqual(state.insert_offset, 3)
        self.assertEqual(state.selection_bound_offset, 0)
class DeltaHistoryTests(unittest.TestCase):
    def test_contiguous_word_typing_coalesces_without_timer(self):
        h = DeltaHistory()
        s1 = h.reset()
        for i, ch in enumerate('abc'):
            h.begin_group(ViewState(i, i))
            h.record_insert(i, ch)
            sid = h.end_group(ViewState(i + 1, i + 1), saved_state_id=s1)
        self.assertEqual(len(h.undo_stack), 1)
        self.assertEqual(h.undo_stack[0].deltas[0].text, 'abc')
        self.assertEqual(h.current_state_id, sid)
    def test_whitespace_is_a_structural_boundary(self):
        h = DeltaHistory()
        h.reset()
        for i, ch in enumerate('a b'):
            h.begin_group(ViewState(i, i))
            h.record_insert(i, ch)
            h.end_group(ViewState(i + 1, i + 1))
        self.assertEqual([g.deltas[0].text for g in h.undo_stack], ['a', ' ', 'b'])
    def test_savepoint_boundary_prevents_later_merge_across_saved_state(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState())
        h.record_insert(0, 'a')
        saved = h.end_group(ViewState(1, 1))
        h.begin_group(ViewState(1, 1))
        h.record_insert(1, 'b')
        h.end_group(ViewState(2, 2), saved_state_id=saved)
        self.assertEqual(len(h.undo_stack), 2)
        self.assertEqual(h.undo_stack[0].after_state_id, saved)
    def test_backward_delete_coalesces_by_position(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState(3, 3))
        h.record_delete(2, 'c', direction=DeleteDirection.BACKWARD)
        h.end_group(ViewState(2, 2))
        h.begin_group(ViewState(2, 2))
        h.record_delete(1, 'b', direction=DeleteDirection.BACKWARD)
        h.end_group(ViewState(1, 1))
        d = h.undo_stack[-1].deltas[0]
        self.assertEqual((d.offset, d.text, d.delete_direction), (1, 'bc', DeleteDirection.BACKWARD))
    def test_forward_delete_coalesces_at_same_offset(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState(0, 0))
        h.record_delete(0, 'a', direction=DeleteDirection.FORWARD)
        h.end_group(ViewState(0, 0))
        h.begin_group(ViewState(0, 0))
        h.record_delete(0, 'b', direction=DeleteDirection.FORWARD)
        h.end_group(ViewState(0, 0))
        d = h.undo_stack[-1].deltas[0]
        self.assertEqual((d.offset, d.text), (0, 'ab'))
    def test_undo_redo_replay_and_saved_identity_are_exact(self):
        h = DeltaHistory()
        initial = h.reset()
        h.begin_group(ViewState())
        h.record_insert(0, 'A')
        saved = h.end_group(ViewState(1, 1))
        undo = h.undo()
        self.assertIsNotNone(undo)
        self.assertEqual(undo.target_state_id, initial)
        self.assertEqual(undo.operations[0].kind, EditKind.DELETE)
        redo = h.redo()
        self.assertIsNotNone(redo)
        self.assertEqual(redo.target_state_id, saved)
        self.assertEqual(redo.operations[0].kind, EditKind.INSERT)
    def test_branch_after_undo_never_reuses_state_ids(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState())
        h.record_insert(0, 'a')
        a = h.end_group(ViewState(1, 1))
        h.begin_group(ViewState(1, 1))
        h.record_insert(1, 'b')
        b = h.end_group(ViewState(2, 2))
        h.undo()
        h.begin_group(ViewState(1, 1))
        h.record_insert(1, 'x')
        x = h.end_group(ViewState(2, 2))
        self.assertGreater(x, b)
        self.assertGreater(b, a)
        self.assertFalse(h.can_redo)
    def test_large_document_size_is_not_a_history_disable_switch(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState(10000000, 10000000))
        h.record_insert(10000000, 'X')
        h.end_group(ViewState(10000001, 10000001))
        self.assertTrue(h.can_undo)
        self.assertEqual(h.stored_payload_chars, 1)
    def test_multidelta_replace_is_one_undo_group(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState(1, 3))
        h.record_delete(1, 'bc', direction=DeleteDirection.RANGE)
        h.record_insert(1, 'XY')
        h.end_group(ViewState(3, 3))
        self.assertEqual(len(h.undo_stack), 1)
        plan = h.undo()
        self.assertEqual([(o.kind, o.offset, o.text) for o in plan.operations], [(EditKind.DELETE, 1, 'XY'), (EditKind.INSERT, 1, 'bc')])

    def test_checkpoint_rollback_does_not_reuse_speculative_id(self):
        h = DeltaHistory()
        h.reset()
        cp = h.checkpoint()
        h.begin_group(ViewState())
        h.record_insert(0, 'a')
        speculative = h.end_group(ViewState(1, 1))
        h.restore_checkpoint(cp)
        h.begin_group(ViewState())
        h.record_insert(0, 'b')
        later = h.end_group(ViewState(1, 1))
        self.assertGreater(later, speculative)

    def test_newline_and_space_do_not_merge_into_one_typing_group(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState(0, 0))
        h.record_insert(0, ' ')
        h.end_group(ViewState(1, 1))
        h.begin_group(ViewState(1, 1))
        h.record_insert(1, '\n')
        h.end_group(ViewState(2, 2))
        self.assertEqual(len(h.undo_stack), 2)

    def test_unicode_payload_is_counted_in_text_characters(self):
        h = DeltaHistory()
        h.reset()
        h.begin_group(ViewState(0, 0))
        h.record_insert(0, 'é🙂')
        h.end_group(ViewState(2, 2))
        self.assertEqual(h.stored_payload_chars, 2)
        plan = h.undo()
        self.assertIsNotNone(plan)
        self.assertEqual(plan.operations[0].text, 'é🙂')
