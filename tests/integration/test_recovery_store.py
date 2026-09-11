from __future__ import annotations
import inspect,multiprocessing,os,stat,tempfile,unittest
from pathlib import Path
from unittest import mock
from graphium.domain.document_identity import BomKind,LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile as P
from graphium.domain.recovery_artifact import *
from graphium.infrastructure.recovery_store import RecoveryArtifactLockedError,RecoveryArtifactStore
from graphium.paths import resolve_recovery_root
def hold(root,u,ready,release): s=RecoveryArtifactStore(root); lock=s.acquire_ownership(u); ready.set(); release.wait(10); lock.release()
def die(root,u,ready): s=RecoveryArtifactStore(root); lock=s.acquire_ownership(u); ready.set(); os._exit(0)
class RecoveryArtifactStoreTests(unittest.TestCase):
    U='00000000-0000-4000-8000-000000000013'
    def setUp(self): self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name)/'state/graphium/recovery'; self.s=RecoveryArtifactStore(self.root); p=P('utf-8',BomKind.NONE,LineEnding.LF,False); self.r=RecoveryRecord(self.U,1777120000000000000,2,17,'recovery α\n',p,p,RecoveryDocumentKind.UNTITLED)
    def tearDown(self): self.t.cleanup()
    def test_xdg_state_root_resolution_is_pure(self): p=Path(self.t.name)/'xdg/graphium/recovery'; self.assertEqual(resolve_recovery_root({'HOME':self.t.name,'XDG_STATE_HOME':str(Path(self.t.name)/'xdg')}),p); self.assertFalse(p.exists())
    def test_api_has_no_arbitrary_destination_path(self):
        self.assertEqual(self.s.artifact_path(self.U),self.root/f'{self.U}.recovery'); [self.assertNotIn('path',inspect.signature(x).parameters) for x in (self.s.write,self.s.load,self.s.remove,self.s.acquire_ownership)]; self.assertRaises(ValueError,self.s.artifact_path,'../../target'); self.assertFalse(self.root.exists())
    def test_atomic_private_roundtrip_and_replace_one_artifact(self):
        p=self.s.write(self.r); self.assertEqual(self.s.load(self.U),self.r); self.assertEqual(stat.S_IMODE(self.root.stat().st_mode),0o700); self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600); n=RecoveryRecord(self.U,self.r.captured_at_ns+1,3,18,'new β',self.r.current_profile,self.r.saved_profile,RecoveryDocumentKind.UNTITLED); self.s.write(n); self.assertEqual(self.s.load(self.U),n); self.assertEqual(len(list(self.root.glob('*.recovery'))),1)
    def test_failed_replace_preserves_previous_and_cleans_temp(self):
        self.s.write(self.r); before=self.s.artifact_path(self.U).read_bytes(); n=RecoveryRecord(self.U,self.r.captured_at_ns+1,3,18,'new',self.r.current_profile,self.r.saved_profile,RecoveryDocumentKind.UNTITLED)
        with mock.patch('graphium.infrastructure.recovery_store.os.replace',side_effect=OSError('fail')): self.assertRaises(OSError,self.s.write,n)
        self.assertEqual(self.s.artifact_path(self.U).read_bytes(),before); self.assertEqual(self.s.load(self.U),self.r); self.assertEqual(list(self.root.glob('.*.tmp')),[])
    def test_corrupt_and_filename_mismatch_fail_closed(self):
        self.s.write(self.r); p=self.s.artifact_path(self.U); raw=p.read_bytes(); p.write_bytes(raw[:-1]); self.assertRaises(CorruptRecoveryArtifactError,self.s.load,self.U); other='00000000-0000-4000-8000-000000000014'; self.s.artifact_path(other).write_bytes(raw); self.assertRaises(CorruptRecoveryArtifactError,self.s.load,other)
    def test_missing_probe_is_lazy(self): self.assertRaises(FileNotFoundError,self.s.load,self.U); self.assertFalse(self.s.is_locked(self.U)); self.assertFalse(self.root.exists())
    def test_live_lock_exclusion_and_release(self):
        c=multiprocessing.get_context('spawn'); ready,release=c.Event(),c.Event(); p=c.Process(target=hold,args=(str(self.root),self.U,ready,release)); p.start(); self.assertTrue(ready.wait(5)); self.assertTrue(self.s.is_locked(self.U)); self.assertRaises(RecoveryArtifactLockedError,self.s.acquire_ownership,self.U); self.assertEqual(stat.S_IMODE(self.s.lock_path(self.U).stat().st_mode),0o600); release.set(); p.join(5); self.assertEqual(p.exitcode,0); self.assertFalse(self.s.is_locked(self.U))
    def test_process_death_releases_lock_without_marker(self):
        c=multiprocessing.get_context('spawn'); ready=c.Event(); p=c.Process(target=die,args=(str(self.root),self.U,ready)); p.start(); self.assertTrue(ready.wait(5)); p.join(5); self.assertEqual(p.exitcode,0); self.assertFalse(self.s.is_locked(self.U)); lock=self.s.acquire_ownership(self.U); lock.release(); self.assertEqual({x.name for x in self.root.iterdir()},{f'{self.U}.lock'})
    def test_remove_is_idempotent_and_confined(self): self.s.write(self.r); x=self.root/'user-target.txt'; x.write_text('stay'); self.assertTrue(self.s.remove(self.U)); self.assertFalse(self.s.remove(self.U)); self.assertEqual(x.read_text(),'stay')
