"""GTK-free scholarly-note parsing, diagnostics, editing and navigation for Graphium Plus.

The Markdown/Pandoc document remains the sole authority.  This module owns only immutable
positions derived from current text plus small prevalidated edit/navigation plans.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from graphium.application.renderability import ensure_interactive_text_renderable
from graphium.domain.edit_history import (
    DEFAULT_MAX_HISTORY_PAYLOAD_CHARS,
    EditKind,
    ReplayOperation,
    ViewState,
)


_DEFINITION_RE = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:[ \t]*")
_REFERENCE_RE = re.compile(r"\[\^([^\]\s]+)\]")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class ScholarlyNoteError(ValueError):
    """Rejected note syntax/edit request."""


@dataclass(frozen=True)
class TextRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("invalid text range")

    def contains(self, offset: int, *, include_end: bool = True) -> bool:
        if include_end:
            return self.start <= offset <= self.end
        return self.start <= offset < self.end


@dataclass(frozen=True)
class FootnoteReference:
    label: str
    marker: TextRange


@dataclass(frozen=True)
class FootnoteDefinition:
    label: str
    marker: TextRange
    block: TextRange
    content_start: int


@dataclass(frozen=True)
class InlineNote:
    whole: TextRange
    content: TextRange


@dataclass(frozen=True)
class NoteDiagnostic:
    kind: str
    label: str
    offset: int
    message: str


@dataclass(frozen=True)
class ScholarlyNotesMap:
    source_length: int
    source_sha256: str
    references: tuple[FootnoteReference, ...]
    definitions: tuple[FootnoteDefinition, ...]
    inline_notes: tuple[InlineNote, ...]
    diagnostics: tuple[NoteDiagnostic, ...]

    @classmethod
    def from_text(cls, text: str) -> "ScholarlyNotesMap":
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        references: list[FootnoteReference] = []
        definitions: list[FootnoteDefinition] = []
        inline_notes: list[InlineNote] = []
        lines = _lines(text)
        opaque = _fenced_line_indexes(lines)
        definition_reference_tokens: set[tuple[int, int]] = set()

        index = 0
        while index < len(lines):
            line = lines[index]
            if index in opaque:
                index += 1
                continue
            match = _DEFINITION_RE.match(line.content)
            if match is None:
                index += 1
                continue
            label = match.group(1)
            marker_start = line.start + match.start()
            marker_end = line.start + match.end()
            token_start = line.start + line.content.index("[^", match.start(), match.end())
            token_end = token_start + len(label) + 3
            definition_reference_tokens.add((token_start, token_end))
            block_end_index = _definition_block_end(lines, opaque, index)
            block_end = lines[block_end_index].full_end
            definitions.append(
                FootnoteDefinition(
                    label=label,
                    marker=TextRange(marker_start, marker_end),
                    block=TextRange(line.start, block_end),
                    content_start=marker_end,
                )
            )
            index += 1

        for line_index, line in enumerate(lines):
            if line_index in opaque:
                continue
            code_ranges = _inline_code_ranges(line.content)
            inline_note_ranges = _scan_inline_notes(line.content, code_ranges)
            for start, end, content_start, content_end in inline_note_ranges:
                inline_notes.append(
                    InlineNote(
                        whole=TextRange(line.start + start, line.start + end),
                        content=TextRange(line.start + content_start, line.start + content_end),
                    )
                )
            for match in _REFERENCE_RE.finditer(line.content):
                absolute = (line.start + match.start(), line.start + match.end())
                if _is_escaped(line.content, match.start()):
                    continue
                if _range_overlaps_any(match.start(), match.end(), code_ranges):
                    continue
                if absolute in definition_reference_tokens:
                    continue
                references.append(
                    FootnoteReference(match.group(1), TextRange(*absolute))
                )

        diagnostics = _diagnostics(references, definitions)
        return cls(
            source_length=len(text),
            source_sha256=_sha256(text),
            references=tuple(references),
            definitions=tuple(definitions),
            inline_notes=tuple(inline_notes),
            diagnostics=diagnostics,
        )

    def matches_text(self, text: str) -> bool:
        return len(text) == self.source_length and _sha256(text) == self.source_sha256

    def definitions_for(self, label: str) -> tuple[FootnoteDefinition, ...]:
        return tuple(item for item in self.definitions if item.label == label)

    def references_for(self, label: str) -> tuple[FootnoteReference, ...]:
        return tuple(item for item in self.references if item.label == label)

    @property
    def labels(self) -> frozenset[str]:
        return frozenset(
            [item.label for item in self.references] + [item.label for item in self.definitions]
        )


@dataclass(frozen=True)
class ScholarlyNoteEditPlan:
    source_state_id: int
    source_text: str
    final_text: str
    operations: tuple[ReplayOperation, ...]
    before_view: ViewState
    target_view: ViewState
    label: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.operations)


@dataclass(frozen=True)
class NoteNavigationPlan:
    source_length: int
    source_sha256: str
    target_view: ViewState | None
    label: str | None
    direction: str | None

    def matches_text(self, text: str) -> bool:
        return len(text) == self.source_length and _sha256(text) == self.source_sha256


@dataclass(frozen=True)
class _Line:
    start: int
    content: str
    full_end: int



def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(text: str) -> tuple[_Line, ...]:
    if text == "":
        return (_Line(0, "", 0),)
    result: list[_Line] = []
    start = 0
    for raw in text.splitlines(keepends=True):
        content = raw[:-1] if raw.endswith("\n") else raw
        if content.endswith("\r"):
            content = content[:-1]
        full_end = start + len(raw)
        result.append(_Line(start, content, full_end))
        start = full_end
    if start < len(text):
        result.append(_Line(start, text[start:], len(text)))
    return tuple(result)


def _fenced_line_indexes(lines: tuple[_Line, ...]) -> frozenset[int]:
    opaque: set[int] = set()
    fence_char = ""
    fence_len = 0
    for index, line in enumerate(lines):
        match = _FENCE_RE.match(line.content)
        if not fence_char:
            if match is None:
                continue
            marker = match.group(1)
            fence_char = marker[0]
            fence_len = len(marker)
            opaque.add(index)
            continue
        opaque.add(index)
        stripped = line.content.lstrip(" ")
        indent = len(line.content) - len(stripped)
        if indent <= 3 and stripped.startswith(fence_char * fence_len):
            run = len(stripped) - len(stripped.lstrip(fence_char))
            tail = stripped[run:]
            if run >= fence_len and not tail.strip():
                fence_char = ""
                fence_len = 0
    return frozenset(opaque)


def _definition_block_end(
    lines: tuple[_Line, ...], opaque: frozenset[int], definition_index: int
) -> int:
    last = definition_index
    index = definition_index + 1
    while index < len(lines):
        if index in opaque:
            break
        content = lines[index].content
        if content.startswith("\t") or content.startswith("    "):
            last = index
            index += 1
            continue
        if content.strip() == "":
            lookahead = index + 1
            while lookahead < len(lines) and lines[lookahead].content.strip() == "":
                lookahead += 1
            if (
                lookahead < len(lines)
                and lookahead not in opaque
                and (
                    lines[lookahead].content.startswith("\t")
                    or lines[lookahead].content.startswith("    ")
                )
            ):
                last = lookahead - 1
                index = lookahead
                continue
        break
    return last


def _inline_code_ranges(line: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    index = 0
    n = len(line)
    while index < n:
        if line[index] != "`" or _is_escaped(line, index):
            index += 1
            continue
        run = 1
        while index + run < n and line[index + run] == "`":
            run += 1
        close = line.find("`" * run, index + run)
        if close < 0:
            index += run
            continue
        ranges.append((index, close + run))
        index = close + run
    return tuple(ranges)


def _scan_inline_notes(
    line: str, code_ranges: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int, int, int], ...]:
    result: list[tuple[int, int, int, int]] = []
    index = 0
    while index + 1 < len(line):
        if line[index:index + 2] != "^[" or _is_escaped(line, index):
            index += 1
            continue
        if _range_overlaps_any(index, index + 2, code_ranges):
            index += 2
            continue
        depth = 1
        cursor = index + 2
        while cursor < len(line):
            char = line[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    result.append((index, cursor + 1, index + 2, cursor))
                    index = cursor + 1
                    break
            cursor += 1
        else:
            index += 2
    return tuple(result)


def _is_escaped(text: str, offset: int) -> bool:
    slashes = 0
    index = offset - 1
    while index >= 0 and text[index] == "\\":
        slashes += 1
        index -= 1
    return bool(slashes % 2)


def _range_overlaps_any(start: int, end: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start < hi and end > lo for lo, hi in ranges)


def _diagnostics(
    references: list[FootnoteReference], definitions: list[FootnoteDefinition]
) -> tuple[NoteDiagnostic, ...]:
    by_label: dict[str, list[FootnoteDefinition]] = {}
    for definition in definitions:
        by_label.setdefault(definition.label, []).append(definition)
    result: list[NoteDiagnostic] = []
    for label, items in by_label.items():
        for duplicate in items[1:]:
            result.append(
                NoteDiagnostic(
                    "duplicate_definition",
                    label,
                    duplicate.marker.start,
                    f"Footnote [^{label}] has more than one definition.",
                )
            )
    defined = set(by_label)
    for reference in references:
        if reference.label not in defined:
            result.append(
                NoteDiagnostic(
                    "unresolved_reference",
                    reference.label,
                    reference.marker.start,
                    f"Footnote reference [^{reference.label}] has no definition.",
                )
            )
    result.sort(key=lambda item: (item.offset, item.kind, item.label))
    return tuple(result)


def _validate_edit_inputs(source_text: str, source_state_id: int, before_view: ViewState) -> None:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    if int(source_state_id) <= 0:
        raise ScholarlyNoteError("source_state_id must be positive")
    if not isinstance(before_view, ViewState):
        raise TypeError("before_view must be ViewState")
    if max(before_view.insert_offset, before_view.selection_bound_offset) > len(source_text):
        raise ScholarlyNoteError("view offset exceeds source text length")


def _validate_plan(final_text: str, operations: tuple[ReplayOperation, ...]) -> None:
    payload = sum(len(operation.text) for operation in operations)
    if payload > DEFAULT_MAX_HISTORY_PAYLOAD_CHARS:
        raise ScholarlyNoteError(
            "scholarly-note edit exceeds Graphium's bounded Undo payload budget"
        )
    ensure_interactive_text_renderable(final_text)


def _next_numeric_label(note_map: ScholarlyNotesMap) -> str:
    used = note_map.labels
    number = 1
    while str(number) in used:
        number += 1
    return str(number)


def plan_insert_footnote(
    *, source_text: str, source_state_id: int, before_view: ViewState
) -> ScholarlyNoteEditPlan:
    _validate_edit_inputs(source_text, source_state_id, before_view)
    note_map = ScholarlyNotesMap.from_text(source_text)
    label = _next_numeric_label(note_map)
    marker = f"[^{label}]"
    insertion = max(before_view.insert_offset, before_view.selection_bound_offset)
    with_marker = source_text[:insertion] + marker + source_text[insertion:]
    if with_marker.endswith("\n\n"):
        separator = ""
    elif with_marker.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    suffix = f"{separator}[^{label}]: "
    final_text = with_marker + suffix
    operations = (
        ReplayOperation(EditKind.INSERT, insertion, marker),
        ReplayOperation(EditKind.INSERT, len(source_text) + len(marker), suffix),
    )
    target = ViewState(len(final_text), len(final_text))
    _validate_plan(final_text, operations)
    return ScholarlyNoteEditPlan(
        source_state_id, source_text, final_text, operations, before_view, target, label,
    )


def plan_insert_inline_note(
    *, source_text: str, source_state_id: int, before_view: ViewState
) -> ScholarlyNoteEditPlan:
    _validate_edit_inputs(source_text, source_state_id, before_view)
    lo = min(before_view.insert_offset, before_view.selection_bound_offset)
    hi = max(before_view.insert_offset, before_view.selection_bound_offset)
    selected = source_text[lo:hi]
    if "\n" in selected or "\r" in selected:
        raise ScholarlyNoteError("inline notes require a single-line selection")
    replacement = f"^[{selected}]"
    operations_list: list[ReplayOperation] = []
    if selected:
        operations_list.append(ReplayOperation(EditKind.DELETE, lo, selected))
    operations_list.append(ReplayOperation(EditKind.INSERT, lo, replacement))
    operations = tuple(operations_list)
    final_text = source_text[:lo] + replacement + source_text[hi:]
    if selected:
        content_lo = lo + 2
        content_hi = content_lo + len(selected)
        if before_view.insert_offset >= before_view.selection_bound_offset:
            target = ViewState(content_hi, content_lo)
        else:
            target = ViewState(content_lo, content_hi)
    else:
        target = ViewState(lo + 2, lo + 2)
    _validate_plan(final_text, operations)
    return ScholarlyNoteEditPlan(
        source_state_id, source_text, final_text, operations, before_view, target, None,
    )


def plan_note_navigation(*, source_text: str, before_view: ViewState) -> NoteNavigationPlan:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    if not isinstance(before_view, ViewState):
        raise TypeError("before_view must be ViewState")
    caret = before_view.insert_offset
    if caret > len(source_text):
        raise ScholarlyNoteError("view offset exceeds source text length")
    note_map = ScholarlyNotesMap.from_text(source_text)
    for reference in note_map.references:
        if reference.marker.contains(caret):
            definitions = note_map.definitions_for(reference.label)
            if definitions:
                target = definitions[0].content_start
                return NoteNavigationPlan(
                    len(source_text), note_map.source_sha256, ViewState(target, target),
                    reference.label, "reference_to_definition",
                )
            break
    for definition in note_map.definitions:
        if definition.block.contains(caret):
            references = note_map.references_for(definition.label)
            if references:
                target = references[0].marker.start
                return NoteNavigationPlan(
                    len(source_text), note_map.source_sha256, ViewState(target, target),
                    definition.label, "definition_to_reference",
                )
            break
    return NoteNavigationPlan(len(source_text), note_map.source_sha256, None, None, None)


def diagnostics_text(note_map: ScholarlyNotesMap) -> str:
    if not isinstance(note_map, ScholarlyNotesMap):
        raise TypeError("note_map must be ScholarlyNotesMap")
    if not note_map.diagnostics:
        return (
            f"Footnotes: {len(note_map.definitions)} definitions, "
            f"{len(note_map.references)} references, "
            f"{len(note_map.inline_notes)} inline notes.\n\nNo duplicate or unresolved footnotes found."
        )
    lines = [
        f"Footnotes: {len(note_map.definitions)} definitions, "
        f"{len(note_map.references)} references, "
        f"{len(note_map.inline_notes)} inline notes.",
        "",
    ]
    for diagnostic in note_map.diagnostics:
        lines.append(f"• {diagnostic.message} (offset {diagnostic.offset})")
    return "\n".join(lines)
