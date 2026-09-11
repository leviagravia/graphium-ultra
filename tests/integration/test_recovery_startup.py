import os,tempfile,unittest
from pathlib import Path
from graphium.application.recovery_startup import RecoveryStartupCoordinator as C,RecoveryStartupDecision as D,RecoveryStartupStatus as S
from graphium.domain.document_serialization import DocumentSerializationProfile,profile_for_document
from graphium.domain.document_identity import BomKind,LineEnding
from graphium.domain.recovery_artifact import RecoveryDocumentKind,RecoveryNamedBaseline,RecoveryRecord
from graphium.infrastructure.document_loader import load_document
from graphium.infrastructure.recovery_store import RecoveryArtifactStore
from graphium.infrastructure.recovery_worker import DedicatedRecoveryWorker
class UI:
    def __init__(self,d=D.START_WITHOUT): self.d=d; self.errors=[]; self.unbound=[]; self.offered=[]
    def choose_startup_recovery(self,r): self.offered.append(r.artifact_uuid); return self.d
    def show_recovered_unbound(self,p,r): self.unbound.append((p,r))
    def show_error(self,t,m): self.errors.append((t,m))
class Editor:
    def __init__(self): self.calls=[]
    def initialize_recovered_named(self,r,t,p): self.calls.append(('named',r,t,p))
    def initialize_recovered_unbound(self,t,p): self.calls.append(('unbound',t,p))
class Restorer:
    def __init__(self): self.claims=[]
    def install_recovered(self,r,o,installer): installer(); self.claims.append((r,o))
    def close(self):
        for _,o in self.claims:
            if o.held:o.release()
