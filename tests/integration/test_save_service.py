from __future__ import annotations
import codecs
import os
from pathlib import Path
import tempfile
import unittest
from graphium.application.document_save_service import DocumentSaveService
from graphium.application.document_session import DocumentSession
from graphium.domain.document_save import SaveBindingError, SaveDisposition, SaveOperation, StaleSaveTargetError
from graphium.domain.document_identity import BomKind, LineEnding
from graphium.domain.document_serialization import DocumentSerializationError, MixedLineEndingConfirmationRequired
from graphium.domain.history import TextHistory
from graphium.infrastructure.document_loader import load_document
from graphium.infrastructure.guarded_file_writer import GuardedFileWriter
class SaveServiceTests(unittest.TestCase):
    def _open_session(self, path: Path):
        history = TextHistory()
        session = DocumentSession()
        loaded = load_document(str(path))
        state = history.reset(loaded.text)
        session.establish_open(loaded, state)
        return (history, session)
    def _edit(self, history: TextHistory, session: DocumentSession, text: str):
        history.commit(text)
        session.commit_history_state(history.current)
        return history.current
    def test_ordinary_save_marks_exact_current_state_clean_and_refreshes_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(b'A\n')
            history, session = self._open_session(path)
            edited = self._edit(history, session, 'AB\n')
            self.assertTrue(session.modified)
            result = DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
            self.assertEqual(path.read_bytes(), b'AB\n')
            self.assertEqual(result.editor_state_id, edited.state_id)
            self.assertEqual(result.disposition, SaveDisposition.COMMITTED_CONFIRMED)
            self.assertFalse(session.modified)
            self.assertEqual(session.file_state.content_fingerprint, result.committed_fingerprint)
    def test_newer_edit_during_save_remains_dirty_after_older_state_commits(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(b'A\n')
            history, session = self._open_session(path)
            ab = self._edit(history, session, 'AB\n')
            def hook(phase, _ctx):
                if phase == 'before_namespace_commit':
                    self._edit(history, session, 'ABC\n')
            result = DocumentSaveService(session=session, writer=GuardedFileWriter(test_hook=hook)).save()
            self.assertEqual(result.editor_state_id, ab.state_id)
            self.assertEqual(path.read_bytes(), b'AB\n')
            self.assertEqual(session.saved_editor_state_id, ab.state_id)
            self.assertNotEqual(session.current_editor_state_id, ab.state_id)
            self.assertTrue(session.modified)
    def test_save_as_new_binds_only_after_commit(self):
        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / 'old.txt'
            new = Path(td) / 'new.txt'
            old.write_bytes(b'A\n')
            history, session = self._open_session(old)
            self._edit(history, session, 'AB\n')
            writer = GuardedFileWriter()
            obs = writer.observe_target(str(new))
            self.assertEqual(session.file_path, os.path.abspath(str(old)))
            result = DocumentSaveService(session=session, writer=writer).save_as(obs)
            self.assertEqual(result.operation, SaveOperation.SAVE_AS)
            self.assertEqual(new.read_bytes(), b'AB\n')
            self.assertEqual(session.file_path, os.path.abspath(str(new)))
            self.assertFalse(session.modified)
    def test_failed_save_as_keeps_old_binding_and_saved_relation(self):
        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / 'old.txt'
            new = Path(td) / 'new.txt'
            old.write_bytes(b'A\n')
            history, session = self._open_session(old)
            self._edit(history, session, 'AB\n')
            prior_saved = session.saved_editor_state_id
            def hook(phase, _ctx):
                if phase == 'before_late_revalidation':
                    new.write_bytes(b'competitor')
            writer = GuardedFileWriter(test_hook=hook)
            obs = writer.observe_target(str(new))
            with self.assertRaises(StaleSaveTargetError):
                DocumentSaveService(session=session, writer=writer).save_as(obs)
            self.assertEqual(session.file_path, os.path.abspath(str(old)))
            self.assertEqual(session.saved_editor_state_id, prior_saved)
            self.assertTrue(session.modified)
            self.assertEqual(new.read_bytes(), b'competitor')
    def test_save_as_overwrite_uses_source_representation_not_destination_representation(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source.txt'
            target = Path(td) / 'target.txt'
            source.write_bytes(codecs.BOM_UTF16_LE + 'A\r\n'.encode('utf-16-le'))
            target.write_bytes(b'old utf8\n')
            history, session = self._open_session(source)
            self._edit(history, session, 'AB\n')
            writer = GuardedFileWriter()
            obs = writer.observe_target(str(target))
            result = DocumentSaveService(session=session, writer=writer).save_as(obs)
            self.assertEqual(result.operation, SaveOperation.SAVE_AS)
            self.assertTrue(target.read_bytes().startswith(codecs.BOM_UTF16_LE))
            self.assertEqual(load_document(str(target)).text, 'AB\n')
            self.assertEqual(session.file_path, os.path.abspath(str(target)))
    def test_mixed_eol_requires_consent_before_any_target_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'mixed.txt'
            original = b'A\r\nB\nC\r\n'
            path.write_bytes(original)
            history, session = self._open_session(path)
            self._edit(history, session, 'A\nB2\nC\n')
            service = DocumentSaveService(session=session, writer=GuardedFileWriter())
            with self.assertRaises(MixedLineEndingConfirmationRequired):
                service.save()
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(session.modified)
    def test_mixed_eol_consent_normalizes_to_dominant_style(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'mixed.txt'
            path.write_bytes(b'A\r\nB\nC\r\n')
            history, session = self._open_session(path)
            self._edit(history, session, 'A\nB2\nC\n')
            DocumentSaveService(session=session, writer=GuardedFileWriter()).save(allow_mixed_eol_normalization=True)
            self.assertEqual(path.read_bytes(), b'A\r\nB2\r\nC\r\n')
            self.assertFalse(session.modified)
    def test_untitled_save_as_uses_utf8_no_bom_lf(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'new.txt'
            history = TextHistory()
            session = DocumentSession()
            state = history.reset('A\nB\n')
            session.establish_new(state, clean=False)
            writer = GuardedFileWriter()
            result = DocumentSaveService(session=session, writer=writer).save_as(writer.observe_target(str(target)))
            self.assertEqual(target.read_bytes(), b'A\nB\n')
            self.assertEqual(result.file_state.load.encoding, 'utf-8')
            self.assertEqual(result.file_state.load.bom.value, 'none')
            self.assertFalse(session.modified)
    def test_explicit_representation_change_is_written_and_becomes_clean(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "doc.txt"; path.write_bytes(b"A\nB\n")
            _history, session = self._open_session(path)
            session.select_representation_encoding("utf-16-le", BomKind.UTF16_LE)
            session.select_representation_line_ending(LineEnding.CRLF)
            DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
            self.assertEqual(path.read_bytes(), codecs.BOM_UTF16_LE + "A\r\nB\r\n".encode("utf-16-le"))
            self.assertFalse(session.modified)
            self.assertEqual(session.current_representation_profile, session.saved_representation_profile)
    def test_explicit_line_ending_selection_on_mixed_source_needs_no_second_consent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mixed.txt"; path.write_bytes(b"A\r\nB\nC\r\n")
            _history, session = self._open_session(path)
            session.select_representation_line_ending(LineEnding.LF)
            DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
            self.assertEqual(path.read_bytes(), b"A\nB\nC\n"); self.assertFalse(session.modified)
    def test_late_save_of_older_representation_does_not_clean_newer_choice(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "doc.txt"; path.write_bytes(b"A\n")
            _history, session = self._open_session(path)
            session.select_representation_line_ending(LineEnding.CRLF)
            def hook(phase, _ctx):
                if phase == "before_namespace_commit": session.select_representation_line_ending(LineEnding.CR)
            DocumentSaveService(session=session, writer=GuardedFileWriter(test_hook=hook)).save()
            self.assertEqual(path.read_bytes(), b"A\r\n")
            self.assertEqual(session.saved_representation_profile.line_ending, LineEnding.CRLF)
            self.assertEqual(session.current_representation_profile.line_ending, LineEnding.CR)
            self.assertTrue(session.modified)
    def test_postcommit_observation_wins_when_eol_choice_has_no_physical_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "one-line.txt"; path.write_bytes(b"A")
            _history, session = self._open_session(path)
            session.select_representation_line_ending(LineEnding.CRLF)
            DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
            self.assertEqual(path.read_bytes(), b"A")
            self.assertEqual(session.current_representation_profile.line_ending, LineEnding.LF)
            self.assertEqual(session.current_representation_profile, session.saved_representation_profile)
            self.assertFalse(session.modified)
    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlinks required')
    def test_save_as_alias_of_active_file_routes_ordinary_save_without_rebind(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source.txt'
            alias = Path(td) / 'alias.txt'
            source.write_bytes(b'A\n')
            alias.symlink_to(source.name)
            history, session = self._open_session(source)
            self._edit(history, session, 'AB\n')
            writer = GuardedFileWriter()
            result = DocumentSaveService(session=session, writer=writer).save_as(writer.observe_target(str(alias)))
            self.assertEqual(result.operation, SaveOperation.SAVE)
            self.assertEqual(source.read_bytes(), b'AB\n')
            self.assertEqual(session.file_path, os.path.abspath(str(source)))
    def test_unstable_pending_session_state_rejects_save(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(b'A\n')
            _history, session = self._open_session(path)
            session.observe_uncommitted_text('AB\n')
            with self.assertRaises(SaveBindingError):
                DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
            self.assertEqual(path.read_bytes(), b'A\n')
    def test_committed_baseline_unavailable_keeps_named_binding_but_blocks_next_save(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(b'A\n')
            history, session = self._open_session(path)
            self._edit(history, session, 'AB\n')
            def hook(phase, _ctx):
                if phase == 'before_postcommit_load':
                    raise RuntimeError('injected')
            result = DocumentSaveService(session=session, writer=GuardedFileWriter(test_hook=hook)).save()
            self.assertEqual(result.disposition, SaveDisposition.COMMITTED_BASELINE_UNAVAILABLE)
            self.assertEqual(path.read_bytes(), b'AB\n')
            self.assertEqual(session.file_path, os.path.abspath(str(path)))
            self.assertIsNone(session.file_state)
            self.assertFalse(session.modified)
            with self.assertRaises(SaveBindingError):
                DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
    def test_stale_ordinary_save_never_moves_savepoint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(b'A\n')
            history, session = self._open_session(path)
            self._edit(history, session, 'AB\n')
            prior_saved = session.saved_editor_state_id
            path.write_bytes(b'external\n')
            with self.assertRaises(StaleSaveTargetError):
                DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
            self.assertEqual(session.saved_editor_state_id, prior_saved)
            self.assertTrue(session.modified)
            self.assertEqual(path.read_bytes(), b'external\n')
    def test_nul_text_fails_serialization_before_disk_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'doc.txt'
            path.write_bytes(b'A\n')
            history, session = self._open_session(path)
            self._edit(history, session, 'A\x00B\n')
            with self.assertRaises(DocumentSerializationError):
                DocumentSaveService(session=session, writer=GuardedFileWriter()).save()
            self.assertEqual(path.read_bytes(), b'A\n')
            self.assertTrue(session.modified)
    def test_save_as_existing_target_change_after_observation_fails_without_rebind(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source.txt'
            target = Path(td) / 'target.txt'
            source.write_bytes(b'A\n')
            target.write_bytes(b'T\n')
            history, session = self._open_session(source)
            self._edit(history, session, 'AB\n')
            writer = GuardedFileWriter()
            obs = writer.observe_target(str(target))
            target.write_bytes(b'changed externally\n')
            with self.assertRaises(StaleSaveTargetError):
                DocumentSaveService(session=session, writer=writer).save_as(obs)
            self.assertEqual(session.file_path, os.path.abspath(str(source)))
            self.assertEqual(target.read_bytes(), b'changed externally\n')
if __name__ == '__main__':
    unittest.main()
