"""Delta-based editor history for Graphium.

Savepoint identity semantics remain authoritative, while the native editor no longer stores a full
copy of the document for every native edit.  Undo data is represented as insert/delete
deltas grouped by structural editing boundaries.  State IDs are positive, monotonic and
never reused, including after Undo branching or checkpoint rollback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


DEFAULT_MAX_HISTORY_PAYLOAD_CHARS = 16_000_000


class EditKind(str, Enum):
    INSERT = "insert"
    DELETE = "delete"


class DeleteDirection(str, Enum):
    BACKWARD = "backward"
    FORWARD = "forward"
    RANGE = "range"


@dataclass(frozen=True)
class ViewState:
    insert_offset: int = 0
    selection_bound_offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "insert_offset", max(0, int(self.insert_offset)))
        object.__setattr__(self, "selection_bound_offset", max(0, int(self.selection_bound_offset)))


@dataclass(frozen=True)
class EditDelta:
    kind: EditKind
    offset: int
    text: str
    delete_direction: DeleteDirection = DeleteDirection.RANGE

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EditKind):
            object.__setattr__(self, "kind", EditKind(self.kind))
        if int(self.offset) < 0:
            raise ValueError("edit offset must be non-negative")
        object.__setattr__(self, "offset", int(self.offset))
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("edit delta text must be a non-empty string")
        if not isinstance(self.delete_direction, DeleteDirection):
            object.__setattr__(self, "delete_direction", DeleteDirection(self.delete_direction))
        if self.kind is EditKind.INSERT:
            object.__setattr__(self, "delete_direction", DeleteDirection.RANGE)


@dataclass(frozen=True)
class EditGroup:
    deltas: tuple[EditDelta, ...]
    before_state_id: int
    after_state_id: int
    before_view: ViewState
    after_view: ViewState

    def __post_init__(self) -> None:
        if not self.deltas:
            raise ValueError("edit group must contain at least one delta")
        if int(self.before_state_id) <= 0 or int(self.after_state_id) <= 0:
            raise ValueError("edit group state IDs must be positive")
        if self.before_state_id == self.after_state_id:
            raise ValueError("edit group must advance editor state identity")

    @property
    def payload_chars(self) -> int:
        return sum(len(delta.text) for delta in self.deltas)


@dataclass(frozen=True)
class ReplayOperation:
    kind: EditKind
    offset: int
    text: str


@dataclass(frozen=True)
class ReplayPlan:
    operations: tuple[ReplayOperation, ...]
    target_state_id: int
    target_view: ViewState


@dataclass(frozen=True)
class DeltaHistoryCheckpoint:
    undo_stack: tuple[EditGroup, ...]
    redo_stack: tuple[EditGroup, ...]
    current_state_id: int
    next_state_id: int
    pending_deltas: tuple[EditDelta, ...]
    pending_before_state_id: int | None
    pending_before_view: ViewState | None


@dataclass
class DeltaHistory:
    """Memory-bounded delta journal for the active native editor.

    The bound applies to stored changed payload, not document size.  A 10 MiB document
    with a one-character edit therefore stores one character of undo payload rather than
    a second 10 MiB snapshot.
    """

    max_groups: int = 1000
    max_payload_chars: int = DEFAULT_MAX_HISTORY_PAYLOAD_CHARS
    undo_stack: list[EditGroup] = field(default_factory=list)
    redo_stack: list[EditGroup] = field(default_factory=list)
    _current_state_id: int = 0
    _next_state_id: int = 1
    _pending_deltas: list[EditDelta] = field(default_factory=list, repr=False)
    _pending_before_state_id: int | None = field(default=None, repr=False)
    _pending_before_view: ViewState | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.max_groups = int(self.max_groups)
        self.max_payload_chars = int(self.max_payload_chars)
        if self.max_groups <= 0 or self.max_payload_chars <= 0:
            raise ValueError("delta history limits must be positive")

    def _fresh_state_id(self) -> int:
        value = self._next_state_id
        self._next_state_id += 1
        return value

    def reset(self) -> int:
        if self.group_active:
            raise RuntimeError("cannot reset delta history during an active edit group")
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._current_state_id = self._fresh_state_id()
        return self._current_state_id

    @property
    def current_state_id(self) -> int:
        if self._current_state_id <= 0:
            raise RuntimeError("delta history is not initialized")
        return self._current_state_id

    @property
    def group_active(self) -> bool:
        return self._pending_before_state_id is not None

    @property
    def can_undo(self) -> bool:
        return not self.group_active and bool(self.undo_stack)

    @property
    def can_redo(self) -> bool:
        return not self.group_active and bool(self.redo_stack)

    @property
    def stored_payload_chars(self) -> int:
        return sum(group.payload_chars for group in self.undo_stack + self.redo_stack)

    def begin_group(self, before_view: ViewState) -> None:
        if self.group_active:
            raise RuntimeError("nested native edit groups are not allowed")
        if self._current_state_id <= 0:
            raise RuntimeError("delta history must be reset before editing")
        if not isinstance(before_view, ViewState):
            raise TypeError("before_view must be ViewState")
        self._pending_before_state_id = self._current_state_id
        self._pending_before_view = before_view
        self._pending_deltas = []

    def record_insert(self, offset: int, text: str) -> None:
        if not self.group_active:
            raise RuntimeError("insert delta recorded outside an edit group")
        self._pending_deltas.append(EditDelta(EditKind.INSERT, offset, text))

    def record_delete(
        self,
        offset: int,
        text: str,
        *,
        direction: DeleteDirection = DeleteDirection.RANGE,
    ) -> None:
        if not self.group_active:
            raise RuntimeError("delete delta recorded outside an edit group")
        self._pending_deltas.append(EditDelta(EditKind.DELETE, offset, text, direction))

    def abort_group(self) -> None:
        self._pending_deltas = []
        self._pending_before_state_id = None
        self._pending_before_view = None

    @staticmethod
    def _text_class(text: str) -> str | None:
        if not text:
            return None
        # Keep explicit structural whitespace boundaries. Leafpad/L3afpad stop their
        # typing sequence around Return/Tab/space; Graphium generalizes that principle
        # without consulting global key state.
        if all(ch in "\r\n" for ch in text):
            return "newline"
        if all(ch == "\t" for ch in text):
            return "tab"
        if all(ch.isspace() and ch not in "\r\n\t" for ch in text):
            return "space"
        if all(not ch.isspace() for ch in text):
            return "word"
        return None

    @classmethod
    def _merged_group(
        cls,
        previous: EditGroup,
        current: EditGroup,
        *,
        saved_state_id: int | None,
    ) -> EditGroup | None:
        # A physical Save is a semantic boundary. Undo must always be able to land on
        # the exact saved state rather than jump across it because adjacent typing was
        # coalesced later.
        if saved_state_id is not None and previous.after_state_id == saved_state_id:
            return None
        if len(previous.deltas) != 1 or len(current.deltas) != 1:
            return None
        a, b = previous.deltas[0], current.deltas[0]
        if a.kind is not b.kind:
            return None
        # Only a new single-character user edit may extend a prior structural run.
        # Multi-character paste/delete remains one explicit unit.
        if len(b.text) != 1:
            return None
        if cls._text_class(a.text) != cls._text_class(b.text):
            return None
        if cls._text_class(a.text) is None:
            return None

        merged_delta: EditDelta | None = None
        if a.kind is EditKind.INSERT:
            if b.offset == a.offset + len(a.text):
                merged_delta = EditDelta(EditKind.INSERT, a.offset, a.text + b.text)
        elif a.delete_direction is b.delete_direction is DeleteDirection.BACKWARD:
            if b.offset + len(b.text) == a.offset:
                merged_delta = EditDelta(
                    EditKind.DELETE,
                    b.offset,
                    b.text + a.text,
                    DeleteDirection.BACKWARD,
                )
        elif a.delete_direction is b.delete_direction is DeleteDirection.FORWARD:
            if b.offset == a.offset:
                merged_delta = EditDelta(
                    EditKind.DELETE,
                    a.offset,
                    a.text + b.text,
                    DeleteDirection.FORWARD,
                )
        if merged_delta is None:
            return None
        return EditGroup(
            (merged_delta,),
            previous.before_state_id,
            current.after_state_id,
            previous.before_view,
            current.after_view,
        )

    def _trim(self) -> None:
        while len(self.undo_stack) > self.max_groups:
            self.undo_stack.pop(0)
        total = sum(group.payload_chars for group in self.undo_stack)
        while total > self.max_payload_chars and len(self.undo_stack) > 1:
            total -= self.undo_stack.pop(0).payload_chars

    def end_group(self, after_view: ViewState, *, saved_state_id: int | None = None) -> int:
        if not self.group_active:
            raise RuntimeError("no active native edit group")
        if not isinstance(after_view, ViewState):
            raise TypeError("after_view must be ViewState")
        before_id = self._pending_before_state_id
        before_view = self._pending_before_view
        assert before_id is not None and before_view is not None
        deltas = tuple(self._pending_deltas)
        self.abort_group()
        if not deltas:
            return self.current_state_id

        after_id = self._fresh_state_id()
        group = EditGroup(deltas, before_id, after_id, before_view, after_view)
        if self.undo_stack:
            merged = self._merged_group(self.undo_stack[-1], group, saved_state_id=saved_state_id)
            if merged is not None:
                self.undo_stack[-1] = merged
            else:
                self.undo_stack.append(group)
        else:
            self.undo_stack.append(group)
        self.redo_stack.clear()
        self._current_state_id = after_id
        self._trim()
        return after_id

    @staticmethod
    def _undo_operations(group: EditGroup) -> tuple[ReplayOperation, ...]:
        result: list[ReplayOperation] = []
        for delta in reversed(group.deltas):
            if delta.kind is EditKind.INSERT:
                result.append(ReplayOperation(EditKind.DELETE, delta.offset, delta.text))
            else:
                result.append(ReplayOperation(EditKind.INSERT, delta.offset, delta.text))
        return tuple(result)

    @staticmethod
    def _redo_operations(group: EditGroup) -> tuple[ReplayOperation, ...]:
        return tuple(ReplayOperation(delta.kind, delta.offset, delta.text) for delta in group.deltas)

    def undo(self) -> ReplayPlan | None:
        if self.group_active:
            raise RuntimeError("cannot undo during an active native edit group")
        if not self.undo_stack:
            return None
        group = self.undo_stack.pop()
        if group.after_state_id != self._current_state_id:
            raise RuntimeError("undo journal state chain is inconsistent")
        self.redo_stack.append(group)
        self._current_state_id = group.before_state_id
        return ReplayPlan(self._undo_operations(group), group.before_state_id, group.before_view)

    def redo(self) -> ReplayPlan | None:
        if self.group_active:
            raise RuntimeError("cannot redo during an active native edit group")
        if not self.redo_stack:
            return None
        group = self.redo_stack.pop()
        if group.before_state_id != self._current_state_id:
            raise RuntimeError("redo journal state chain is inconsistent")
        self.undo_stack.append(group)
        self._current_state_id = group.after_state_id
        self._trim()
        return ReplayPlan(self._redo_operations(group), group.after_state_id, group.after_view)

    def checkpoint(self) -> DeltaHistoryCheckpoint:
        return DeltaHistoryCheckpoint(
            tuple(self.undo_stack),
            tuple(self.redo_stack),
            self._current_state_id,
            self._next_state_id,
            tuple(self._pending_deltas),
            self._pending_before_state_id,
            self._pending_before_view,
        )

    def restore_checkpoint(self, checkpoint: DeltaHistoryCheckpoint) -> None:
        if not isinstance(checkpoint, DeltaHistoryCheckpoint):
            raise TypeError("checkpoint must be DeltaHistoryCheckpoint")
        self.undo_stack = list(checkpoint.undo_stack)
        self.redo_stack = list(checkpoint.redo_stack)
        self._current_state_id = checkpoint.current_state_id
        # IDs consumed speculatively are never reused.
        self._next_state_id = max(self._next_state_id, checkpoint.next_state_id)
        self._pending_deltas = list(checkpoint.pending_deltas)
        self._pending_before_state_id = checkpoint.pending_before_state_id
        self._pending_before_view = checkpoint.pending_before_view
