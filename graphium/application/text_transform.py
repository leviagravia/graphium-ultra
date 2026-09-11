"""GTK-free explicit text-transformation planner.

The planner is pure and immutable. It owns transformation semantics only; actual mutation,
Undo/Redo grouping, stale-state rejection, rollback and final renderability remain owned by
NativeEditorController.apply_prevalidated_programmatic_group().
"""
from __future__ import annotations

from dataclasses import dataclass

from graphium.application.renderability import ensure_interactive_text_renderable
from graphium.domain.edit_history import (
    DEFAULT_MAX_HISTORY_PAYLOAD_CHARS,
    EditKind,
    ReplayOperation,
    ViewState,
)


MAX_TRANSFORM_CHANGED_SPANS = 50_000


class TransformInputError(ValueError):
    pass


class TransformScaleError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransformationPlan:
    source_state_id: int
    source_text: str
    final_text: str
    operations: tuple[ReplayOperation, ...]
    before_view: ViewState
    target_view: ViewState
    changed_span_count: int

    def __post_init__(self) -> None:
        if int(self.source_state_id) <= 0:
            raise ValueError("transformation plan source state id must be positive")
        if not isinstance(self.source_text, str) or not isinstance(self.final_text, str):
            raise TypeError("transformation plan text must be strings")
        if not isinstance(self.before_view, ViewState) or not isinstance(self.target_view, ViewState):
            raise TypeError("transformation plan views must be ViewState")
        if int(self.changed_span_count) < 0:
            raise ValueError("changed_span_count must be non-negative")

    @property
    def changed(self) -> bool:
        return bool(self.operations)

    @property
    def payload_chars(self) -> int:
        return sum(len(operation.text) for operation in self.operations)


@dataclass(frozen=True)
class _Line:
    start: int
    content_end: int
    full_end: int


@dataclass(frozen=True)
class _Scope:
    first: int
    last: int
    lo: int
    hi: int


def _validate_inputs(source_text: str, source_state_id: int, before_view: ViewState) -> None:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    if int(source_state_id) <= 0:
        raise TransformInputError("source_state_id must be positive")
    if not isinstance(before_view, ViewState):
        raise TypeError("before_view must be ViewState")
    n = len(source_text)
    if before_view.insert_offset > n or before_view.selection_bound_offset > n:
        raise TransformInputError("ViewState offset exceeds source text length")


def _no_op(source_text: str, source_state_id: int, before_view: ViewState) -> TransformationPlan:
    return TransformationPlan(
        source_state_id, source_text, source_text, (), before_view, before_view, 0
    )


def _selection(before_view: ViewState) -> tuple[int, int]:
    return (
        min(before_view.insert_offset, before_view.selection_bound_offset),
        max(before_view.insert_offset, before_view.selection_bound_offset),
    )


def _directed_view(before_view: ViewState, lo: int, hi: int) -> ViewState:
    if before_view.insert_offset > before_view.selection_bound_offset:
        return ViewState(hi, lo)
    return ViewState(lo, hi)


def _prevalidate_final(final_text: str, operations: tuple[ReplayOperation, ...]) -> None:
    payload = sum(len(operation.text) for operation in operations)
    if payload > DEFAULT_MAX_HISTORY_PAYLOAD_CHARS:
        raise TransformScaleError(
            "text transformation exceeds Graphium's bounded Undo payload budget "
            f"({payload} > {DEFAULT_MAX_HISTORY_PAYLOAD_CHARS} characters)"
        )
    ensure_interactive_text_renderable(final_text)


def _replace_plan(
    *,
    source_text: str,
    source_state_id: int,
    before_view: ViewState,
    start: int,
    end: int,
    replacement: str,
    target_view: ViewState,
) -> TransformationPlan:
    original = source_text[start:end]
    if original == replacement:
        return _no_op(source_text, source_state_id, before_view)
    ops: list[ReplayOperation] = []
    if original:
        ops.append(ReplayOperation(EditKind.DELETE, start, original))
    if replacement:
        ops.append(ReplayOperation(EditKind.INSERT, start, replacement))
    operations = tuple(ops)
    final_text = source_text[:start] + replacement + source_text[end:]
    _prevalidate_final(final_text, operations)
    return TransformationPlan(
        source_state_id, source_text, final_text, operations,
        before_view, target_view, 1,
    )


