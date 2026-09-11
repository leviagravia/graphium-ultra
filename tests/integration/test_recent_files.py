from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from graphium.application.document_save_service import DocumentSaveService
from graphium.application.document_session import DocumentSession
from graphium.application.file_lifecycle import FileLifecycleController, UnsavedDecision
from graphium.application.native_editor import NativeEditorController
from graphium.application.recent_files import RecentFilesController
from graphium.domain.edit_history import DeltaHistory
from graphium.infrastructure.document_loader import load_document
from graphium.infrastructure.guarded_file_writer import GuardedFileWriter
from tests.integration.test_file_lifecycle import FakeBuffer, FakeUI
import json
import os
import stat
from graphium.application.recent_files import MAX_RECENT_FILES, RecentFilesController
from graphium.infrastructure.recent_files_store import JsonRecentFilesStore
class RecordingRecentStore:
    def __init__(self, *, fail_save: bool=False) -> None:
        self.value: tuple[str, ...] = ()
        self.fail_save = fail_save
        self.save_calls: list[tuple[str, ...]] = []
    def load(self) -> tuple[str, ...]:
        return self.value
    def save(self, paths: tuple[str, ...]) -> None:
        self.save_calls.append(tuple(paths))
        if self.fail_save:
            raise OSError('simulated recent persistence failure')
        self.value = tuple(paths)
class LifecycleRecentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.buffer = FakeBuffer()
        self.session = DocumentSession()
        self.history = DeltaHistory()
        self.editor = NativeEditorController(session=self.session, history=self.history, buffer=self.buffer)
        self.editor.initialize_new_text('', clean=True)
        self.ui = FakeUI()
        self.store = RecordingRecentStore()
        self.recent = RecentFilesController(self.store)
        self.lifecycle = FileLifecycleController(session=self.session, editor=self.editor, save_service=DocumentSaveService(session=self.session, writer=GuardedFileWriter()), loader=load_document, ui=self.ui, recent_files=self.recent)
    def tearDown(self) -> None:
        self.temp.cleanup()
    def edit_append(self, extra: str) -> None:
        self.buffer.user_insert(self.editor, len(self.buffer.text), extra)
    def test_successful_open_touches_only_after_install(self):
        source = self.root / 'open.txt'
        source.write_text('hello\n', encoding='utf-8')
        self.assertTrue(self.lifecycle.open_document(str(source)).completed)
        self.assertEqual(self.recent.paths, (str(source.resolve()),))
        self.assertEqual(len(self.store.save_calls), 1)
    def test_failed_or_cancelled_open_does_not_touch(self):
        self.assertFalse(self.lifecycle.open_document(str(self.root / 'missing.txt')).completed)
        self.assertEqual(self.store.save_calls, [])
        self.edit_append('draft')
        self.ui.unsaved = UnsavedDecision.CANCEL
        target = self.root / 'target.txt'
        target.write_text('target', encoding='utf-8')
        self.assertTrue(self.lifecycle.open_document(str(target)).cancelled)
        self.assertEqual(self.store.save_calls, [])
    def test_first_save_touches_recent_but_ordinary_save_does_not(self):
        self.edit_append('one')
        target = self.root / 'saved.txt'
        self.ui.save_path = str(target)
        self.assertTrue(self.lifecycle.save().completed)
        self.assertEqual(self.recent.paths, (str(target.resolve()),))
        calls = len(self.store.save_calls)
        self.edit_append(' two')
        self.assertTrue(self.lifecycle.save().completed)
        self.assertEqual(len(self.store.save_calls), calls)
    def test_binding_changing_save_as_touches_new_path(self):
        first = self.root / 'first.txt'
        first.write_text('a', encoding='utf-8')
        self.assertTrue(self.lifecycle.open_document(str(first)).completed)
        second = self.root / 'second.txt'
        self.ui.save_path = str(second)
        self.assertTrue(self.lifecycle.save_as().completed)
        self.assertEqual(self.recent.paths[0], str(second.resolve()))
        self.assertEqual(self.recent.paths[1], str(first.resolve()))
    def test_recent_persistence_failure_cannot_rollback_successful_open(self):
        store = RecordingRecentStore(fail_save=True)
        recent = RecentFilesController(store)
        lifecycle = FileLifecycleController(session=self.session, editor=self.editor, save_service=DocumentSaveService(session=self.session, writer=GuardedFileWriter()), loader=load_document, ui=self.ui, recent_files=recent)
        source = self.root / 'open.txt'
        source.write_text('accepted\n', encoding='utf-8')
        result = lifecycle.open_document(str(source))
        self.assertTrue(result.completed)
        self.assertEqual(self.session.logical_path, str(source.resolve()))
        self.assertEqual(self.buffer.text, 'accepted\n')
        self.assertTrue(self.ui.warnings)
        self.assertEqual(recent.paths, ())
class MemoryStore:
    def __init__(self, initial=(), fail=False):
        self.value = tuple(initial)
        self.fail = fail
        self.loads = 0
        self.saves = 0
    def load(self):
        self.loads += 1
        return self.value
    def save(self, paths):
        self.saves += 1
        if self.fail:
            raise OSError('simulated persistence failure')
        self.value = tuple(paths)
class RecentFilesTests(unittest.TestCase):
    def test_lazy_load_dedup_mru_and_cap(self):
        store = MemoryStore([f'/tmp/f{i}' for i in range(12)])
        recent = RecentFilesController(store)
        self.assertEqual(store.loads, 0)
        self.assertEqual(len(recent.paths), MAX_RECENT_FILES)
        self.assertEqual(store.loads, 1)
        recent.touch('/tmp/f5')
        self.assertEqual(recent.paths[0], os.path.abspath('/tmp/f5'))
        self.assertEqual(len(recent.paths), MAX_RECENT_FILES)
        self.assertEqual(len(set(recent.paths)), len(recent.paths))
    def test_unicode_logical_symlink_spelling_is_not_realpathed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / 'real'
            real.mkdir()
            link = root / 'logical'
            link.symlink_to(real, target_is_directory=True)
            path = link / 'café.txt'
            store = MemoryStore()
            recent = RecentFilesController(store)
            recent.touch(str(path))
            self.assertEqual(recent.paths, (os.path.abspath(str(path)),))
            self.assertNotEqual(recent.paths[0], os.path.realpath(str(path)))
    def test_persistence_failure_does_not_publish_false_in_memory_update(self):
        store = MemoryStore(['/tmp/a'], fail=True)
        recent = RecentFilesController(store)
        self.assertEqual(recent.paths, ('/tmp/a',))
        with self.assertRaises(OSError):
            recent.touch('/tmp/b')
        self.assertEqual(recent.paths, ('/tmp/a',))
        with self.assertRaises(OSError):
            recent.clear()
        self.assertEqual(recent.paths, ('/tmp/a',))
    def test_json_store_atomic_0600_roundtrip_and_no_metadata_schema(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state' / 'graphium' / 'recent-files.json'
            store = JsonRecentFilesStore(path)
            values = ('/tmp/α.txt', '/tmp/b.txt')
            store.save(values)
            self.assertEqual(store.load(), values)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 384)
            payload = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(payload, {'version': 1, 'paths': list(values)})
            self.assertEqual(set(payload), {'version', 'paths'})
            self.assertEqual(list(path.parent.glob('.*.tmp')), [])
    def test_missing_or_corrupt_store_is_empty_and_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'recent-files.json'
            store = JsonRecentFilesStore(path)
            self.assertEqual(store.load(), ())
            self.assertFalse(path.exists())
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(store.load(), ())
