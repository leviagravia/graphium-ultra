"""Gtk.TextBuffer adapter for Graphium native delta editing."""
from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from graphium.domain.edit_history import (
    DeleteDirection,
    EditKind,
    ReplayOperation,
    ReplayPlan,
    ViewState,
)
from graphium.domain.history import HistoryState


class GtkTextBufferPort:
    __slots__ = ("buffer",)

    def __init__(self, buffer: Gtk.TextBuffer) -> None:
        if not isinstance(buffer, Gtk.TextBuffer):
            raise TypeError("buffer must be Gtk.TextBuffer")
        self.buffer = buffer

    def capture_full(self) -> HistoryState:
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        view = self.capture_view()
        return HistoryState(
            text=text,
            insert_offset=view.insert_offset,
            selection_bound_offset=view.selection_bound_offset,
        )

    # Protocol name retained for headless adapters that use the same shape.
    def capture(self) -> HistoryState:
        return self.capture_full()

    def capture_view(self) -> ViewState:
        insert = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        bound = self.buffer.get_iter_at_mark(self.buffer.get_selection_bound()).get_offset()
        return ViewState(insert, bound)

    def restore_full(self, state: HistoryState) -> None:
        if not isinstance(state, HistoryState):
            raise TypeError("state must be HistoryState")
        self.buffer.set_text(state.text)
        insert = self.buffer.get_iter_at_offset(state.insert_offset)
        bound = self.buffer.get_iter_at_offset(state.selection_bound_offset)
        self.buffer.select_range(insert, bound)

    def restore(self, state: HistoryState) -> None:
        self.restore_full(state)

    def text_in_range(self, start_offset: int, end_offset: int) -> str:
        start = self.buffer.get_iter_at_offset(int(start_offset))
        end = self.buffer.get_iter_at_offset(int(end_offset))
        return self.buffer.get_text(start, end, True)

    def delete_direction(self, start_offset: int, end_offset: int) -> DeleteDirection:
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        if cursor == int(end_offset):
            return DeleteDirection.BACKWARD
        if cursor == int(start_offset):
            return DeleteDirection.FORWARD
        return DeleteDirection.RANGE

    def _insert(self, offset: int, text: str) -> None:
        it = self.buffer.get_iter_at_offset(offset)
        self.buffer.insert(it, text)

    def _delete_expected(self, offset: int, text: str) -> None:
        start = self.buffer.get_iter_at_offset(offset)
        end = self.buffer.get_iter_at_offset(offset + len(text))
        actual = self.buffer.get_text(start, end, True)
        if actual != text:
            raise RuntimeError(
                f"delta replay mismatch at {offset}: expected {text!r}, found {actual!r}"
            )
        self.buffer.delete(start, end)

    @staticmethod
    def _inverse(operation: ReplayOperation) -> ReplayOperation:
        kind = EditKind.DELETE if operation.kind is EditKind.INSERT else EditKind.INSERT
        return ReplayOperation(kind, operation.offset, operation.text)

    def _apply_operation(self, operation: ReplayOperation) -> None:
        if operation.kind is EditKind.INSERT:
            self._insert(operation.offset, operation.text)
        else:
            self._delete_expected(operation.offset, operation.text)

    def apply_operations(
        self,
        operations: tuple[ReplayOperation, ...],
        target_view: ViewState,
    ) -> None:
        if not isinstance(target_view, ViewState):
            raise TypeError("target_view must be ViewState")
        applied: list[ReplayOperation] = []
        try:
            for operation in operations:
                if not isinstance(operation, ReplayOperation):
                    raise TypeError("operations must contain ReplayOperation values")
                self._apply_operation(operation)
                applied.append(operation)
        except BaseException:
            rollback_error: BaseException | None = None
            for operation in reversed(applied):
                try:
                    self._apply_operation(self._inverse(operation))
                except BaseException as exc:
                    rollback_error = exc
                    break
            if rollback_error is not None:
                raise RuntimeError(
                    f"delta operation failed and buffer rollback also failed: {rollback_error}"
                ) from rollback_error
            raise
        insert = self.buffer.get_iter_at_offset(target_view.insert_offset)
        bound = self.buffer.get_iter_at_offset(target_view.selection_bound_offset)
        self.buffer.select_range(insert, bound)

    def apply_replay(self, plan: ReplayPlan) -> None:
        if not isinstance(plan, ReplayPlan):
            raise TypeError("plan must be ReplayPlan")
        self.apply_operations(plan.operations, plan.target_view)