def plan_uppercase(*, source_text: str, source_state_id: int, before_view: ViewState) -> TransformationPlan:
    _validate_inputs(source_text, source_state_id, before_view)
    lo, hi = _selection(before_view)
    if lo == hi:
        return _no_op(source_text, source_state_id, before_view)
    replacement = source_text[lo:hi].upper()
    target = _directed_view(before_view, lo, lo + len(replacement))
    return _replace_plan(
        source_text=source_text, source_state_id=source_state_id, before_view=before_view,
        start=lo, end=hi, replacement=replacement, target_view=target,
    )


def plan_lowercase(*, source_text: str, source_state_id: int, before_view: ViewState) -> TransformationPlan:
    _validate_inputs(source_text, source_state_id, before_view)
    lo, hi = _selection(before_view)
    if lo == hi:
        return _no_op(source_text, source_state_id, before_view)
    replacement = source_text[lo:hi].lower()
    target = _directed_view(before_view, lo, lo + len(replacement))
    return _replace_plan(
        source_text=source_text, source_state_id=source_state_id, before_view=before_view,
        start=lo, end=hi, replacement=replacement, target_view=target,
    )


def _real_lines(text: str) -> tuple[_Line, ...]:
    if text == "":
        return (_Line(0, 0, 0),)
    lines: list[_Line] = []
    start = 0
    n = len(text)
    while start < n:
        nl = text.find("\n", start)
        if nl < 0:
            lines.append(_Line(start, n, n))
            break
        lines.append(_Line(start, nl, nl + 1))
        start = nl + 1
    # A final LF exposes a terminal zero-length sentinel, but it is intentionally
    # not a real movable line. Duplicate handles that sentinel separately.
    return tuple(lines)


def _line_index_for_char(lines: tuple[_Line, ...], char_offset: int) -> int:
    if char_offset < 0:
        return 0
    for idx, line in enumerate(lines):
        if char_offset < line.full_end:
            return idx
        if line.full_end == line.start == char_offset:
            return idx
    return max(0, len(lines) - 1)


def _line_index_for_caret(text: str, lines: tuple[_Line, ...], offset: int) -> int | None:
    if text.endswith("\n") and offset == len(text):
        return None  # terminal sentinel
    if text == "":
        return 0
    if offset == len(text):
        return len(lines) - 1
    return _line_index_for_char(lines, offset)


def _selected_line_scope(text: str, before_view: ViewState) -> _Scope | None:
    lo, hi = _selection(before_view)
    lines = _real_lines(text)
    if lo == hi:
        idx = _line_index_for_caret(text, lines, before_view.insert_offset)
        if idx is None:
            return None
        return _Scope(idx, idx, lo, hi)
    first = _line_index_for_char(lines, lo)
    last = _line_index_for_char(lines, hi - 1)
    return _Scope(first, last, lo, hi)


def plan_duplicate_line_selection(
    *, source_text: str, source_state_id: int, before_view: ViewState
) -> TransformationPlan:
    _validate_inputs(source_text, source_state_id, before_view)
    lo, hi = _selection(before_view)
    if lo != hi:
        selected = source_text[lo:hi]
        operations = (ReplayOperation(EditKind.INSERT, hi, selected),)
        final_text = source_text[:hi] + selected + source_text[hi:]
        target = _directed_view(before_view, hi, hi + len(selected))
        _prevalidate_final(final_text, operations)
        return TransformationPlan(
            source_state_id, source_text, final_text, operations,
            before_view, target, 1,
        )

    caret = before_view.insert_offset
    # Terminal empty line after an existing final LF is duplicable and yields one more LF.
    if source_text.endswith("\n") and caret == len(source_text):
        operations = (ReplayOperation(EditKind.INSERT, len(source_text), "\n"),)
        final_text = source_text + "\n"
        target = ViewState(len(source_text) + 1, len(source_text) + 1)
        _prevalidate_final(final_text, operations)
        return TransformationPlan(
            source_state_id, source_text, final_text, operations,
            before_view, target, 1,
        )

    lines = _real_lines(source_text)
    idx = _line_index_for_caret(source_text, lines, caret)
    assert idx is not None
    line = lines[idx]
    column = max(0, caret - line.start)
    line_text = source_text[line.start:line.full_end]
    if line.full_end > line.content_end:  # terminating LF exists
        insertion = line_text
        insert_at = line.full_end
        new_line_start = insert_at
    else:
        insertion = "\n" + source_text[line.start:line.content_end]
        insert_at = line.full_end
        new_line_start = insert_at + 1
    # Empty document is the final empty line without LF.
    if source_text == "":
        insertion = "\n"
        insert_at = 0
        new_line_start = 1
    operations = (ReplayOperation(EditKind.INSERT, insert_at, insertion),)
    final_text = source_text[:insert_at] + insertion + source_text[insert_at:]
    target_offset = min(new_line_start + column, len(final_text))
    target = ViewState(target_offset, target_offset)
    _prevalidate_final(final_text, operations)
    return TransformationPlan(
        source_state_id, source_text, final_text, operations,
        before_view, target, 1,
    )


