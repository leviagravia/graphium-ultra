from __future__ import annotations
import unittest
from graphium.application.document_session import DocumentSession
from graphium.application.editor_transaction import EditorRollbackError, EditorTransactionController
from graphium.domain.document_identity import BomKind, ContentFingerprint, DiskObservation, DocumentFileBinding, DocumentFileState, DocumentLoadMetadata, DocumentLoadResult, FileObjectIdentity, LineEnding, LineEndingProfile
from graphium.domain.history import HistoryState, TextHistory
class MemoryBuffer:
    def __init__(self, text: str='', insert: int | None=None, bound: int | None=None):
        self.text = text
        self.insert = len(text) if insert is None else insert
        self.bound = self.insert if bound is None else bound
        self.fail_next_restore = False
    def capture(self) -> HistoryState:
        return HistoryState(self.text, self.insert, self.bound)
    def restore(self, state: HistoryState) -> None:
        if self.fail_next_restore:
            self.fail_next_restore = False
            raise RuntimeError('injected restore failure')
        self.text = state.text
        self.insert = state.insert_offset
        self.bound = state.selection_bound_offset
    def set(self, text: str, insert: int | None=None, bound: int | None=None) -> None:
        self.text = text
        self.insert = len(text) if insert is None else insert
        self.bound = self.insert if bound is None else bound
def file_state(path: str='/tmp/open.txt') -> DocumentFileState:
    return DocumentFileState(binding=DocumentFileBinding(path, path, FileObjectIdentity(1, 2)), load=DocumentLoadMetadata('utf-8', BomKind.NONE, LineEndingProfile(LineEnding.LF, False, False)), disk=DiskObservation(1, 10, 33188, False), content_fingerprint=ContentFingerprint('sha256', '11' * 32))
