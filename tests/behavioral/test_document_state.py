from __future__ import annotations
import importlib, sys, types, unittest
from unittest.mock import patch
from graphium.application.document_session import DocumentSession, DocumentSessionPhase
from graphium.domain.document_identity import (BomKind, ContentFingerprint, DiskObservation,
    DocumentFileBinding, DocumentFileState, DocumentLoadMetadata, DocumentLoadResult,
    FileObjectIdentity, LineEnding, LineEndingProfile)
from graphium.domain.history import TextHistory

def file_state(path: str='/tmp/example.txt', *, eol=None, object_id=None, fingerprint=None, ctime_ns=None) -> DocumentFileState:
    eol = eol or LineEndingProfile(LineEnding.LF, False, True, lf_count=1)
    return DocumentFileState(DocumentFileBinding(path, path, object_id or FileObjectIdentity(1, 2)),
        DocumentLoadMetadata('utf-8', BomKind.NONE, eol), DiskObservation(2, 10, 33188, False, ctime_ns=ctime_ns),
        fingerprint or ContentFingerprint('sha256', '00' * 32))

class DocumentSessionTests(unittest.TestCase):
    def setUp(self): self.history, self.session = TextHistory(), DocumentSession()
    def _new(self, text='A', *, clean=True):
        state = self.history.reset(text); self.session.establish_new(state, clean=clean); return state

    def test_new_blank_can_be_clean_and_template_new_can_be_dirty(self):
        self._new('', clean=True); self.assertFalse(self.session.modified); self.assertIsNone(self.session.file_state)
        self._new('template', clean=False); self.assertTrue(self.session.modified); self.assertEqual(self.session.text, 'template')

    def test_open_is_clean_and_retains_loaded_file_state(self):
        fs=file_state(); self.session.establish_open(DocumentLoadResult('A\n', fs, False), self.history.reset('A\n'))
        self.assertFalse(self.session.modified); self.assertEqual(self.session.file_state, fs); self.assertEqual(self.session.file_path, '/tmp/example.txt')

    def test_modified_is_state_identity_relation_not_text_digest(self):
        self._new(); self.history.commit('AB'); saved=self.history.current; self.session.commit_history_state(saved); self.session.accept_saved_state(saved.state_id)
        self.history.undo(saved); self.session.commit_history_state(self.history.current); self.history.commit('AB'); branched=self.history.current; self.session.commit_history_state(branched)
        self.assertEqual(self.session.text, 'AB'); self.assertNotEqual(branched.state_id, saved.state_id); self.assertTrue(self.session.modified)

    def test_pending_native_text_is_dirty_until_reconciled_to_stable_state(self):
        saved=self._new(); self.assertTrue(self.session.observe_uncommitted_text('AB')); self.assertTrue(self.session.modified); self.assertIsNone(self.session.current_editor_state_id)
        self.session.observe_uncommitted_text('A'); self.assertTrue(self.session.reconcile_with_history(saved)); self.assertFalse(self.session.modified)

    def test_late_save_of_older_state_does_not_clean_newer_current_state(self):
        self._new(); self.history.commit('AB'); ab=self.history.current; self.session.commit_history_state(ab); self.history.commit('ABC'); abc=self.history.current; self.session.commit_history_state(abc)
        self.session.accept_saved_state(ab.state_id); self.assertEqual(self.session.saved_editor_state_id, ab.state_id); self.assertEqual(self.session.current_editor_state_id, abc.state_id); self.assertTrue(self.session.modified)

    def test_accept_current_saved_state_can_update_file_baseline_without_writing(self):
        a=self._new(clean=False); fs=file_state(); self.session.accept_saved_state(a.state_id, file_state=fs)
        self.assertFalse(self.session.modified); self.assertEqual(self.session.file_state, fs)

    def test_representation_profile_is_part_of_saved_modified_relation(self):
        self._new('A\n'); saved=self.session.saved_representation_profile
        self.assertTrue(self.session.select_representation_encoding('utf-8', BomKind.UTF8)); self.assertTrue(self.session.modified); self.assertEqual(self.session.text, 'A\n')
        self.assertFalse(self.session.select_representation_encoding('utf-8', BomKind.UTF8)); self.assertTrue(self.session.select_representation_encoding(saved.encoding, saved.bom)); self.assertFalse(self.session.modified)

    def test_text_undo_does_not_discard_pending_representation_choice(self):
        self._new(); self.session.select_representation_line_ending(LineEnding.CRLF); self.history.commit('AB'); self.session.commit_history_state(self.history.current)
        self.history.undo(self.history.current); self.session.commit_history_state(self.history.current)
        self.assertEqual(self.session.current_editor_state_id, self.session.saved_editor_state_id); self.assertEqual(self.session.current_representation_profile.line_ending, LineEnding.CRLF); self.assertTrue(self.session.modified)

    def test_mixed_source_line_ending_selection_is_explicit_normalization_consent(self):
        eol=LineEndingProfile(LineEnding.CRLF, True, True, lf_count=1, crlf_count=2); fs=file_state(eol=eol); text='A\nB\nC\n'
        self.session.establish_open(DocumentLoadResult(text, fs, False), self.history.reset(text)); self.session.select_representation_encoding('utf-8', BomKind.UTF8)
        self.assertTrue(self.session.current_representation_profile.mixed_source); self.session.select_representation_line_ending(LineEnding.LF)
        self.assertFalse(self.session.current_representation_profile.mixed_source); self.assertTrue(self.session.modified)

    def test_only_frozen_representation_targets_are_selectable(self):
        self._new('')
        with self.assertRaises(ValueError): self.session.select_representation_encoding('ascii', BomKind.NONE)
        with self.assertRaises(ValueError): self.session.select_representation_encoding('utf-16-le', BomKind.NONE)
        with self.assertRaises(ValueError): self.session.select_representation_line_ending(LineEnding.NONE)

    def test_replacement_phase_suppresses_uncommitted_observation(self):
        self._new()
        with self.session.replacement(DocumentSessionPhase.OPENING): self.assertTrue(self.session.loading); self.assertFalse(self.session.observe_uncommitted_text('spurious'))
        self.assertFalse(self.session.loading); self.assertEqual(self.session.text, 'A'); self.assertFalse(self.session.modified)

    def test_checkpoint_restore_is_exact(self):
        self._new(); snapshot=self.session.snapshot(); self.session.select_representation_encoding('utf-8', BomKind.UTF8); self.session.observe_uncommitted_text('AB'); self.session.invalidate_saved_relation(); self.session.restore_checkpoint(snapshot)
        self.assertEqual(self.session.snapshot(), snapshot)


    def test_path_only_retarget_preserves_text_dirty_relation_and_editor_state_ids(self):
        accepted=file_state('/tmp/old.md', ctime_ns=10)
        self.session.establish_open(DocumentLoadResult('A\n', accepted, False), self.history.reset('A\n'))
        self.history.commit('A\nchanged'); self.session.commit_history_state(self.history.current)
        before=self.session.snapshot(); self.assertTrue(before.modified)
        moved=file_state('/tmp/new.md', object_id=accepted.binding.object_id,
            fingerprint=accepted.content_fingerprint, ctime_ns=99)
        self.session.retarget_file_binding(moved)
        after=self.session.snapshot()
        self.assertEqual(after.logical_path, '/tmp/new.md'); self.assertEqual(after.file_state, moved)
        self.assertEqual(after.text, before.text); self.assertEqual(after.modified, before.modified)
        self.assertEqual(after.current_editor_state_id, before.current_editor_state_id)
        self.assertEqual(after.saved_editor_state_id, before.saved_editor_state_id)
        self.assertEqual(after.text_editor_state_id, before.text_editor_state_id)
        self.assertEqual(after.current_representation_profile, before.current_representation_profile)
        self.assertEqual(after.saved_representation_profile, before.saved_representation_profile)
        self.assertEqual(after.revision, before.revision + 1)

    def test_path_only_retarget_rejects_different_object_content_or_representation_without_mutation(self):
        accepted=file_state('/tmp/old.md')
        self.session.establish_open(DocumentLoadResult('A\n', accepted, False), self.history.reset('A\n'))
        before=self.session.snapshot()
        bad_states=(
            file_state('/tmp/new.md', object_id=FileObjectIdentity(1, 99)),
            file_state('/tmp/new.md', fingerprint=ContentFingerprint('sha256', '11' * 32)),
            file_state('/tmp/new.md', eol=LineEndingProfile(LineEnding.CRLF, False, True, crlf_count=1)),
        )
        for bad in bad_states:
            with self.subTest(bad=bad), self.assertRaises(ValueError): self.session.retarget_file_binding(bad)
            self.assertEqual(self.session.snapshot(), before)

