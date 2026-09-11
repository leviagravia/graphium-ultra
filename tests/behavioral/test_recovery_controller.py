from __future__ import annotations
import tempfile
from pathlib import Path
import unittest
from graphium.application.document_session import DocumentSession
from graphium.application.native_editor import NativeEditorController
from graphium.application.recovery import RECOVERY_DELAY_SECONDS, RecoveryController
from graphium.domain.document_identity import BomKind, LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile
from graphium.domain.recovery_artifact import RecoveryDocumentKind, RecoveryRecord
from graphium.domain.edit_history import DeltaHistory
from graphium.infrastructure.recovery_store import RecoveryArtifactStore
from graphium.infrastructure.document_loader import load_document
from tests.behavioral._native_test_support import NativeTestBuffer
class Scheduler:
    def __init__(self): self.n=1; self.pending={}; self.calls=[]
    def schedule_once(self,delay,callback):
        h=self.n; self.n+=1; self.pending[h]=(int(delay),callback); self.calls.append(int(delay)); return h
    def cancel(self,h): self.pending.pop(int(h),None)
    def dispatch(self,callback): callback()
    def fire(self): h=min(self.pending); self.pending.pop(h)[1]()
class Worker:
    def __init__(self): self.jobs=[]; self.closed=False
    def submit(self,job,done):
        if self.closed: raise RuntimeError('closed')
        self.jobs.append((job,done))
    def run(self):
        job,done=self.jobs.pop(0)
        try: result,error=job(),None
        except BaseException as exc: result,error=None,exc
        done(result,error)
    def all(self):
        while self.jobs: self.run()
    def close(self): self.closed=True
class RecoveryControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)/'recovery'; self.store=RecoveryArtifactStore(self.root)
        self.scheduler=Scheduler(); self.worker=Worker(); self.session=DocumentSession(); self.history=DeltaHistory(); self.buffer=NativeTestBuffer()
        self.editor=NativeEditorController(session=self.session,history=self.history,buffer=self.buffer); self.editor.initialize_new_text('',clean=True)
        self.recovery=RecoveryController(session=self.session,capture=self.buffer,store=self.store,scheduler=self.scheduler,worker=self.worker,clock_ns=lambda:123456789)
        self.editor.set_document_state_listener(self.recovery.document_state_changed)
    def tearDown(self):
        try: self.recovery.close(); self.worker.all()
        except Exception: pass
        self.tmp.cleanup()
    def edit(self,text): self.buffer.append(self.editor,text)
    def publish(self): self.scheduler.fire(); self.worker.run()
    def record(self): return self.store.load(self.recovery.snapshot().artifact_uuid)

    def test_clean_start_is_lazy(self): self.assertFalse(self.root.exists() or self.scheduler.pending or self.worker.jobs)
    def test_first_modified_state_schedules_one_30_second_timer(self):
        self.edit('a'); self.assertEqual(self.scheduler.calls,[RECOVERY_DELAY_SECONDS]); self.assertEqual(len(self.scheduler.pending),1)
    def test_repeated_edits_coalesce_and_capture_latest_at_timer(self):
        self.edit('a'); self.edit('b'); self.edit('c'); self.assertEqual(self.scheduler.calls,[30]); self.buffer.full_captures=0; self.publish(); self.assertEqual((self.buffer.full_captures,self.record().text),(1,'abc'))
    def test_untitled_unicode_roundtrip(self):
        self.edit('α🙂'); self.publish(); r=self.record(); self.assertEqual((r.document_kind.value,r.named_baseline,r.text),('untitled',None,'α🙂'))
    def test_representation_only_state_is_recoverable(self):
        self.session.select_representation_line_ending(LineEnding.CRLF); self.recovery.document_state_changed(); self.publish(); self.assertEqual(self.record().current_profile.line_ending,LineEnding.CRLF)
    def test_encoding_is_metadata_not_target_serialization(self):
        self.session.select_representation_encoding('utf-16-le',BomKind.UTF16_LE); self.recovery.document_state_changed(); self.publish(); r=self.record(); self.assertEqual((r.text,r.current_profile.encoding),('','utf-16-le'))
    def test_undo_to_savepoint_invalidates_artifact(self):
        self.edit('x'); u=self.recovery.snapshot().artifact_uuid; self.publish(); self.editor.undo(); self.worker.all(); self.assertFalse(self.store.artifact_path(u).exists()); self.assertIsNone(self.recovery.snapshot().artifact_uuid)
    def test_stale_worker_after_invalidation_cannot_publish(self):
        self.edit('x'); u=self.recovery.snapshot().artifact_uuid; self.scheduler.fire(); self.recovery.invalidate(); self.worker.all(); self.assertFalse(self.store.artifact_path(u).exists()); self.assertIsNone(self.recovery.snapshot().artifact_uuid)
    def test_edits_during_write_collapse_to_exactly_one_next_timer(self):
        self.edit('a'); self.scheduler.fire(); self.edit('b'); self.edit('c'); self.worker.run(); self.assertEqual((len(self.scheduler.pending),self.scheduler.calls), (1,[30,30])); self.publish(); self.assertEqual(self.record().text,'abc')
    def test_close_fences_late_worker(self):
        self.edit('x'); u=self.recovery.snapshot().artifact_uuid; self.scheduler.fire(); self.recovery.close(); self.worker.all(); self.assertFalse(self.store.artifact_path(u).exists())

    def test_named_recovery_has_fresh_saved_baseline_but_empty_undo_history(self):
        path=Path(self.tmp.name)/'named.txt'; path.write_text('saved'); loaded=load_document(str(path)); p=DocumentSerializationProfile('utf-8',BomKind.NONE,LineEnding.CRLF,False); self.editor.initialize_recovered_named(loaded,'saved + draft',p); self.assertEqual((self.session.logical_path,self.session.modified,self.editor.can_undo,self.editor.can_redo,self.session.current_representation_profile,self.buffer.text),(str(path),True,False,False,p,'saved + draft')); self.assertNotEqual(self.session.saved_editor_state_id,self.session.current_editor_state_id)
    def test_claimed_orphan_install_is_modified_empty_history_and_keeps_profile(self):
        p=DocumentSerializationProfile('utf-16-le',BomKind.UTF16_LE,LineEnding.CRLF,False); u='00000000-0000-4000-8000-000000000036'; r=RecoveryRecord(u,123,1,1,'recovered',p,p,RecoveryDocumentKind.UNTITLED); self.store.write(r); lock=self.store.claim_existing(u)
        self.recovery.install_recovered(r,lock,lambda:self.editor.initialize_recovered_unbound(r.text,r.current_profile)); self.assertEqual(self.buffer.text,'recovered'); self.assertTrue(self.session.modified); self.assertIsNone(self.session.logical_path); self.assertEqual(self.session.current_representation_profile,p); self.assertFalse(self.editor.can_undo or self.editor.can_redo); self.assertEqual(self.recovery.snapshot().artifact_uuid,u); self.assertTrue(lock.held)

if __name__=='__main__': unittest.main()
