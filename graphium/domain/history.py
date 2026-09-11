"""Bounded, savepoint-aware text history for Graphium.

The model is deliberately GTK-free. A committed text state receives a positive,
monotonic identity that is never reused during the history lifetime.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace

@dataclass(frozen=True)
class HistoryState:
    text: str
    insert_offset: int = 0
    selection_bound_offset: int = 0
    state_id: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("history text must be a string")
        limit = len(self.text)
        object.__setattr__(self, "insert_offset", max(0, min(int(self.insert_offset), limit)))
        object.__setattr__(self, "selection_bound_offset", max(0, min(int(self.selection_bound_offset), limit)))
        state_id = int(self.state_id)
        if state_id < 0:
            raise ValueError("history state_id must be non-negative")
        object.__setattr__(self, "state_id", state_id)

    @property
    def has_selection(self) -> bool:
        return self.insert_offset != self.selection_bound_offset

@dataclass(frozen=True)
class HistoryCheckpoint:
    undo_stack: tuple[HistoryState, ...]
    redo_stack: tuple[HistoryState, ...]
    disabled_reason: str | None
    next_state_id: int

@dataclass
class TextHistory:
    max_steps: int = 100
    max_snapshot_chars: int = 750_000
    max_total_chars: int = 2_500_000
    undo_stack: list[HistoryState] = field(default_factory=list)
    redo_stack: list[HistoryState] = field(default_factory=list)
    disabled_reason: str | None = None
    _next_state_id: int = field(default=1, repr=False)

    def __post_init__(self) -> None:
        for name in ("max_steps", "max_snapshot_chars", "max_total_chars"):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            setattr(self, name, value)

    @staticmethod
    def _coerce(value: HistoryState | str) -> HistoryState:
        if isinstance(value, HistoryState):
            return value
        if isinstance(value, str):
            return HistoryState(value)
        raise TypeError("history state must be HistoryState or str")

    def _fresh(self, value: HistoryState | str) -> HistoryState:
        assigned = replace(self._coerce(value), state_id=self._next_state_id)
        self._next_state_id += 1
        return assigned

    def _preserve_id(self, value: HistoryState | str, state_id: int) -> HistoryState:
        return replace(self._coerce(value), state_id=state_id)

    def _too_large(self, state: HistoryState) -> bool:
        return len(state.text) > self.max_snapshot_chars

    def _trim(self) -> None:
        while len(self.undo_stack) > self.max_steps + 1:
            self.undo_stack.pop(0)
        total = sum(len(item.text) for item in self.undo_stack)
        while total > self.max_total_chars and len(self.undo_stack) > 1:
            total -= len(self.undo_stack.pop(0).text)

    def reset(self, value: HistoryState | str) -> HistoryState:
        assigned = self._fresh(value)
        self.undo_stack = [assigned]
        self.redo_stack.clear()
        self.disabled_reason = "Undo history limited for large documents" if self._too_large(assigned) else None
        return assigned

    def replace_current_view_state(self, value: HistoryState | str) -> bool:
        state = self._coerce(value)
        if not self.undo_stack or self.undo_stack[-1].text != state.text:
            return False
        self.undo_stack[-1] = self._preserve_id(state, self.undo_stack[-1].state_id)
        return True

    def commit(self, value: HistoryState | str) -> bool:
        state = self._coerce(value)
        if not self.undo_stack:
            self.reset(state)
            return False
        if self.undo_stack[-1].text == state.text:
            self.replace_current_view_state(state)
            return False
        assigned = self._fresh(state)
        if self._too_large(assigned):
            self.undo_stack = [assigned]
            self.redo_stack.clear()
            self.disabled_reason = "Undo history limited for large documents"
            return True
        self.disabled_reason = None
        self.undo_stack.append(assigned)
        self._trim()
        self.redo_stack.clear()
        return True

    def undo(self, current_value: HistoryState | str) -> HistoryState | None:
        current_value = self._coerce(current_value)
        if self.disabled_reason or len(self.undo_stack) <= 1:
            return None
        committed = self.undo_stack.pop()
        redo_state = committed if committed.text == current_value.text else self._fresh(current_value)
        self.redo_stack.append(redo_state)
        return self.undo_stack[-1]

    def redo(self) -> HistoryState | None:
        if self.disabled_reason or not self.redo_stack:
            return None
        state = self.redo_stack.pop()
        self.undo_stack.append(state)
        self._trim()
        return state

    def checkpoint(self) -> HistoryCheckpoint:
        return HistoryCheckpoint(tuple(self.undo_stack), tuple(self.redo_stack), self.disabled_reason, self._next_state_id)

    def restore_checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        if not isinstance(checkpoint, HistoryCheckpoint):
            raise TypeError("checkpoint must be HistoryCheckpoint")
        self.undo_stack = list(checkpoint.undo_stack)
        self.redo_stack = list(checkpoint.redo_stack)
        self.disabled_reason = checkpoint.disabled_reason
        # IDs allocated by speculative work are never reused even when the
        # visible/history stacks roll back to an earlier checkpoint.
        self._next_state_id = max(self._next_state_id, checkpoint.next_state_id)

    @property
    def current(self) -> HistoryState | None:
        return self.undo_stack[-1] if self.undo_stack else None

    @property
    def current_state_id(self) -> int | None:
        current = self.current
        return current.state_id if current is not None and current.state_id > 0 else None

    @property
    def can_undo(self) -> bool:
        return self.disabled_reason is None and len(self.undo_stack) > 1

    @property
    def can_redo(self) -> bool:
        return self.disabled_reason is None and bool(self.redo_stack)