def _line_identity_for_endpoint(
    lines: tuple[_Line, ...], first: int, last: int, offset: int
) -> tuple[int, int] | None:
    """Return (source line index, character column) for an endpoint inside the block."""
    x = int(offset)
    for idx in range(first, last + 1):
        line = lines[idx]
        if x == line.start:
            return idx, 0
        if line.start < x <= line.content_end:
            return idx, x - line.start
    return None


def _moved_target_view(
    source_text: str,
    before_view: ViewState,
    lines: tuple[_Line, ...],
    *,
    first: int,
    last: int,
    neighbor_idx: int,
    moving_up: bool,
    region_start: int,
    region_end: int,
    selection_active: bool,
) -> ViewState:
    block_start = lines[first].start
    block_end = lines[last].full_end
    if moving_up:
        order = list(range(first, last + 1)) + [neighbor_idx]
    else:
        order = [neighbor_idx] + list(range(first, last + 1))

    new_starts: dict[int, int] = {}
    cursor = region_start
    for pos, idx in enumerate(order):
        new_starts[idx] = cursor
        cursor += lines[idx].content_end - lines[idx].start
        if pos < len(order) - 1:
            cursor += 1
    final_eol = source_text[region_end - 1:region_end] == "\n"
    if final_eol:
        cursor += 1
    assert cursor == region_end

    if moving_up:
        # The block is followed by the previous line, so its endpoint affinity is the
        # new start of that previous line (after the separator).
        block_end_affinity = new_starts[neighbor_idx]
    else:
        # The block now ends the region and inherits the region's final-EOL status.
        block_end_affinity = region_end

    def map_one(x: int) -> int:
        if selection_active and x == block_end:
            return block_end_affinity
        identity = _line_identity_for_endpoint(lines, first, last, x)
        if identity is None:
            return x
        idx, column = identity
        return new_starts[idx] + column

    return ViewState(map_one(before_view.insert_offset), map_one(before_view.selection_bound_offset))


def _plan_move(
    *, source_text: str, source_state_id: int, before_view: ViewState, moving_up: bool
) -> TransformationPlan:
    _validate_inputs(source_text, source_state_id, before_view)
    lines = _real_lines(source_text)
    scope = _selected_line_scope(source_text, before_view)
    if scope is None:
        return _no_op(source_text, source_state_id, before_view)
    first, last = scope.first, scope.last
    selection_active = scope.lo != scope.hi

    if moving_up:
        if first <= 0:
            return _no_op(source_text, source_state_id, before_view)
        neighbor_idx = first - 1
        region_start = lines[neighbor_idx].start
        region_end = lines[last].full_end
        order = list(range(first, last + 1)) + [neighbor_idx]
    else:
        if last >= len(lines) - 1:
            return _no_op(source_text, source_state_id, before_view)
        neighbor_idx = last + 1
        region_start = lines[first].start
        region_end = lines[neighbor_idx].full_end
        order = [neighbor_idx] + list(range(first, last + 1))

    contents = [source_text[lines[idx].start:lines[idx].content_end] for idx in order]
    replacement = "\n".join(contents)
    if source_text[region_end - 1:region_end] == "\n":
        replacement += "\n"
    original = source_text[region_start:region_end]
    if original == replacement:
        return _no_op(source_text, source_state_id, before_view)
    target = _moved_target_view(
        source_text,
        before_view,
        lines,
        first=first,
        last=last,
        neighbor_idx=neighbor_idx,
        moving_up=moving_up,
        region_start=region_start,
        region_end=region_end,
        selection_active=selection_active,
    )
    return _replace_plan(
        source_text=source_text, source_state_id=source_state_id, before_view=before_view,
        start=region_start, end=region_end, replacement=replacement,
        target_view=target,
    )


