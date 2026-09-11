"""GTK-free logical editor transaction controller for Graphium."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Protocol
from graphium.application.document_session import DocumentSession
from graphium.domain.document_identity import DocumentFileState, DocumentLoadResult
from graphium.domain.history import HistoryState, TextHistory

class EditorBufferPort(Protocol):
    def capture(self) -> HistoryState: ...
    def restore(self, state: HistoryState) -> None: ...

@dataclass(frozen=True)
class EditorTransactionResult:
    label: str
    before: HistoryState
    after: HistoryState
    changed: bool
    rolled_back: bool = False
    restored_history: bool = False

class EditorRollbackError(RuntimeError):
    """A failed edit could not fully restore the concrete buffer view.

    History and session authorities are restored before this is raised.
    """

    def __init__(self, original_error: BaseException, restore_error: BaseException) -> None:
        super().__init__(
            f"editor transaction failed and buffer rollback also failed: {restore_error}"
        )
        self.original_error = original_error
        self.restore_error = restore_error

class EditorTransactionController:
    __slots__ = ("session","history","buffer","_programmatic_depth","_restoring_depth")

    def __init__(self, *, session: DocumentSession, history: TextHistory, buffer: EditorBufferPort) -> None:
        if not isinstance(session, DocumentSession): raise TypeError("session must be DocumentSession")
        if not isinstance(history, TextHistory): raise TypeError("history must be TextHistory")
        if buffer is None: raise TypeError("buffer is required")
        self.session, self.history, self.buffer = session, history, buffer
        self._programmatic_depth = self._restoring_depth = 0

    @property
    def programmatic_active(self): return self._programmatic_depth > 0
    @property
    def restoring(self): return self._restoring_depth > 0

    def initialize_new(self, *, clean: bool = True) -> HistoryState:
        assigned = self.history.reset(self.buffer.capture())
        self.session.establish_new(assigned, clean=clean)
        return assigned

    def initialize_open(self, result: DocumentLoadResult) -> HistoryState:
        if not isinstance(result, DocumentLoadResult):
            raise TypeError("result must be DocumentLoadResult")
        history_checkpoint = self.history.checkpoint()
        session_checkpoint = self.session.snapshot()
        before = self.buffer.capture()
        self._restoring_depth += 1
        try:
            self.buffer.restore(HistoryState(result.text))
            captured = self.buffer.capture()
            if captured.text != result.text:
                raise RuntimeError("buffer restore did not reproduce loaded text")
            assigned = self.history.reset(captured)
            self.session.establish_open(result, assigned)
            return assigned
        except BaseException:
            self.history.restore_checkpoint(history_checkpoint)
            self.session.restore_checkpoint(session_checkpoint)
            try:
                self.buffer.restore(before)
            except BaseException as restore_error:
                raise RuntimeError(
                    f"open initialization failed and prior buffer could not be restored: {restore_error}"
                ) from restore_error
            raise
        finally:
            self._restoring_depth -= 1

    def sync_view_state(self) -> bool:
        changed = self.history.replace_current_view_state(self.buffer.capture())
        if changed and self.history.current is not None:
            self.session.reconcile_with_history(self.history.current)
        return changed

    def observe_native_change(self) -> None:
        if self.restoring or self.session.loading: return
        captured = self.buffer.capture()
        self.session.observe_uncommitted_text(captured.text)
        current = self.history.current
        if current is not None and current.text == captured.text:
            self.history.replace_current_view_state(captured)
            self.session.reconcile_with_history(self.history.current)

    def commit_native_group(self) -> bool:
        changed = self.history.commit(self.buffer.capture())
        current = self.history.current
        if current is None: raise RuntimeError("history has no current state after native commit")
        self.session.commit_history_state(current)
        return changed

    def flush(self) -> int:
        self.commit_native_group()
        current = self.history.current
        if current is None or current.state_id <= 0: raise RuntimeError("history has no stable current state")
        return current.state_id

    def execute(self, label: str, action: Callable[[], None]) -> EditorTransactionResult:
        if self.programmatic_active: raise RuntimeError("nested editor transactions are not allowed")
        if not isinstance(label, str) or not label: raise ValueError("transaction label must be non-empty")
        if not callable(action): raise TypeError("action must be callable")
        hcp, scp, before = self.history.checkpoint(), self.session.snapshot(), self.buffer.capture()
        self._programmatic_depth += 1
        try:
            action()
            after = self.buffer.capture()
            changed = before.text != after.text
            if changed: self.history.commit(after)
            else: self.history.replace_current_view_state(after)
            current = self.history.current
            if current is None: raise RuntimeError("history has no current state after transaction")
            self.session.commit_history_state(current)
            return EditorTransactionResult(label, before, after, changed)
        except BaseException as original_error:
            restore_error: BaseException | None = None
            self._restoring_depth += 1
            try:
                try:
                    self.buffer.restore(before)
                except BaseException as exc:
                    restore_error = exc
                finally:
                    # Authoritative state rolls back even when the concrete
                    # view cannot be restored. The editor may then fail closed.
                    self.history.restore_checkpoint(hcp)
                    self.session.restore_checkpoint(scp)
            finally:
                self._restoring_depth -= 1
            if restore_error is not None:
                raise EditorRollbackError(original_error, restore_error) from restore_error
            raise
        finally:
            self._programmatic_depth -= 1

    def _restore_history_state(self, state: HistoryState, *, label: str) -> EditorTransactionResult:
        before = self.buffer.capture()
        self._restoring_depth += 1
        try: self.buffer.restore(state)
        finally: self._restoring_depth -= 1
        after = self.buffer.capture()
        if after.text != state.text:
            raise RuntimeError("buffer restore did not reproduce history text")
        if not self.history.replace_current_view_state(after):
            raise RuntimeError("restored history state is not the current history state")
        restored = self.history.current
        if restored is None:
            raise RuntimeError("history has no current state after restore")
        self.session.commit_history_state(restored)
        return EditorTransactionResult(
            label,
            before,
            after,
            before.text != after.text
            or before.insert_offset != after.insert_offset
            or before.selection_bound_offset != after.selection_bound_offset,
            restored_history=True,
        )

    def undo(self) -> EditorTransactionResult | None:
        self.flush()
        hcp, scp, before = self.history.checkpoint(), self.session.snapshot(), self.buffer.capture()
        target = self.history.undo(before)
        if target is None: return None
        try: return self._restore_history_state(target, label="Undo")
        except BaseException:
            self.history.restore_checkpoint(hcp); self.session.restore_checkpoint(scp)
            self._restoring_depth += 1
            try: self.buffer.restore(before)
            finally: self._restoring_depth -= 1
            raise

    def redo(self) -> EditorTransactionResult | None:
        self.flush()
        hcp, scp, before = self.history.checkpoint(), self.session.snapshot(), self.buffer.capture()
        target = self.history.redo()
        if target is None: return None
        try: return self._restore_history_state(target, label="Redo")
        except BaseException:
            self.history.restore_checkpoint(hcp); self.session.restore_checkpoint(scp)
            self._restoring_depth += 1
            try: self.buffer.restore(before)
            finally: self._restoring_depth -= 1
            raise

    def accept_current_as_saved(self, *, file_state: DocumentFileState | None = None) -> int:
        state_id = self.flush()
        self.session.accept_saved_state(state_id, file_state=file_state)
        return state_id

    def accept_specific_as_saved(self, state_id: int, *, file_state: DocumentFileState | None = None) -> None:
        self.session.accept_saved_state(state_id, file_state=file_state)