class ExternalMonitorStateMachineTests(unittest.TestCase):
    def test_pending_delivery_semantics_are_scheduler_independent(self):
        gi=types.ModuleType('gi'); gi.require_version=lambda *_args: None; repository=types.ModuleType('gi.repository'); names='CHANGED DELETED CREATED ATTRIBUTE_CHANGED MOVED RENAMED MOVED_IN MOVED_OUT CHANGES_DONE_HINT'.split()
        repository.Gio=type('Gio', (), {'FileMonitorEvent': type('FileMonitorEvent', (), dict(zip(names, range(len(names)))))})
        class FakeGLib:
            @staticmethod
            def timeout_add(*_args): raise AssertionError('pending work scheduled a second worker')
            @staticmethod
            def source_remove(_source_id): return True
        repository.GLib=FakeGLib
        with patch.dict(sys.modules, {'gi':gi, 'gi.repository':repository}): sys.modules.pop('graphium.adapters.gtk.external_monitor', None); module=importlib.import_module('graphium.adapters.gtk.external_monitor')
        accepted=file_state(); history=TextHistory(); session=DocumentSession(); session.establish_open(DocumentLoadResult('A\n', accepted, False), history.reset('A\n')); delivered, followups=[], []
        monitor=module.StrongExternalFileMonitor(session=session, on_result=delivered.append, observer=lambda *_a, **_k: None); generation=monitor._generation; ticket=module._ObservationTicket(generation, accepted.binding.logical_path, accepted)
        first=module.CheckNowResult(module.CheckNowStatus.UNCHANGED); latest=module.CheckNowResult(module.CheckNowStatus.CONTENT_CHANGED); monitor._inflight_generation=generation; monitor._schedule_observation(generation, immediate=False); self.assertEqual(monitor._pending_generation, generation)
        with patch.object(module.StrongExternalFileMonitor, '_schedule_observation', autospec=True, side_effect=lambda _self, gen, *, immediate: followups.append((gen, immediate))): monitor._deliver_result(ticket, first)
        self.assertEqual((delivered, followups, monitor._pending_generation, monitor._inflight_generation), ([], [(generation, False)], None, None)); monitor._inflight_generation=generation; monitor._deliver_result(ticket, latest); self.assertEqual(delivered, [latest]); monitor._generation+=1; monitor._inflight_generation=generation; monitor._deliver_result(ticket, first); self.assertEqual(delivered, [latest])

if __name__ == '__main__': unittest.main()
