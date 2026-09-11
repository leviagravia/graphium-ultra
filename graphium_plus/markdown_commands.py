"""GTK-free Markdown writing command catalog and edit planner for Graphium Plus.

The catalog is the single Plus authority shared by ``Commands > Markdown`` and the
later editor-local Markdown toolbar.  Plans are immutable and text-first; actual GTK
mutation, stale-state rejection, rollback and Undo/Redo grouping remain owned by
NativeEditorController.apply_prevalidated_programmatic_group().
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from graphium.application.renderability import ensure_interactive_text_renderable
from graphium.domain.edit_history import (
    DEFAULT_MAX_HISTORY_PAYLOAD_CHARS,
    EditKind,
    ReplayOperation,
    ViewState,
)
from graphium_plus.markdown import MarkdownBlockKind, build_markdown_document_map


class MarkdownCommandError(ValueError):
    """Rejected Markdown command request."""


@dataclass(frozen=True, slots=True)
class MarkdownCommandSpec:
    action: str
    label: str
    group: str
    toolbar_markup: str | None = None


MARKDOWN_COMMANDS = (
    MarkdownCommandSpec("markdown-heading-1", "Heading 1", "Headings", "H<sub>1</sub>"),
    MarkdownCommandSpec("markdown-heading-2", "Heading 2", "Headings", "H<sub>2</sub>"),
    MarkdownCommandSpec("markdown-heading-3", "Heading 3", "Headings"),
    MarkdownCommandSpec("markdown-heading-4", "Heading 4", "Headings"),
    MarkdownCommandSpec("markdown-heading-5", "Heading 5", "Headings"),
    MarkdownCommandSpec("markdown-heading-6", "Heading 6", "Headings"),
    MarkdownCommandSpec("markdown-bold", "Bold", "Inline", "<b>B</b>"),
    MarkdownCommandSpec("markdown-italic", "Italic", "Inline", "<i>I</i>"),
    MarkdownCommandSpec("markdown-strikethrough", "Strikethrough", "Inline", "<s>S</s>"),
    MarkdownCommandSpec("markdown-inline-code", "Inline Code", "Inline", "<tt>`</tt>"),
    MarkdownCommandSpec("markdown-blockquote", "Blockquote", "Blocks", "&gt;"),
    MarkdownCommandSpec("markdown-unordered-list", "Bulleted List", "Blocks", "•"),
    MarkdownCommandSpec("markdown-ordered-list", "Numbered List", "Blocks", "1."),
    MarkdownCommandSpec("markdown-task-list", "Task List", "Blocks", "☑"),
    MarkdownCommandSpec("markdown-fenced-code", "Code Block", "Blocks", "<tt>```</tt>"),
    MarkdownCommandSpec("markdown-thematic-break", "Thematic Break", "Blocks"),
    MarkdownCommandSpec("markdown-link", "Link", "Insert", "⛓"),
    MarkdownCommandSpec("markdown-image", "Image", "Insert", "▧"),
    MarkdownCommandSpec("markdown-table", "Table", "Insert"),
    MarkdownCommandSpec("insert-footnote", "Insert Footnote", "Academic", "[^]"),
    MarkdownCommandSpec("insert-inline-note", "Insert Inline Note", "Academic"),
    MarkdownCommandSpec("go-to-footnote", "Go to Footnote / Reference", "Academic"),
    MarkdownCommandSpec("check-footnotes", "Check Footnotes", "Academic"),
)
MARKDOWN_COMMAND_GROUPS = ("Headings", "Inline", "Blocks", "Insert", "Academic")
ACADEMIC_MARKDOWN_ACTIONS = frozenset(
    {"insert-footnote", "insert-inline-note", "go-to-footnote", "check-footnotes"}
)
PLANNED_MARKDOWN_ACTIONS = frozenset(
    spec.action for spec in MARKDOWN_COMMANDS if spec.action not in ACADEMIC_MARKDOWN_ACTIONS
)


@dataclass(frozen=True, slots=True)
class MarkdownEditPlan:
    source_state_id: int
    source_text: str
    final_text: str
    operations: tuple[ReplayOperation, ...]
    before_view: ViewState
    target_view: ViewState
    action: str

    @property
    def changed(self) -> bool:
        return bool(self.operations)


_LINE_PREFIX_RE = re.compile(
    r"^( {0,3})(?:(?:>[ \t]?)|(?:[-+*][ \t]+(?:\[[ xX]\][ \t]+)?)|(?:\d{1,9}[.)][ \t]+))"
)
_ATX_PREFIX_RE = re.compile(r"^( {0,3})#{1,6}(?:[ \t]+|$)")


def _validate(source_text: str, source_state_id: int, before_view: ViewState) -> None:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be str")
    if int(source_state_id) <= 0:
        raise MarkdownCommandError("source_state_id must be positive")
    if not isinstance(before_view, ViewState):
        raise TypeError("before_view must be ViewState")
    if max(before_view.insert_offset, before_view.selection_bound_offset) > len(source_text):
        raise MarkdownCommandError("view offset exceeds source text length")


def _selection(view: ViewState) -> tuple[int, int]:
    return (
        min(view.insert_offset, view.selection_bound_offset),
        max(view.insert_offset, view.selection_bound_offset),
    )


def _directed_view(before: ViewState, lo: int, hi: int) -> ViewState:
    if before.insert_offset >= before.selection_bound_offset:
        return ViewState(hi, lo)
    return ViewState(lo, hi)


def _prevalidate(final_text: str, operations: tuple[ReplayOperation, ...]) -> None:
    payload = sum(len(operation.text) for operation in operations)
    if payload > DEFAULT_MAX_HISTORY_PAYLOAD_CHARS:
        raise MarkdownCommandError(
            "Markdown command exceeds Graphium's bounded Undo payload budget "
            f"({payload} > {DEFAULT_MAX_HISTORY_PAYLOAD_CHARS} characters)"
        )
    ensure_interactive_text_renderable(final_text)


def _plan_replace(
    *,
    action: str,
    source_text: str,
    source_state_id: int,
    before_view: ViewState,
    start: int,
    end: int,
    replacement: str,
    target_view: ViewState,
) -> MarkdownEditPlan:
    original = source_text[start:end]
    if original == replacement:
        return MarkdownEditPlan(
            source_state_id, source_text, source_text, (), before_view, before_view, action
        )
    operations: list[ReplayOperation] = []
    if original:
        operations.append(ReplayOperation(EditKind.DELETE, start, original))
    if replacement:
        operations.append(ReplayOperation(EditKind.INSERT, start, replacement))
    ops = tuple(operations)
    final = source_text[:start] + replacement + source_text[end:]
    _prevalidate(final, ops)
    return MarkdownEditPlan(
        source_state_id, source_text, final, ops, before_view, target_view, action
    )


def _line_content_range(text: str, offset: int) -> tuple[int, int]:
    offset = max(0, min(offset, len(text)))
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return start, len(text) if end < 0 else end


def _line_scope(text: str, view: ViewState) -> tuple[int, int]:
    lo, hi = _selection(view)
    start, _ = _line_content_range(text, lo)
    probe = hi - 1 if hi > lo else hi
    _, end = _line_content_range(text, probe)
    return start, end


def _max_run(text: str, character: str) -> int:
    best = run = 0
    for current in text:
        if current == character:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _inline_wrap(
    action: str,
    marker: str,
    source_text: str,
    source_state_id: int,
    before_view: ViewState,
) -> MarkdownEditPlan:
    lo, hi = _selection(before_view)
    if "\n" in source_text[lo:hi]:
        raise MarkdownCommandError("inline Markdown formatting does not accept a multiline selection")
    if lo == hi:
        insertion = marker + marker
        final = source_text[:lo] + insertion + source_text[lo:]
        ops = (ReplayOperation(EditKind.INSERT, lo, insertion),)
        target = ViewState(lo + len(marker), lo + len(marker))
        _prevalidate(final, ops)
        return MarkdownEditPlan(
            source_state_id, source_text, final, ops, before_view, target, action
        )

    selected = source_text[lo:hi]
    if lo >= len(marker) and source_text[lo - len(marker):lo] == marker and source_text[hi:hi + len(marker)] == marker:
        # Remove trailing markup first so the leading offset remains valid.
        ops = (
            ReplayOperation(EditKind.DELETE, hi, marker),
            ReplayOperation(EditKind.DELETE, lo - len(marker), marker),
        )
        final = source_text[:lo - len(marker)] + selected + source_text[hi + len(marker):]
        target = _directed_view(before_view, lo - len(marker), hi - len(marker))
        _prevalidate(final, ops)
        return MarkdownEditPlan(
            source_state_id, source_text, final, ops, before_view, target, action
        )

    ops = (
        ReplayOperation(EditKind.INSERT, hi, marker),
        ReplayOperation(EditKind.INSERT, lo, marker),
    )
    final = source_text[:lo] + marker + selected + marker + source_text[hi:]
    target = _directed_view(before_view, lo + len(marker), hi + len(marker))
    _prevalidate(final, ops)
    return MarkdownEditPlan(
        source_state_id, source_text, final, ops, before_view, target, action
    )


def _inline_code(
    source_text: str, source_state_id: int, before_view: ViewState
) -> MarkdownEditPlan:
    lo, hi = _selection(before_view)
    selected = source_text[lo:hi]
    marker = "`" * max(1, _max_run(selected, "`") + 1)
    return _inline_wrap(
        "markdown-inline-code", marker, source_text, source_state_id, before_view
    )


def _heading(
    level: int, source_text: str, source_state_id: int, before_view: ViewState
) -> MarkdownEditPlan:
    lo, hi = _selection(before_view)
    line_start, line_end = _line_scope(source_text, before_view)
    if hi > lo and "\n" in source_text[lo:hi]:
        raise MarkdownCommandError("heading command requires one source line")

    document_map = build_markdown_document_map(source_text)
    caret = before_view.insert_offset
    heading = next(
        (
            block for block in document_map.headings
            if block.source_start <= caret <= block.source_end
            or (lo < hi and block.source_start <= lo and hi <= block.source_end)
        ),
        None,
    )
    marker = "#" * level + " "
    if heading is not None:
        content = source_text[heading.content_start:heading.content_end]
        original = source_text[heading.source_start:heading.source_end]
        newline = "\n" if original.endswith("\n") else ""
        replacement = marker + content + newline
        content_lo = heading.source_start + len(marker)
        content_hi = content_lo + len(content)
        target = _directed_view(before_view, content_lo, content_hi) if lo < hi else ViewState(content_hi, content_hi)
        return _plan_replace(
            action=f"markdown-heading-{level}", source_text=source_text,
            source_state_id=source_state_id, before_view=before_view,
            start=heading.source_start, end=heading.source_end,
            replacement=replacement, target_view=target,
        )

    raw = source_text[line_start:line_end]
    cleaned = _ATX_PREFIX_RE.sub("", raw, count=1).lstrip(" \t")
    replacement = marker + cleaned
    content_lo = line_start + len(marker)
    content_hi = content_lo + len(cleaned)
    target = _directed_view(before_view, content_lo, content_hi) if lo < hi else ViewState(content_hi, content_hi)
    return _plan_replace(
        action=f"markdown-heading-{level}", source_text=source_text,
        source_state_id=source_state_id, before_view=before_view,
        start=line_start, end=line_end, replacement=replacement, target_view=target,
    )


def _normalize_block_lines(chunk: str, action: str) -> str:
    lines = chunk.split("\n")
    result: list[str] = []
    item_number = 1
    for line in lines:
        if not line.strip():
            result.append(line)
            continue
        existing = _LINE_PREFIX_RE.match(line)
        if existing is not None:
            indent = existing.group(1)
            base = line[existing.end():]
        else:
            indent_match = re.match(r"^ {0,3}", line)
            indent = indent_match.group(0) if indent_match is not None else ""
            base = line[len(indent):]
        if action == "markdown-blockquote":
            prefix = "> "
        elif action == "markdown-unordered-list":
            prefix = "- "
        elif action == "markdown-ordered-list":
            prefix = f"{item_number}. "
            item_number += 1
        elif action == "markdown-task-list":
            prefix = "- [ ] "
        else:
            raise KeyError(action)
        result.append(indent + prefix + base)
    return "\n".join(result)


def _block_prefix(
    action: str, source_text: str, source_state_id: int, before_view: ViewState
) -> MarkdownEditPlan:
    start, end = _line_scope(source_text, before_view)
    original = source_text[start:end]
    replacement = _normalize_block_lines(original, action)
    delta = len(replacement) - len(original)
    lo, hi = _selection(before_view)
    if lo == hi:
        target_offset = max(start, min(before_view.insert_offset + delta, start + len(replacement)))
        target = ViewState(target_offset, target_offset)
    else:
        target = _directed_view(before_view, start, start + len(replacement))
    return _plan_replace(
        action=action, source_text=source_text, source_state_id=source_state_id,
        before_view=before_view, start=start, end=end, replacement=replacement,
        target_view=target,
    )


def _fenced_code(
    source_text: str, source_state_id: int, before_view: ViewState
) -> MarkdownEditPlan:
    lo, hi = _selection(before_view)
    selected = source_text[lo:hi]
    fence = "`" * max(3, _max_run(selected, "`") + 1)
    at = lo if lo != hi else before_view.insert_offset
    before_newline = "" if at == 0 or source_text[at - 1] == "\n" else "\n"
    after_offset = hi if lo != hi else at
    after_newline = "" if after_offset == len(source_text) or source_text[after_offset:after_offset + 1] == "\n" else "\n"
    if lo != hi:
        body = selected
        body_tail = "" if body.endswith("\n") else "\n"
        replacement = f"{before_newline}{fence}\n{body}{body_tail}{fence}{after_newline}"
        body_start = lo + len(before_newline) + len(fence) + 1
        target = _directed_view(before_view, body_start, body_start + len(body))
        return _plan_replace(
            action="markdown-fenced-code", source_text=source_text,
            source_state_id=source_state_id, before_view=before_view,
            start=lo, end=hi, replacement=replacement, target_view=target,
        )
    insertion = f"{before_newline}{fence}\n\n{fence}{after_newline}"
    final = source_text[:at] + insertion + source_text[at:]
    ops = (ReplayOperation(EditKind.INSERT, at, insertion),)
    caret = at + len(before_newline) + len(fence) + 1
    target = ViewState(caret, caret)
    _prevalidate(final, ops)
    return MarkdownEditPlan(
        source_state_id, source_text, final, ops, before_view, target,
        "markdown-fenced-code",
    )


def _insert_template(
    *, action: str, source_text: str, source_state_id: int, before_view: ViewState,
    prefix: str, suffix: str, empty_label: str,
) -> MarkdownEditPlan:
    lo, hi = _selection(before_view)
    if "\n" in source_text[lo:hi]:
        raise MarkdownCommandError(f"{action} does not accept a multiline selection")
    label = source_text[lo:hi] if lo != hi else empty_label
    replacement = prefix + label + suffix
    start, end = (lo, hi) if lo != hi else (before_view.insert_offset, before_view.insert_offset)
    target_lo = start + len(prefix)
    target_hi = target_lo + len(label)
    return _plan_replace(
        action=action, source_text=source_text, source_state_id=source_state_id,
        before_view=before_view, start=start, end=end, replacement=replacement,
        target_view=ViewState(target_hi, target_lo),
    )


def _insert_block(
    *, action: str, source_text: str, source_state_id: int, before_view: ViewState,
    block: str, caret_relative: int | None = None,
) -> MarkdownEditPlan:
    at = before_view.insert_offset
    before_newline = "" if at == 0 or source_text[at - 1] == "\n" else "\n"
    after_newline = "" if at == len(source_text) or source_text[at:at + 1] == "\n" else "\n"
    insertion = before_newline + block + after_newline
    final = source_text[:at] + insertion + source_text[at:]
    ops = (ReplayOperation(EditKind.INSERT, at, insertion),)
    relative = len(insertion) if caret_relative is None else len(before_newline) + caret_relative
    target = ViewState(at + relative, at + relative)
    _prevalidate(final, ops)
    return MarkdownEditPlan(source_state_id, source_text, final, ops, before_view, target, action)


def plan_markdown_command(
    action: str, *, source_text: str, source_state_id: int, before_view: ViewState
) -> MarkdownEditPlan:
    """Plan one Plus Markdown writing command without touching GTK or document state."""
    _validate(source_text, source_state_id, before_view)
    if action not in PLANNED_MARKDOWN_ACTIONS:
        raise MarkdownCommandError(f"unsupported planned Markdown action: {action}")

    if action.startswith("markdown-heading-"):
        return _heading(int(action.rsplit("-", 1)[1]), source_text, source_state_id, before_view)
    if action == "markdown-bold":
        return _inline_wrap(action, "**", source_text, source_state_id, before_view)
    if action == "markdown-italic":
        return _inline_wrap(action, "*", source_text, source_state_id, before_view)
    if action == "markdown-strikethrough":
        return _inline_wrap(action, "~~", source_text, source_state_id, before_view)
    if action == "markdown-inline-code":
        return _inline_code(source_text, source_state_id, before_view)
    if action in {
        "markdown-blockquote", "markdown-unordered-list", "markdown-ordered-list",
        "markdown-task-list",
    }:
        return _block_prefix(action, source_text, source_state_id, before_view)
    if action == "markdown-fenced-code":
        return _fenced_code(source_text, source_state_id, before_view)
    if action == "markdown-thematic-break":
        return _insert_block(
            action=action, source_text=source_text, source_state_id=source_state_id,
            before_view=before_view, block="---\n",
        )
    if action == "markdown-link":
        return _insert_template(
            action=action, source_text=source_text, source_state_id=source_state_id,
            before_view=before_view, prefix="[", suffix="](url)", empty_label="link text",
        )
    if action == "markdown-image":
        return _insert_template(
            action=action, source_text=source_text, source_state_id=source_state_id,
            before_view=before_view, prefix="![", suffix="](path)", empty_label="alt text",
        )
    if action == "markdown-table":
        block = "| Column 1 | Column 2 |\n| --- | --- |\n|  |  |\n"
        return _insert_block(
            action=action, source_text=source_text, source_state_id=source_state_id,
            before_view=before_view, block=block,
            caret_relative=len("| "),
        )
    raise AssertionError(action)