class RecoveryStartupTests(unittest.TestCase):
    def setUp(self): self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name); self.store=RecoveryArtifactStore(self.root/'state/recovery'); self.ui=UI(); self.editor=Editor(); self.rest=Restorer()
    def tearDown(self): self.rest.close(); self.t.cleanup()
    def record(self,u,ts,text='draft',path=None,current=None):
        p=current or DocumentSerializationProfile('utf-8',BomKind.NONE,LineEnding.LF,False)
        if path is None:return RecoveryRecord(u,ts,1,1,text,p,p,RecoveryDocumentKind.UNTITLED)
        loaded=load_document(str(path)); st=loaded.file_state; oid=st.binding.object_id
        b=RecoveryNamedBaseline(st.binding.logical_path,st.binding.canonical_path,None if oid is None else oid.device,None if oid is None else oid.inode,st.content_fingerprint.hex_digest)
        return RecoveryRecord(u,ts,1,1,text,p,profile_for_document(st),RecoveryDocumentKind.NAMED,b)
    def invoke(self,explicit=None): return C(store=self.store,editor=self.editor,recovery=self.rest,ui=self.ui).run(explicit)
    def test_no_recovery_start_is_lazy(self): self.assertEqual(self.invoke().status,S.NONE); self.assertFalse(self.store.root.exists()); self.assertEqual(self.ui.offered,[])
    def test_recovery_worker_executor_is_lazy_before_first_submit(self): w=DedicatedRecoveryWorker(lambda cb:cb()); self.assertIsNone(w._executor); w.close()
    def test_plain_activation_offers_newest_and_start_without_retains_it(self):
        a=self.record('00000000-0000-4000-8000-000000000021',10); b=self.record('00000000-0000-4000-8000-000000000022',20); self.store.write(a); self.store.write(b)
        r=self.invoke(); self.assertEqual((r.status,self.ui.offered),(S.DEFERRED,[b.artifact_uuid])); self.assertTrue(self.store.artifact_path(b.artifact_uuid).exists()); self.assertFalse(self.store.is_locked(b.artifact_uuid))
    def test_explicit_open_never_offers_unrelated_newer_recovery(self):
        p=self.root/'a.txt'; q=self.root/'b.txt'; p.write_text('a'); q.write_text('b'); a=self.record('00000000-0000-4000-8000-000000000023',10,path=p); b=self.record('00000000-0000-4000-8000-000000000024',20,path=q); self.store.write(a); self.store.write(b)
        self.assertEqual(self.invoke(str(p)).artifact_uuid,a.artifact_uuid); self.assertEqual(self.ui.offered,[a.artifact_uuid])
    def test_live_owned_newest_is_skipped(self):
        a=self.record('00000000-0000-4000-8000-000000000025',10); b=self.record('00000000-0000-4000-8000-000000000026',20); self.store.write(a); self.store.write(b); lock=self.store.acquire_ownership(b.artifact_uuid)
        try:self.assertEqual(self.invoke().artifact_uuid,a.artifact_uuid)
        finally:lock.release()
    def test_discard_is_explicit_and_removes_only_selected_artifact(self): self.ui.d=D.DISCARD; a=self.record('00000000-0000-4000-8000-000000000027',10); b=self.record('00000000-0000-4000-8000-000000000028',20); self.store.write(a); self.store.write(b); r=self.invoke(); self.assertEqual(r.status,S.DISCARDED); self.assertTrue(self.store.artifact_path(a.artifact_uuid).exists()); self.assertFalse(self.store.artifact_path(b.artifact_uuid).exists())
    def test_corrupt_newest_is_not_offered(self): a=self.record('00000000-0000-4000-8000-000000000029',10); self.store.write(a); bad='00000000-0000-4000-8000-000000000030'; self.store.artifact_path(bad).write_bytes(b'bad'); self.assertEqual(self.invoke().artifact_uuid,a.artifact_uuid)
    def test_exact_named_baseline_recovers_bound_with_current_profile(self): self.ui.d=D.RECOVER; p=self.root/'doc.txt'; p.write_text('saved'); prof=DocumentSerializationProfile('utf-16-le',BomKind.UTF16_LE,LineEnding.CRLF,False); r=self.record('00000000-0000-4000-8000-000000000031',10,'saved + draft',p,prof); self.store.write(r); out=self.invoke(); self.assertEqual(out.status,S.RECOVERED_BOUND); self.assertEqual(self.editor.calls[0][0],'named'); self.assertEqual(self.editor.calls[0][2:],(r.text,r.current_profile)); self.assertEqual(self.ui.unbound,[])
    def test_named_target_replacement_is_recovered_unbound_even_if_bytes_match(self): self.ui.d=D.RECOVER; p=self.root/'doc.txt'; p.write_text('saved'); r=self.record('00000000-0000-4000-8000-000000000032',10,'draft',p); self.store.write(r); replacement=self.root/'replacement'; replacement.write_text('saved'); os.replace(replacement,p); out=self.invoke(); self.assertEqual(out.status,S.RECOVERED_UNBOUND); self.assertEqual(self.editor.calls[0][0],'unbound'); self.assertEqual(self.ui.unbound[0][0],str(p))
    def test_symlink_retarget_is_recovered_unbound(self): self.ui.d=D.RECOVER; a=self.root/'a'; b=self.root/'b'; link=self.root/'doc'; a.write_text('same'); b.write_text('same'); link.symlink_to(a); r=self.record('00000000-0000-4000-8000-000000000033',10,'draft',link); self.store.write(r); link.unlink(); link.symlink_to(b); self.assertEqual(self.invoke().status,S.RECOVERED_UNBOUND)
    def test_missing_named_target_is_recovered_unbound(self): self.ui.d=D.RECOVER; p=self.root/'gone.txt'; p.write_text('saved'); r=self.record('00000000-0000-4000-8000-000000000034',10,'draft',p); self.store.write(r); p.unlink(); self.assertEqual(self.invoke().status,S.RECOVERED_UNBOUND); self.assertEqual(self.editor.calls[0][0],'unbound')
    def test_untitled_recovery_is_unbound_without_inventing_provenance(self): self.ui.d=D.RECOVER; r=self.record('00000000-0000-4000-8000-000000000035',10,'untitled'); self.store.write(r); out=self.invoke(); self.assertEqual((out.status,out.provenance_path),(S.RECOVERED_UNBOUND,None)); self.assertEqual(self.editor.calls[0][0],'unbound'); self.assertEqual(self.ui.unbound,[])
if __name__=='__main__':unittest.main()