def plan_move_lines_up(*, source_text: str, source_state_id: int, before_view: ViewState) -> TransformationPlan:
    return _plan_move(
        source_text=source_text, source_state_id=source_state_id,
        before_view=before_view, moving_up=True,
    )


def plan_move_lines_down(*, source_text: str, source_state_id: int, before_view: ViewState) -> TransformationPlan:
    return _plan_move(
        source_text=source_text, source_state_id=source_state_id,
        before_view=before_view, moving_up=False,
    )


def _trailing_runs_for_line(text: str, line: _Line) -> tuple[int, int] | None:
    end = line.content_end
    start = end
    while start > line.start and text[start - 1] in " \t":
        start -= 1
    if start == end:
        return None
    return start, end


def _map_offset_through_deletions(offset: int, runs: tuple[tuple[int, int], ...]) -> int:
    removed = 0
    x = int(offset)
    for start, end in runs:
        if x < start:
            break
        if x <= end:
            return start - removed
        removed += end - start
    return x - removed


def plan_trim_trailing_spaces(
    *, source_text: str, source_state_id: int, before_view: ViewState
) -> TransformationPlan:
    _validate_inputs(source_text, source_state_id, before_view)
    lines = _real_lines(source_text)
    lo, hi = _selection(before_view)
    if lo == hi:
        first, last = 0, len(lines) - 1
    else:
        scope = _selected_line_scope(source_text, before_view)
        assert scope is not None
        first, last = scope.first, scope.last

    runs: list[tuple[int, int]] = []
    for idx in range(first, last + 1):
        run = _trailing_runs_for_line(source_text, lines[idx])
        if run is not None:
            runs.append(run)
            if len(runs) > MAX_TRANSFORM_CHANGED_SPANS:
                raise TransformScaleError(
                    "Trim Trailing Spaces exceeds Graphium's changed-span planning cap "
                    f"({len(runs)} > {MAX_TRANSFORM_CHANGED_SPANS})"
                )
    if not runs:
        return _no_op(source_text, source_state_id, before_view)

    runs_tuple = tuple(runs)
    operations = tuple(
        ReplayOperation(EditKind.DELETE, start, source_text[start:end])
        for start, end in reversed(runs_tuple)
    )
    # Linear materialization; do not repeatedly slice/rebuild the full document per run.
    pieces: list[str] = []
    cursor = 0
    for start, end in runs_tuple:
        pieces.append(source_text[cursor:start])
        cursor = end
    pieces.append(source_text[cursor:])
    final_text = "".join(pieces)
    target = ViewState(
        _map_offset_through_deletions(before_view.insert_offset, runs_tuple),
        _map_offset_through_deletions(before_view.selection_bound_offset, runs_tuple),
    )
    _prevalidate_final(final_text, operations)
    return TransformationPlan(
        source_state_id, source_text, final_text, operations,
        before_view, target, len(runs_tuple),
    )


PLANNERS = {
    "uppercase": plan_uppercase,
    "lowercase": plan_lowercase,
    "duplicate-line-selection": plan_duplicate_line_selection,
    "move-lines-up": plan_move_lines_up,
    "move-lines-down": plan_move_lines_down,
    "trim-trailing-spaces": plan_trim_trailing_spaces,
}


def build_transformation_plan(
    action: str,
    *,
    source_text: str,
    source_state_id: int,
    before_view: ViewState,
) -> TransformationPlan:
    try:
        planner = PLANNERS[str(action)]
    except KeyError as exc:
        raise TransformInputError(f"unknown text transformation: {action!r}") from exc
    return planner(
        source_text=source_text,
        source_state_id=source_state_id,
        before_view=before_view,
    )