class EditorTransactionTests(unittest.TestCase):
    def make_controller(self, text: str='', *, clean: bool=True):
        buffer = MemoryBuffer(text)
        history = TextHistory()
        session = DocumentSession()
        controller = EditorTransactionController(session=session, history=history, buffer=buffer)
        controller.initialize_new(clean=clean)
        return (buffer, history, session, controller)
    def test_saved_edit_undo_saved_redo_dirty(self):
        buffer, history, session, controller = self.make_controller('A')
        saved_id = session.saved_editor_state_id
        buffer.set('AB')
        controller.observe_native_change()
        self.assertTrue(session.modified)
        controller.commit_native_group()
        self.assertTrue(session.modified)
        controller.undo()
        self.assertEqual(buffer.text, 'A')
        self.assertEqual(session.current_editor_state_id, saved_id)
        self.assertFalse(session.modified)
        controller.redo()
        self.assertEqual(buffer.text, 'AB')
        self.assertTrue(session.modified)
    def test_save_ab_undo_a_dirty_redo_ab_clean(self):
        buffer, history, session, controller = self.make_controller('A')
        buffer.set('AB')
        controller.observe_native_change()
        controller.commit_native_group()
        saved_ab_id = controller.accept_current_as_saved()
        self.assertFalse(session.modified)
        controller.undo()
        self.assertEqual(buffer.text, 'A')
        self.assertTrue(session.modified)
        controller.redo()
        self.assertEqual(buffer.text, 'AB')
        self.assertEqual(session.current_editor_state_id, saved_ab_id)
        self.assertFalse(session.modified)
    def test_type_then_backspace_before_group_commit_returns_clean(self):
        buffer, history, session, controller = self.make_controller('A')
        saved_id = session.saved_editor_state_id
        buffer.set('AB')
        controller.observe_native_change()
        self.assertTrue(session.modified)
        buffer.set('A')
        controller.observe_native_change()
        self.assertEqual(session.current_editor_state_id, saved_id)
        self.assertFalse(session.modified)
        self.assertFalse(controller.commit_native_group())
        self.assertFalse(session.modified)
    def test_branch_after_undo_with_same_saved_text_remains_dirty_by_identity(self):
        buffer, history, session, controller = self.make_controller('A')
        buffer.set('AB')
        controller.observe_native_change()
        controller.commit_native_group()
        saved_old_branch = controller.accept_current_as_saved()
        controller.undo()
        self.assertTrue(session.modified)
        buffer.set('AB')
        controller.observe_native_change()
        controller.commit_native_group()
        self.assertEqual(buffer.text, 'AB')
        self.assertNotEqual(session.current_editor_state_id, saved_old_branch)
        self.assertTrue(session.modified)
        self.assertFalse(history.can_redo)
    def test_caret_selection_only_sync_preserves_state_and_cleanliness(self):
        buffer, history, session, controller = self.make_controller('abcdef')
        saved_id = session.saved_editor_state_id
        buffer.set('abcdef', insert=5, bound=2)
        self.assertTrue(controller.sync_view_state())
        self.assertEqual(history.current.state_id, saved_id)
        self.assertEqual((history.current.insert_offset, history.current.selection_bound_offset), (5, 2))
        self.assertFalse(session.modified)
        self.assertFalse(history.can_undo)
    def test_programmatic_transaction_is_one_undo_step(self):
        buffer, history, session, controller = self.make_controller('A')
        def action():
            buffer.set('ABC', 3, 3)
        result = controller.execute('Expand', action)
        self.assertTrue(result.changed)
        self.assertEqual(len(history.undo_stack), 2)
        controller.undo()
        self.assertEqual(buffer.text, 'A')
    def test_failed_programmatic_transaction_rolls_back_buffer_history_and_session(self):
        buffer, history, session, controller = self.make_controller('A')
        hcp = history.checkpoint()
        scp = session.snapshot()
        before = buffer.capture()
        def action():
            buffer.set('BROKEN')
            raise ValueError('boom')
        with self.assertRaisesRegex(ValueError, 'boom'):
            controller.execute('Broken', action)
        self.assertEqual(buffer.capture(), before)
        self.assertEqual(history.checkpoint(), hcp)
        self.assertEqual(session.snapshot(), scp)
    def test_failed_undo_restore_rolls_back_history_session_and_visible_buffer(self):
        buffer, history, session, controller = self.make_controller('A')
        buffer.set('AB')
        controller.observe_native_change()
        controller.commit_native_group()
        hcp = history.checkpoint()
        scp = session.snapshot()
        before = buffer.capture()
        buffer.fail_next_restore = True
        with self.assertRaisesRegex(RuntimeError, 'injected restore failure'):
            controller.undo()
        self.assertEqual(buffer.capture(), before)
        self.assertEqual(history.checkpoint(), hcp)
        self.assertEqual(session.snapshot(), scp)
    def test_failed_programmatic_rollback_restores_authorities_even_if_buffer_restore_fails(self):
        buffer, history, session, controller = self.make_controller('A')
        hcp = history.checkpoint()
        scp = session.snapshot()
        def action():
            buffer.set('BROKEN')
            buffer.fail_next_restore = True
            raise ValueError('primary failure')
        with self.assertRaises(EditorRollbackError) as caught:
            controller.execute('Broken rollback', action)
        self.assertIsInstance(caught.exception.original_error, ValueError)
        self.assertIsInstance(caught.exception.restore_error, RuntimeError)
        self.assertEqual(history.undo_stack, list(hcp.undo_stack))
        self.assertEqual(history.redo_stack, list(hcp.redo_stack))
        self.assertEqual(session.snapshot(), scp)

    def test_failed_open_initialization_restores_previous_authorities_and_buffer(self):
        buffer, history, session, controller = self.make_controller('old')
        hcp = history.checkpoint()
        scp = session.snapshot()
        before = buffer.capture()
        original_restore = buffer.restore
        calls = {'count': 0}

        def fail_first_restore(state):
            calls['count'] += 1
            if calls['count'] == 1:
                raise RuntimeError('open restore failure')
            return original_restore(state)
        buffer.restore = fail_first_restore
        with self.assertRaisesRegex(RuntimeError, 'open restore failure'):
            controller.initialize_open(DocumentLoadResult('new', file_state(), False))
        self.assertEqual(buffer.capture(), before)
        self.assertEqual(history.undo_stack, list(hcp.undo_stack))
        self.assertEqual(history.redo_stack, list(hcp.redo_stack))
        self.assertEqual(session.snapshot(), scp)

    def test_late_save_specific_state_does_not_clean_newer_current_state(self):
        buffer, history, session, controller = self.make_controller('A')
        buffer.set('AB')
        controller.observe_native_change()
        controller.commit_native_group()
        ab_id = history.current_state_id
        buffer.set('ABC')
        controller.observe_native_change()
        controller.commit_native_group()
        controller.accept_specific_as_saved(ab_id)
        self.assertTrue(session.modified)
        self.assertNotEqual(session.current_editor_state_id, session.saved_editor_state_id)

    def test_open_replaces_buffer_without_creating_native_edit_and_is_clean(self):
        buffer, history, session, controller = self.make_controller('old')
        fs = file_state()
        result = DocumentLoadResult('opened', fs, False)
        controller.initialize_open(result)
        self.assertEqual(buffer.text, 'opened')
        self.assertEqual(session.file_state, fs)
        self.assertFalse(session.modified)
        self.assertEqual(len(history.undo_stack), 1)

    def test_nested_programmatic_transactions_are_rejected_and_outer_rolls_back(self):
        buffer, history, session, controller = self.make_controller('A')

        def outer():
            buffer.set('AB')
            controller.execute('Inner', lambda: buffer.set('ABC'))
        with self.assertRaisesRegex(RuntimeError, 'nested'):
            controller.execute('Outer', outer)
        self.assertEqual(buffer.text, 'A')
        self.assertFalse(session.modified)

    def test_large_document_state_can_be_saved_without_snapshot_undo(self):
        buffer = MemoryBuffer('12345')
        history = TextHistory(max_snapshot_chars=4, max_total_chars=20)
        session = DocumentSession()
        controller = EditorTransactionController(session=session, history=history, buffer=buffer)
        controller.initialize_new(clean=True)
        self.assertFalse(history.can_undo)
        buffer.set('123456')
        controller.observe_native_change()
        controller.commit_native_group()
        self.assertTrue(session.modified)
        controller.accept_current_as_saved()
        self.assertFalse(session.modified)
        self.assertFalse(history.can_undo)
if __name__ == '__main__':
    unittest.main()
