from __future__ import annotations
import os,stat,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest import mock
from graphium.domain.spellcheck import MAX_SPELL_TOKEN_CODEPOINTS,WordSpan,iter_word_spans
from graphium.infrastructure.hunspell_session import *

SCRIPT='''#!%s
import pathlib,sys,time
LOG=pathlib.Path(%r)
LOG.write_text(repr(sys.argv))
mode=%r
if mode=='no-banner': sys.exit(2)
print('@(#) Fake Hunspell 1.0',flush=True)
for raw in sys.stdin:
    LOG.write_text(LOG.read_text()+'\\n'+raw.rstrip('\\n'))
    word=raw.rstrip('\\n')[1:]
    if mode=='multi' and word=='compound-ok':
        print('*',flush=True); print('*',flush=True); print('',flush=True); continue
    if mode=='multi' and word=='compound-bad':
        print('*',flush=True); print('& component 1 0: replacement',flush=True); print('',flush=True); continue
    if mode=='multi' and word=='compound-manybad':
        print('& first 1 0: one',flush=True); print('& second 1 0: two',flush=True); print('',flush=True); continue
    if mode=='multi-malformed':
        print('*',flush=True); print('BAD',flush=True); print('',flush=True); continue
    if mode=='too-many-records':
        [print('*',flush=True) for _ in range(80)]; print('',flush=True); continue
    if mode=='too-many-bytes':
        [print('+ '+('x'*15000),flush=True) for _ in range(8)]; print('',flush=True); continue
    if mode=='partial-hang':
        print('*',flush=True); time.sleep(10); continue
    if mode=='no-terminator':
        print('*',flush=True); sys.exit(0)
    if mode=='hang': time.sleep(10); continue
    if mode=='die': sys.exit(3)
    if mode=='malformed': print('BAD',flush=True); print('',flush=True); continue
    if mode=='oversized': print('& '+word+' 1 0: '+'x'*17000,flush=True); print('',flush=True); continue
    if mode=='oversuggestion': print('& '+word+' 1 0: '+'x'*300,flush=True); print('',flush=True); continue
    if mode=='count': print('& '+word+' 2 0: one',flush=True); print('',flush=True); continue
    if word=='good': print('*',flush=True)
    elif word=='none': print('# none 0',flush=True)
    elif word=='many': print('& many 20 0: '+', '.join('s'+str(i) for i in range(20)),flush=True)
    else: print('& '+word+' 3 0: bed, bid, bud',flush=True)
    print('',flush=True)
'''
class SpellCoreTests(unittest.TestCase):
    def spans(self,text,**kw): return list(iter_word_spans(text,**kw))
    def test_unicode_span_offsets_and_punctuation(self):
        text='  café, naïve!'; self.assertEqual(self.spans(text),[WordSpan(2,6,'café'),WordSpan(8,13,'naïve')])
    def test_combining_mark_preserves_codepoint_offsets(self):
        text='x e\u0301lan z'; self.assertEqual(self.spans(text)[1],WordSpan(2,7,'e\u0301lan'))
    def test_internal_apostrophes_and_hyphen_only(self):
        text="'can't' l’arte -- well-being a--b"; self.assertEqual([x.text for x in self.spans(text)],["can't",'l’arte','well-being','a','b'])
    def test_nonletters_and_overlong_tokens_are_skipped(self):
        text='123 _ - \u0301 '+('a'*(MAX_SPELL_TOKEN_CODEPOINTS+1))+' ok'; self.assertEqual([x.text for x in self.spans(text)],['ok'])
    def test_one_mib_single_line_is_scanned_without_line_materialization_protocol(self):
        text=('word '*209715)[:1048576]; spans=iter_word_spans(text); self.assertEqual(next(spans),WordSpan(0,4,'word')); self.assertGreater(sum(1 for _ in spans),100000)
    def test_parser_correct_unknown_and_bounded_suggestions(self):
        self.assertTrue(parse_hunspell_response('ok','*').correct); self.assertEqual(parse_hunspell_response('none','# none 0').suggestions,())
        many=', '.join('s'+str(i) for i in range(20)); r=parse_hunspell_response('many','& many 20 0: '+many); self.assertFalse(r.correct); self.assertEqual(len(r.suggestions),MAX_SUGGESTIONS)
    def test_parser_rejects_malformed_count_and_oversized_suggestion(self):
        self.assertRaises(HunspellProtocolError,parse_hunspell_response,'bad','& bad 2 0: one')
        self.assertRaises(HunspellProtocolError,parse_hunspell_response,'bad','& bad 1 0: '+'x'*300)
    def test_token_rejects_newline_and_oversize(self):
        self.assertRaises(ValueError,parse_hunspell_response,'bad\n*','*'); self.assertRaises(ValueError,parse_hunspell_response,'é'*3000,'*')
