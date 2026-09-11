from __future__ import annotations
import unittest
from graphium.application.commands import CHECK_SPELLING_COMMAND,COMMANDS
from graphium.application.document_session import DocumentSession
from graphium.application.native_editor import NativeEditorController
from graphium.application.spellcheck import *
from graphium.domain.document_identity import BomKind,LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile
from graphium.domain.edit_history import DeltaHistory,ViewState
from graphium.domain.spellcheck import WordSpan,iter_word_spans
from graphium.infrastructure.hunspell_session import HunspellResult
from tests.behavioral._native_test_support import NativeTestBuffer

def make(text='bad good bad',clean=True):
    s=DocumentSession(); h=DeltaHistory(); b=NativeTestBuffer(text); e=NativeEditorController(session=s,history=h,buffer=b); e.initialize_new_text(text,clean=clean); return s,h,b,e,SpellCheckController(e)
def issue(c,suggestions=('bed','bid')):
    r=c.next_request(); assert r is not None; return r,c.accept_result(r,HunspellResult(r.span.text,False,suggestions))

class SpellSessionTests(unittest.TestCase):
    def test_01_empty_and_no_word_document_complete_without_request(self):
        for text in ('','123 -- 😀'):
            *_,c=make(text); self.assertIsNone(c.next_request()); self.assertEqual(c.phase,SpellCheckPhase.COMPLETE)
    def test_02_request_order_and_correct_result_advance(self):
        *_,c=make('one two'); r1=c.next_request(); self.assertEqual(r1.span,WordSpan(0,3,'one')); self.assertIsNone(c.accept_result(r1,HunspellResult('one',True))); r2=c.next_request(); self.assertEqual(r2.span,WordSpan(4,7,'two'))
    def test_03_unknown_with_and_without_suggestions_projects_exact_issue(self):
        *_,c=make('bad'); r,i=issue(c,()); self.assertEqual(i,SpellIssue(c.source_state_id,r.span,())); self.assertEqual(c.phase,SpellCheckPhase.ISSUE)
    def test_04_ignore_is_session_only_and_does_not_mutate_document(self):
        s,h,b,e,c=make('bad good'); old=h.current_state_id; issue(c); c.ignore(); self.assertEqual((b.text,h.current_state_id,s.modified),('bad good',old,False)); self.assertEqual(c.next_request().span.text,'good')
    def test_05_ignore_all_skips_exact_later_token_but_not_case_variant(self):
        *_,c=make('bad Bad bad'); issue(c); c.ignore_all(); self.assertEqual(c.ignored_all,frozenset({'bad'})); self.assertEqual(c.next_request().span.text,'Bad'); r=c._pending; c.accept_result(r,HunspellResult('Bad',True)); self.assertIsNone(c.next_request())
    def test_06_replace_is_one_normal_undo_redo_group_and_saved_semantics(self):
        s,h,b,e,c=make('bad'); old=h.current_state_id; issue(c); plan=c.replace('bed'); self.assertTrue(plan.changed); self.assertEqual(b.text,'bed'); self.assertEqual(len(h.undo_stack),1); self.assertTrue(s.modified); e.undo(); self.assertEqual((b.text,s.modified),('bad',False)); e.redo(); self.assertEqual((b.text,s.modified),('bed',True)); self.assertGreater(c.source_state_id,old)
    def test_07_custom_replace_continues_from_end_of_accepted_replacement(self):
        s,h,b,e,c=make('bad next'); issue(c); c.replace('very good'); self.assertEqual(b.text,'very good next'); self.assertEqual(c.cursor,len('very good')); self.assertEqual(c.next_request().span.text,'next')
    def test_08_identical_replace_is_exact_noop_without_dirty_or_undo(self):
        s,h,b,e,c=make('bad'); old=h.current_state_id; issue(c); p=c.replace('bad'); self.assertFalse(p.changed); self.assertEqual((h.current_state_id,len(h.undo_stack),s.modified,b.text),(old,0,False,'bad'))
    def test_09_delete_replacement_is_bounded_normal_edit(self):
        s,h,b,e,c=make('bad x'); issue(c); p=c.replace(''); self.assertEqual((b.text,c.cursor),(' x',0)); self.assertEqual(len(p.operations),1); e.undo(); self.assertEqual(b.text,'bad x')
    def test_10_stale_editor_state_before_replace_aborts_without_spell_mutation(self):
        s,h,b,e,c=make('bad'); issue(c); b.append(e,'!'); current=b.text; with_ctx=self.assertRaisesRegex(SpellCheckStaleError,'document changed');
        with with_ctx: c.replace('bed')
        self.assertEqual(b.text,current); self.assertEqual(c.phase,SpellCheckPhase.STALE)
    def test_11_stale_result_after_user_edit_is_rejected_before_projection(self):
        s,h,b,e,c=make('bad'); r=c.next_request(); b.append(e,'!'); self.assertRaises(SpellCheckStaleError,c.accept_result,r,HunspellResult('bad',False,('bed',))); self.assertEqual(c.phase,SpellCheckPhase.STALE)
    def test_12_mismatched_or_out_of_order_result_is_rejected(self):
        *_,c=make('bad'); r=c.next_request(); fake=SpellCheckRequest(r.sequence+1,r.source_state_id,r.span); self.assertRaises(SpellCheckStateError,c.accept_result,fake,HunspellResult('bad',True)); self.assertRaises(SpellCheckStateError,c.accept_result,r,HunspellResult('other',True))
    def test_13_replacement_preserves_representation_profile_and_notifies_normal_edit(self):
        s,h,b,e,c=make('bad'); profile=DocumentSerializationProfile('utf-16-le',BomKind.UTF16_LE,LineEnding.CRLF); s.select_representation_encoding('utf-16-le',BomKind.UTF16_LE); s.select_representation_line_ending(LineEnding.CRLF); seen=[]; e.set_document_state_listener(lambda:seen.append(1)); issue(c); c.replace('bed'); self.assertEqual(s.current_representation_profile,profile); self.assertEqual(len(seen),1)
    def test_14_close_destroys_session_state_and_blocks_further_use(self):
        *_,c=make('bad bad'); issue(c); c.ignore_all(); c.close(); self.assertEqual(c.ignored_all,frozenset()); self.assertEqual(c.phase,SpellCheckPhase.CLOSED); self.assertRaises(SpellCheckStateError,c.next_request)
    def test_15_planner_validates_span_and_collapses_target_view_at_replacement_end(self):
        p=plan_spell_replacement(source_text='x bad y',source_state_id=7,span=WordSpan(2,5,'bad'),replacement='better',before_view=ViewState(7,1)); self.assertEqual((p.final_text,p.target_view),('x better y',ViewState(8,8))); self.assertRaises(ValueError,plan_spell_replacement,source_text='bad',source_state_id=7,span=WordSpan(0,3,'foo'),replacement='x',before_view=ViewState())
    def test_16_command_identity_is_projected_once_with_f2(self):
        self.assertEqual((CHECK_SPELLING_COMMAND.action,CHECK_SPELLING_COMMAND.label,CHECK_SPELLING_COMMAND.accelerator,CHECK_SPELLING_COMMAND.menu),('check-spelling','Check Spelling…','F2','Document')); self.assertEqual(COMMANDS.count(CHECK_SPELLING_COMMAND),1)
    def test_17_iterator_start_preserves_absolute_codepoint_offsets(self):
        self.assertEqual(list(iter_word_spans('zero café end',start=5)),[WordSpan(5,9,'café'),WordSpan(10,13,'end')]); self.assertRaises(ValueError,lambda:list(iter_word_spans('abc',start=4)))
if __name__=='__main__': unittest.main()
