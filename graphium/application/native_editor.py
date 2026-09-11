"""Native editor coordinator: delta history + savepoint session, GTK-free.

The coordinator is the bridge between a mutable native text buffer and Graphium's
the state/save authorities. Native edits are recorded as deltas; full document text is
captured only at lifecycle boundaries that actually require it (Open/New rollback and Save).
"""
from __future__ import annotations

from typing import Callable, Protocol

from graphium.application.document_session import DocumentSession
from graphium.domain.document_identity import DocumentLoadResult
from graphium.domain.document_serialization import DocumentSerializationProfile
from graphium.domain.edit_history import (
    DeleteDirection,
    DeltaHistory,
    EditKind,
    ReplayOperation,
    ReplayPlan,
    ViewState,
)
from graphium.application.renderability import ensure_interactive_text_renderable
from graphium.domain.history import HistoryState


class NativeEditorBufferPort(Protocol):
    def capture_full(self) -> HistoryState: ...
    def restore_full(self, state: HistoryState) -> None: ...
    def capture_view(self) -> ViewState: ...
    def apply_replay(self, plan: ReplayPlan) -> None: ...
    def apply_operations(self, operations: tuple[ReplayOperation, ...], target_view: ViewState) -> None: ...



class NativeEditorController:
    __slots__ = ("session", "history", "buffer", "_restoring_depth", "_state_changed")

    def __init__(
        self,
        *,
        session: DocumentSession,
        history: DeltaHistory,
        buffer: NativeEditorBufferPort,
    ) -> None:
        if not isinstance(session, DocumentSession):
            raise TypeError("session must be DocumentSession")
        if not isinstance(history, DeltaHistory):
            raise TypeError("history must be DeltaHistory")
        if buffer is None:
            raise TypeError("buffer is required")
        self.session = session
        self.history = history
        self.buffer = buffer
        self._restoring_depth = 0
        self._state_changed: Callable[[], None] | None = None

    def set_document_state_listener(self, callback: Callable[[], None] | None) -> None:
        if callback is not None and not callable(callback):
            raise TypeError("document state listener must be callable or None")
        self._state_changed = callback

    def _notify_document_state_changed(self) -> None:
        callback = self._state_changed
        if callback is not None:
            callback()

    @property
    def restoring(self) -> bool:
        return self._restoring_depth > 0

    @property
    def native_group_active(self) -> bool:
        return self.history.group_active

    @property
    def can_undo(self) -> bool:
        return self.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self.history.can_redo

    @property
    def current_state_id(self) -> int:
        return self.history.current_state_id

    def capture_programmatic_source(self) -> HistoryState:
        """Capture exact text/view under the current editor state identity."""
        if self.history.group_active:
            raise RuntimeError("cannot capture a programmatic source during an active native group")
        current = self.history.current_state_id
        if self.session.current_editor_state_id != current:
            raise RuntimeError("native history/session state identity mismatch before programmatic capture")
        captured = self.buffer.capture_full()
        return self._assigned_state(captured.text, current, self.buffer.capture_view())

    def _assigned_state(self, text: str, state_id: int, view: ViewState) -> HistoryState:
        return HistoryState(
            text,
            insert_offset=view.insert_offset,
            selection_bound_offset=view.selection_bound_offset,
            state_id=state_id,
        )

    def initialize_new_text(self, text: str = "", *, clean: bool = True) -> HistoryState:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        hcp = self.history.checkpoint()
        scp = self.session.snapshot()
        before = self.buffer.capture_full()
        self._restoring_depth += 1
        try:
            self.buffer.restore_full(HistoryState(text))
            captured = self.buffer.capture_full()
            if captured.text != text:
                raise RuntimeError("buffer restore did not reproduce new-document text")
            state_id = self.history.reset()
            state = self._assigned_state(text, state_id, self.buffer.capture_view())
            self.session.establish_new(state, clean=clean)
            self._notify_document_state_changed()
            return state
        except BaseException:
            self.history.restore_checkpoint(hcp)
            self.session.restore_checkpoint(scp)
            self.buffer.restore_full(before)
            raise
        finally:
            self._restoring_depth -= 1

    def initialize_open(self, result: DocumentLoadResult) -> HistoryState:
        if not isinstance(result, DocumentLoadResult):
            raise TypeError("result must be DocumentLoadResult")
        hcp = self.history.checkpoint()
        scp = self.session.snapshot()
        before = self.buffer.capture_full()
        self._restoring_depth += 1
        try:
            self.buffer.restore_full(HistoryState(result.text))
            captured = self.buffer.capture_full()
            if captured.text != result.text:
                raise RuntimeError("buffer restore did not reproduce loaded text")
            state_id = self.history.reset()
            state = self._assigned_state(result.text, state_id, self.buffer.capture_view())
            self.session.establish_open(result, state)
            self._notify_document_state_changed()
            return state
        except BaseException:
            self.history.restore_checkpoint(hcp)
            self.session.restore_checkpoint(scp)
            self.buffer.restore_full(before)
            raise
        finally:
            self._restoring_depth -= 1

    def initialize_recovered_named(
        self,
        result: DocumentLoadResult,
        text: str,
        current_profile: DocumentSerializationProfile,
    ) -> HistoryState:
        """Restore recovered text over a fresh named baseline with empty Undo/Redo."""
        if not isinstance(result, DocumentLoadResult):
            raise TypeError("result must be DocumentLoadResult")
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not isinstance(current_profile, DocumentSerializationProfile):
            raise TypeError("current_profile must be DocumentSerializationProfile")
        ensure_interactive_text_renderable(text)
        hcp, scp, before = self.history.checkpoint(), self.session.snapshot(), self.buffer.capture_full()
        self._restoring_depth += 1
        try:
            self.buffer.restore_full(HistoryState(text))
            captured = self.buffer.capture_full()
            if captured.text != text:
                raise RuntimeError("buffer restore did not reproduce recovered text")
            saved_state_id = self.history.reset()
            current_state_id = self.history.reset()
            state = self._assigned_state(text, current_state_id, self.buffer.capture_view())
            self.session.establish_recovered_named(
                result, state, saved_state_id=saved_state_id, current_profile=current_profile
            )
            self._notify_document_state_changed()
            return state
        except BaseException:
            self.history.restore_checkpoint(hcp)
            self.session.restore_checkpoint(scp)
            self.buffer.restore_full(before)
            raise
        finally:
            self._restoring_depth -= 1

    def initialize_recovered_unbound(
        self, text: str, current_profile: DocumentSerializationProfile
    ) -> HistoryState:
        """Restore recovered content as a fresh unbound Modified document."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not isinstance(current_profile, DocumentSerializationProfile):
            raise TypeError("current_profile must be DocumentSerializationProfile")
        ensure_interactive_text_renderable(text)
        hcp, scp, before = self.history.checkpoint(), self.session.snapshot(), self.buffer.capture_full()
        self._restoring_depth += 1
        try:
            self.buffer.restore_full(HistoryState(text))
            captured = self.buffer.capture_full()
            if captured.text != text:
                raise RuntimeError("buffer restore did not reproduce recovered text")
            state_id = self.history.reset()
            state = self._assigned_state(text, state_id, self.buffer.capture_view())
            self.session.establish_recovered_unbound(state, current_profile=current_profile)
            self._notify_document_state_changed()
            return state
        except BaseException:
            self.history.restore_checkpoint(hcp)
            self.session.restore_checkpoint(scp)
            self.buffer.restore_full(before)
            raise
        finally:
            self._restoring_depth -= 1

    def begin_native_group(self, before_view: ViewState) -> bool:
        if self.restoring or self.session.loading:
            return False
        self.history.begin_group(before_view)
        return True

    def record_native_insert(self, offset: int, text: str) -> None:
        if self.restoring or self.session.loading:
            return
        self.history.record_insert(offset, text)

    def record_native_delete(
        self,
        offset: int,
        text: str,
        *,
        direction: DeleteDirection,
    ) -> None:
        if self.restoring or self.session.loading:
            return
        self.history.record_delete(offset, text, direction=direction)

    def end_native_group(self, after_view: ViewState) -> int:
        if self.restoring or self.session.loading:
            return self.history.current_state_id
        state_id = self.history.end_group(
            after_view,
            saved_state_id=self.session.saved_editor_state_id,
        )
        self.session.advance_editor_state(state_id)
        self._notify_document_state_changed()
        return state_id

    def prepare_for_save(self) -> int:
        if self.history.group_active:
            raise RuntimeError("cannot save while a native edit group is active")
        current = self.history.current_state_id
        if self.session.current_editor_state_id != current:
            raise RuntimeError("native history/session state identity mismatch before Save")
        captured = self.buffer.capture_full()
        self.session.synchronize_current_text(captured.text, state_id=current)
        return current

    def _replay(self, *, redo: bool) -> ReplayPlan | None:
        if self.history.group_active:
            raise RuntimeError("cannot Undo/Redo during a native edit group")
        hcp = self.history.checkpoint()
        scp = self.session.snapshot()
        plan = self.history.redo() if redo else self.history.undo()
        if plan is None:
            return None
        self._restoring_depth += 1
        try:
            self.buffer.apply_replay(plan)
            self.session.advance_editor_state(plan.target_state_id)
            self._notify_document_state_changed()
            return plan
        except BaseException:
            self.history.restore_checkpoint(hcp)
            self.session.restore_checkpoint(scp)
            raise
        finally:
            self._restoring_depth -= 1


    @staticmethod
    def _inverse_operations(operations: tuple[ReplayOperation, ...]) -> tuple[ReplayOperation, ...]:
        result: list[ReplayOperation] = []
        for operation in reversed(operations):
            kind = EditKind.DELETE if operation.kind is EditKind.INSERT else EditKind.INSERT
            result.append(ReplayOperation(kind, operation.offset, operation.text))
        return tuple(result)

    def apply_prevalidated_programmatic_group(
        self,
        *,
        operations: tuple[ReplayOperation, ...],
        expected_source_state_id: int,
        final_text: str,
        before_view: ViewState,
        target_view: ViewState,
    ) -> int:
        """Apply one explicit programmatic edit transaction.

        Final renderability and history payload are checked before any GTK mutation.
        Buffer operations own expected-delete verification/inverse rollback; this controller
        checkpoints DeltaHistory/DocumentSession and advances them only after buffer success.
        No full-document snapshot is stored in history.
        """
        if self.history.group_active:
            raise RuntimeError("cannot apply a programmatic edit during an active native group")
        if not isinstance(operations, tuple):
            operations = tuple(operations)
        if not isinstance(before_view, ViewState) or not isinstance(target_view, ViewState):
            raise TypeError("programmatic edit views must be ViewState")
        if not isinstance(final_text, str):
            raise TypeError("programmatic edit final_text must be a string")
        expected = int(expected_source_state_id)
        current = self.history.current_state_id
        if expected <= 0 or expected != current or self.session.current_editor_state_id != current:
            raise RuntimeError("stale programmatic edit plan: editor state identity changed")
        if not operations:
            return current
        payload_chars = sum(len(operation.text) for operation in operations)
        if payload_chars > self.history.max_payload_chars:
            raise RuntimeError(
                "programmatic edit exceeds Graphium's bounded Undo payload budget "
                f"({payload_chars} > {self.history.max_payload_chars} characters)"
            )
        # This is the only authority that permits programmatic signal suppression for a
        # text-changing command: the complete final text must first satisfy the published
        # Renderer-safety policy. Ordinary typing/paste/delete still use GTK guards.
        ensure_interactive_text_renderable(final_text)

        hcp = self.history.checkpoint()
        scp = self.session.snapshot()
        self.history.begin_group(before_view)
        try:
            for operation in operations:
                if operation.kind is EditKind.INSERT:
                    self.history.record_insert(operation.offset, operation.text)
                else:
                    self.history.record_delete(
                        operation.offset,
                        operation.text,
                        direction=DeleteDirection.RANGE,
                    )
        except BaseException:
            self.history.restore_checkpoint(hcp)
            raise

        buffer_applied = False
        self._restoring_depth += 1
        try:
            self.buffer.apply_operations(operations, target_view)
            buffer_applied = True
            state_id = self.history.end_group(
                target_view,
                saved_state_id=self.session.saved_editor_state_id,
            )
            self.session.advance_editor_state(state_id)
            self._notify_document_state_changed()
            return state_id
        except BaseException as exc:
            rollback_error: BaseException | None = None
            if buffer_applied:
                try:
                    self.buffer.apply_operations(self._inverse_operations(operations), before_view)
                except BaseException as rollback_exc:
                    rollback_error = rollback_exc
            self.history.restore_checkpoint(hcp)
            self.session.restore_checkpoint(scp)
            if rollback_error is not None:
                raise RuntimeError(
                    "programmatic edit authority commit failed and exact buffer rollback also failed: "
                    f"{rollback_error}"
                ) from rollback_error
            raise
        finally:
            self._restoring_depth -= 1

    def undo(self) -> ReplayPlan | None:
        return self._replay(redo=False)

    def redo(self) -> ReplayPlan | None:
        return self._replay(redo=True)
