from __future__ import annotations
from graphium.domain.edit_history import DeleteDirection, EditKind, ReplayOperation, ViewState
from graphium.domain.history import HistoryState

class NativeTestBuffer:
    def __init__(self, text=''):
        self.text=text; self.insert=len(text); self.bound=len(text); self.full_captures=0
        self.fail_restore_text=None; self.fail_after_operations=None
    def capture_full(self):
        self.full_captures+=1; return HistoryState(self.text,self.insert,self.bound)
    def restore_full(self,state):
        if state.text==self.fail_restore_text: raise RuntimeError('injected restore failure')
        self.text=state.text; self.insert=state.insert_offset; self.bound=state.selection_bound_offset
    def capture_view(self): return ViewState(self.insert,self.bound)
    def _apply(self,op):
        if op.kind is EditKind.INSERT: self.text=self.text[:op.offset]+op.text+self.text[op.offset:]
        else:
            end=op.offset+len(op.text)
            if self.text[op.offset:end]!=op.text: raise RuntimeError('expected-delete mismatch')
            self.text=self.text[:op.offset]+self.text[end:]
    @staticmethod
    def _inverse(op):
        return ReplayOperation(EditKind.DELETE if op.kind is EditKind.INSERT else EditKind.INSERT,op.offset,op.text)
    def apply_operations(self,operations,target_view):
        applied=[]
        try:
            for i,op in enumerate(operations,1):
                self._apply(op); applied.append(op)
                if self.fail_after_operations is not None and i>=self.fail_after_operations: raise RuntimeError('injected operation failure')
        except BaseException:
            for op in reversed(applied): self._apply(self._inverse(op))
            raise
        self.insert=target_view.insert_offset; self.bound=target_view.selection_bound_offset
    def apply_replay(self,plan): self.apply_operations(plan.operations,plan.target_view)
    def user_insert(self,controller,offset,text):
        controller.begin_native_group(self.capture_view()); self.text=self.text[:offset]+text+self.text[offset:]
        self.insert=self.bound=offset+len(text); controller.record_native_insert(offset,text)
        return controller.end_native_group(self.capture_view())
    def append(self,controller,text): return self.user_insert(controller,len(self.text),text)
    def user_delete(self,controller,offset,length,direction=DeleteDirection.RANGE):
        controller.begin_native_group(self.capture_view()); deleted=self.text[offset:offset+length]
        self.text=self.text[:offset]+self.text[offset+length:]; self.insert=self.bound=offset
        controller.record_native_delete(offset,deleted,direction=direction); return controller.end_native_group(self.capture_view())