class HunspellPipeTests(unittest.TestCase):
    def setUp(self): self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name)
    def tearDown(self): self.t.cleanup()
    def fake(self,mode='ok'):
        log=self.root/(mode.replace('/','_')+'.log'); exe=self.root/'hunspell'; exe.write_text(SCRIPT%(sys.executable,str(log),mode)); exe.chmod(exe.stat().st_mode|stat.S_IXUSR); return exe,log
    def test_resolver_is_optional_and_on_demand(self):
        with mock.patch.dict(os.environ,{'PATH':str(self.root)},clear=False): self.assertIsNone(resolve_hunspell_executable()); exe,_=self.fake(); self.assertEqual(resolve_hunspell_executable(),str(exe))
    def test_disappears_between_resolution_and_spawn_fails_closed(self):
        exe,_=self.fake(); path=str(exe); exe.unlink(); self.assertRaises(HunspellProcessError,HunspellPipeSession(path).start)
    def test_real_pipe_argv_prefix_and_results(self):
        exe,log=self.fake(); s=HunspellPipeSession(str(exe)); self.assertTrue(s.check('good').correct); r=s.check('*bad'); self.assertEqual(r.suggestions,('bed','bid','bud')); pid=s.pid; s.close(); data=log.read_text(); self.assertIn("'-a'",data); self.assertIn("'UTF-8'",data); self.assertIn('^*bad',data); self.assertNotIn('/document/',data); self.assertFalse(_alive(pid))
    def test_unknown_without_suggestions(self):
        exe,_=self.fake(); s=HunspellPipeSession(str(exe)); self.assertEqual(s.check('none'),HunspellResult('none',False,())); s.close()
    def test_excessive_suggestions_are_bounded(self):
        exe,_=self.fake(); s=HunspellPipeSession(str(exe)); self.assertEqual(len(s.check('many').suggestions),MAX_SUGGESTIONS); s.close()
    def test_bad_banner_malformed_and_count_fail_closed(self):
        for mode in ('no-banner','malformed','count'):
            exe,_=self.fake(mode); s=HunspellPipeSession(str(exe)); self.assertRaises(HunspellError,s.start) if mode=='no-banner' else self.assertRaises(HunspellError,s.check,'bad'); s.close()
    def test_oversized_protocol_and_suggestion_fail_closed(self):
        for mode in ('oversized','oversuggestion'):
            exe,_=self.fake(mode); s=HunspellPipeSession(str(exe)); self.assertRaises(HunspellProtocolError,s.check,'bad'); s.close()
    def test_child_exit_mid_session_and_hang_are_bounded_and_reaped(self):
        exe,_=self.fake('die'); s=HunspellPipeSession(str(exe),timeout_seconds=.8); s.start(); pid=s.pid; self.assertRaises(HunspellProcessError,s.check,'bad'); self.assertFalse(_alive(pid))
        exe,_=self.fake('hang'); s=HunspellPipeSession(str(exe),timeout_seconds=1.0); s.start(); pid=s.pid; start=time.monotonic(); self.assertRaises(HunspellTimeoutError,s.check,'bad'); self.assertLess(time.monotonic()-start,2.5); self.assertFalse(_alive(pid))
        exe,_=self.fake('hang'); s=HunspellPipeSession(str(exe),timeout_seconds=5); s.start(); pid=s.pid; errors=[]; th=threading.Thread(target=lambda: _capture(errors,lambda:s.check('bad'))); th.start(); time.sleep(.1); s.cancel(); th.join(1); self.assertFalse(th.is_alive()); self.assertTrue(errors); self.assertFalse(_alive(pid))
    def test_multi_record_groups_aggregate_conservatively_and_preserve_sync(self):
        exe,_=self.fake('multi'); s=HunspellPipeSession(str(exe))
        self.assertEqual(s.check('compound-ok'),HunspellResult('compound-ok',True,()))
        self.assertEqual(s.check('compound-bad'),HunspellResult('compound-bad',False,()))
        self.assertEqual(s.check('compound-manybad'),HunspellResult('compound-manybad',False,()))
        self.assertTrue(s.check('good').correct)
        s.close()
    def test_malformed_record_inside_multi_record_group_fails_closed(self):
        exe,_=self.fake('multi-malformed'); s=HunspellPipeSession(str(exe)); self.assertRaises(HunspellProtocolError,s.check,'compound'); s.close()
    def test_response_group_line_and_byte_budgets_fail_closed(self):
        for mode in ('too-many-records','too-many-bytes'):
            exe,_=self.fake(mode); s=HunspellPipeSession(str(exe)); self.assertRaises(HunspellProtocolError,s.check,'compound'); s.close()
    def test_missing_terminator_and_partial_group_timeout_fail_closed(self):
        exe,_=self.fake('no-terminator'); s=HunspellPipeSession(str(exe),timeout_seconds=.8); s.start(); pid=s.pid
        self.assertRaises(HunspellError,s.check,'compound'); self.assertFalse(_alive(pid))
        exe,_=self.fake('partial-hang'); s=HunspellPipeSession(str(exe),timeout_seconds=.8); s.start(); pid=s.pid
        start=time.monotonic(); self.assertRaises(HunspellTimeoutError,s.check,'compound'); self.assertLess(time.monotonic()-start,2.0); self.assertFalse(_alive(pid))
    def test_cancellation_mid_multi_record_group_is_bounded_and_reaped(self):
        exe,_=self.fake('partial-hang'); s=HunspellPipeSession(str(exe),timeout_seconds=5); s.start(); pid=s.pid; errors=[]
        th=threading.Thread(target=lambda: _capture(errors,lambda:s.check('compound'))); th.start(); time.sleep(.1); s.cancel(); th.join(1)
        self.assertFalse(th.is_alive()); self.assertTrue(errors); self.assertFalse(_alive(pid))
    def test_repeated_open_close_reaps_and_closed_session_cannot_restart(self):
        exe,_=self.fake(); pids=[]
        for _ in range(3):
            s=HunspellPipeSession(str(exe)); s.start(); pids.append(s.pid); s.close(); self.assertRaises(HunspellClosedError,s.start)
        self.assertFalse(any(_alive(x) for x in pids))
def _capture(out,call):
    try: call()
    except BaseException as exc: out.append(exc)
def _alive(pid):
    if pid is None:return False
    try: os.kill(pid,0)
    except ProcessLookupError:return False
    return True
