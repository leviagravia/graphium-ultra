from __future__ import annotations
import hashlib,json,struct,unittest
from graphium.domain.document_identity import BomKind,LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile as P
from graphium.domain.recovery_artifact import *
def prof(enc='utf-8',bom=BomKind.NONE,eol=LineEnding.LF,mixed=False): return P(enc,bom,eol,mixed)
def rec(kind=RecoveryDocumentKind.NAMED):
    base=RecoveryNamedBaseline('/tmp/logical/α.txt','/real/α.txt',8,42,hashlib.sha256(b'saved').hexdigest()) if kind is RecoveryDocumentKind.NAMED else None
    return RecoveryRecord('00000000-0000-4000-8000-000000000013',1777120000000000000,7,91,'alpha\nβeta 😀\n',prof('utf-16-le',BomKind.UTF16_LE,LineEnding.CRLF,True),prof(),kind,base)
def parts(payload):
    n=len(RECOVERY_MAGIC); m=struct.unpack('>I',payload[n:n+4])[0]; h=json.loads(payload[n+4:n+4+m].decode()); return h,payload[n+4+m:]
def repack(h,b):
    x=json.dumps(h,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode(); return RECOVERY_MAGIC+struct.pack('>I',len(x))+x+b
class RecoveryArtifactCodecTests(unittest.TestCase):
    def test_named_unicode_roundtrip(self): self.assertEqual(decode_recovery_record(encode_recovery_record(rec())),rec())
    def test_untitled_has_no_file_identity(self): r=rec(RecoveryDocumentKind.UNTITLED); self.assertEqual(decode_recovery_record(encode_recovery_record(r)),r); self.assertIsNone(r.named_baseline)
    def test_body_is_strict_utf8_not_target_encoding(self): r=rec(); _,b=parts(encode_recovery_record(r)); self.assertEqual(b,r.text.encode()); self.assertNotEqual(b,r.text.encode('utf-16-le'))
    def test_corruption_truncation_digest_and_utf8_fail_closed(self):
        p=encode_recovery_record(rec()); h,b=parts(p); bad=[p[:-1],b'wrong'+p[5:]]; d=dict(h); d['body_sha256']='0'*64; bad.append(repack(d,b)); u=b'\xff'+b[1:]; d=dict(h); d.update(body_length=len(u),body_sha256=hashlib.sha256(u).hexdigest()); bad.append(repack(d,u)); [self.assertRaises(CorruptRecoveryArtifactError,decode_recovery_record,x) for x in bad]
    def test_version_schema_and_uuid_fail_closed(self):
        p=encode_recovery_record(rec()); h,b=parts(p)
        for k,v in [('format_version',2),('unexpected',True),('artifact_uuid','../../escape')]: d=dict(h); d[k]=v; self.assertRaises(CorruptRecoveryArtifactError,decode_recovery_record,repack(d,b))
    def test_document_kind_invariants(self):
        r=rec(); self.assertRaises(ValueError,RecoveryRecord,r.artifact_uuid,1,1,1,'x',prof(),prof(),RecoveryDocumentKind.NAMED,None); self.assertRaises(ValueError,RecoveryRecord,r.artifact_uuid,1,1,1,'x',prof(),prof(),RecoveryDocumentKind.UNTITLED,r.named_baseline)
    def test_uuid_is_canonical_and_path_safe(self):
        u=new_recovery_uuid(); self.assertEqual(canonical_recovery_uuid(u),u); [self.assertRaises(ValueError,canonical_recovery_uuid,x) for x in ('../x','ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF','','/tmp/x')]
