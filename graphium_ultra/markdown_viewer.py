"""Pure native-viewer projection derived from the shared MarkdownDocumentMap.

The editable Gtk.TextBuffer remains the only mutable document authority.  This
module owns only a disposable rendered-text plan with output-range styles.  It
performs no GTK or filesystem I/O and never reparses Markdown independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from urllib.parse import unquote, urlsplit

from graphium.domain.text_search import SearchScaleError, find_all, validate_query
from graphium_plus.markdown import (
    MarkdownBlockKind,
    MarkdownDocumentMap,
    MarkdownInlineKind,
    MarkdownTableAlignment,
    MarkdownTableCell,
)


class MarkdownViewerLinkActionKind(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class MarkdownViewerSpanKind(str, Enum):
    HEADING = "heading"
    EMPHASIS = "emphasis"
    STRONG = "strong"
    INLINE_CODE = "inline-code"
    CODE_BLOCK = "code-block"
    BLOCKQUOTE = "blockquote"
    LIST_ITEM = "list-item"
    LIST_MARKER = "list-marker"
    LINK_LABEL = "link-label"
    IMAGE_PLACEHOLDER = "image-placeholder"
    TABLE_PLACEHOLDER = "table-placeholder"
    THEMATIC_BREAK = "thematic-break"


@dataclass(frozen=True, slots=True)
class MarkdownViewerSpan:
    kind: MarkdownViewerSpanKind
    start: int
    end: int
    level: int | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if not (0 <= self.start <= self.end):
            raise ValueError("invalid Markdown viewer span range")
        if self.level is not None and not 1 <= self.level <= 6:
            raise ValueError("viewer heading level must be 1..6")


@dataclass(frozen=True, slots=True)
class MarkdownViewerHeadingAnchor:
    identifier: str
    offset: int
    explicit: bool = False

    def __post_init__(self) -> None:
        if not self.identifier or self.offset < 0:
            raise ValueError("invalid Markdown viewer heading anchor")


@dataclass(frozen=True, slots=True)
class MarkdownViewerLinkAction:
    kind: MarkdownViewerLinkActionKind
    target: str
    offset: int | None = None

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("Markdown viewer link target cannot be empty")
        if self.kind is MarkdownViewerLinkActionKind.INTERNAL and self.offset is None:
            raise ValueError("internal Markdown viewer link requires an offset")
        if self.kind is MarkdownViewerLinkActionKind.EXTERNAL and self.offset is not None:
            raise ValueError("external Markdown viewer link cannot carry a viewer offset")


@dataclass(frozen=True, slots=True)
class MarkdownViewerReadingPosition:
    """Transient semantic reading position for the disposable Viewer projection."""

    anchor_identifier: str | None
    section_fraction: float
    global_fraction: float

    def __post_init__(self) -> None:
        if self.anchor_identifier is not None and not self.anchor_identifier:
            raise ValueError("reading-position anchor cannot be empty")
        for value in (self.section_fraction, self.global_fraction):
            if not 0.0 <= value <= 1.0:
                raise ValueError("reading-position fractions must be within 0..1")


def _clamped_fraction(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, numerator / denominator))


def capture_markdown_viewer_reading_position(
    plan: MarkdownViewerPlan, offset: int
) -> MarkdownViewerReadingPosition:
    """Capture one semantic Viewer position without GTK or persistent state."""
    offset = min(len(plan.text), max(0, int(offset)))
    global_fraction = _clamped_fraction(offset, len(plan.text))
    anchor_index = None
    for index, anchor in enumerate(plan.heading_anchors):
        if anchor.offset > offset:
            break
        anchor_index = index
    if anchor_index is None:
        return MarkdownViewerReadingPosition(None, 0.0, global_fraction)

    anchor = plan.heading_anchors[anchor_index]
    if sum(item.identifier == anchor.identifier for item in plan.heading_anchors) != 1:
        return MarkdownViewerReadingPosition(None, 0.0, global_fraction)
    section_end = (
        plan.heading_anchors[anchor_index + 1].offset
        if anchor_index + 1 < len(plan.heading_anchors)
        else len(plan.text)
    )
    section_fraction = _clamped_fraction(offset - anchor.offset, section_end - anchor.offset)
    return MarkdownViewerReadingPosition(anchor.identifier, section_fraction, global_fraction)


def resolve_markdown_viewer_reading_position(
    plan: MarkdownViewerPlan, position: MarkdownViewerReadingPosition
) -> int:
    """Resolve a captured position into the current Viewer plan."""
    identifier = position.anchor_identifier
    if identifier is not None:
        matches = [
            index for index, anchor in enumerate(plan.heading_anchors)
            if anchor.identifier == identifier
        ]
        if len(matches) == 1:
            index = matches[0]
            start = plan.heading_anchors[index].offset
            end = (
                plan.heading_anchors[index + 1].offset
                if index + 1 < len(plan.heading_anchors)
                else len(plan.text)
            )
            return min(len(plan.text), max(start, round(start + (end - start) * position.section_fraction)))
    return min(len(plan.text), max(0, round(len(plan.text) * position.global_fraction)))


@dataclass(frozen=True, slots=True)
class MarkdownViewerSearchHit:
    """One literal-search match inside one visual Viewer text segment."""

    segment_index: int
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.segment_index < 0 or not (0 <= self.start < self.end):
            raise ValueError("invalid Markdown viewer search hit")


def find_markdown_viewer_search_hits(
    segments: tuple[str, ...],
    query: str,
    *,
    match_case: bool = False,
    max_matches: int = 2000,
) -> tuple[MarkdownViewerSearchHit, ...]:
    """Search already ordered Viewer text segments without crossing segment boundaries."""
    if not isinstance(segments, tuple) or any(not isinstance(text, str) for text in segments):
        raise TypeError("Viewer search segments must be a tuple of strings")
    query = validate_query(query)
    cap = int(max_matches)
    if cap <= 0:
        raise ValueError("max_matches must be positive")
    hits: list[MarkdownViewerSearchHit] = []
    for segment_index, text in enumerate(segments):
        remaining = cap - len(hits)
        try:
            local = find_all(
                text, query, match_case=match_case, max_matches=max(1, remaining + 1)
            )
        except SearchScaleError as exc:
            raise SearchScaleError(
                f"Viewer search match count exceeds bounded budget ({cap})"
            ) from exc
        if len(local) > remaining:
            raise SearchScaleError(
                f"Viewer search match count exceeds bounded budget ({cap})"
            )
        hits.extend(
            MarkdownViewerSearchHit(segment_index, match.start, match.end)
            for match in local
        )
    return tuple(hits)


@dataclass(frozen=True, slots=True)
class MarkdownViewerTableCell:
    text: str
    spans: tuple[MarkdownViewerSpan, ...]
    alignment: MarkdownTableAlignment
    header: bool = False

    def __post_init__(self) -> None:
        for span in self.spans:
            if not (0 <= span.start <= span.end <= len(self.text)):
                raise ValueError("table-cell span falls outside rendered text")
            if span.kind in {
                MarkdownViewerSpanKind.HEADING,
                MarkdownViewerSpanKind.CODE_BLOCK,
                MarkdownViewerSpanKind.BLOCKQUOTE,
                MarkdownViewerSpanKind.LIST_ITEM,
                MarkdownViewerSpanKind.LIST_MARKER,
                MarkdownViewerSpanKind.TABLE_PLACEHOLDER,
                MarkdownViewerSpanKind.THEMATIC_BREAK,
            }:
                raise ValueError("invalid block-level span inside table cell")


@dataclass(frozen=True, slots=True)
class MarkdownViewerTable:
    start: int
    end: int
    header: tuple[MarkdownViewerTableCell, ...]
    rows: tuple[tuple[MarkdownViewerTableCell, ...], ...]

    def __post_init__(self) -> None:
        columns = len(self.header)
        if columns < 1 or not (0 <= self.start < self.end):
            raise ValueError("invalid viewer table")
        if any(len(row) != columns for row in self.rows):
            raise ValueError("viewer table row width mismatch")


@dataclass(frozen=True, slots=True)
class MarkdownViewerPlan:
    text: str
    spans: tuple[MarkdownViewerSpan, ...]
    source_text_length: int
    source_sha256: str
    source_state_id: int | None = None
    tables: tuple[MarkdownViewerTable, ...] = ()
    heading_anchors: tuple[MarkdownViewerHeadingAnchor, ...] = ()

    def matches_source(self, text: str, state_id: int | None = None) -> bool:
        if len(text) != self.source_text_length:
            return False
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != self.source_sha256:
            return False
        return self.source_state_id is None or state_id is None or state_id == self.source_state_id


_MAX_LINK_TARGET_CHARS = 4096
_ALLOWED_EXTERNAL_LINK_SCHEMES = frozenset({"http", "https"})


def resolve_markdown_viewer_link(
    plan: MarkdownViewerPlan, target: str | None
) -> MarkdownViewerLinkAction | None:
    """Resolve one already-parsed link target without I/O or Markdown rescanning."""
    if not isinstance(target, str) or not target or len(target) > _MAX_LINK_TARGET_CHARS:
        return None
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in target
    ):
        return None
    if target.startswith("#"):
        identifier = unquote(target[1:])
        if not identifier:
            return None
        matches = tuple(anchor for anchor in plan.heading_anchors if anchor.identifier == identifier)
        if len(matches) != 1:
            return None
        return MarkdownViewerLinkAction(
            MarkdownViewerLinkActionKind.INTERNAL, target, offset=matches[0].offset
        )
    if target.startswith("//"):
        return None
    try:
        parsed = urlsplit(target)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme.casefold() not in _ALLOWED_EXTERNAL_LINK_SCHEMES or not host:
        return None
    return MarkdownViewerLinkAction(MarkdownViewerLinkActionKind.EXTERNAL, target)


class _Builder:
    __slots__ = ("parts", "length", "spans")

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.spans: list[MarkdownViewerSpan] = []

    def append(
        self,
        text: str,
        kind: MarkdownViewerSpanKind | None = None,
        *,
        level: int | None = None,
        target: str | None = None,
    ) -> tuple[int, int]:
        start = self.length
        if text:
            self.parts.append(text)
            self.length += len(text)
        end = self.length
        if kind is not None and end > start:
            self.spans.append(MarkdownViewerSpan(kind, start, end, level=level, target=target))
        return start, end

    def mark(
        self,
        start: int,
        end: int,
        kind: MarkdownViewerSpanKind,
        *,
        level: int | None = None,
        target: str | None = None,
    ) -> None:
        if end > start:
            self.spans.append(MarkdownViewerSpan(kind, start, end, level=level, target=target))

    def value(self) -> str:
        return "".join(self.parts)


_INLINE_KIND = {
    MarkdownInlineKind.EMPHASIS: MarkdownViewerSpanKind.EMPHASIS,
    MarkdownInlineKind.STRONG: MarkdownViewerSpanKind.STRONG,
    MarkdownInlineKind.INLINE_CODE: MarkdownViewerSpanKind.INLINE_CODE,
    MarkdownInlineKind.LINK: MarkdownViewerSpanKind.LINK_LABEL,
}


class _InlineCursor:
    __slots__ = ("spans", "index")

    def __init__(self, document_map: MarkdownDocumentMap) -> None:
        self.spans = document_map.inline_spans
        self.index = 0

    def render(self, builder: _Builder, text: str, start: int, end: int) -> None:
        spans = self.spans
        index = self.index
        while index < len(spans) and spans[index].source_end <= start:
            index += 1
        cursor = start
        while index < len(spans):
            span = spans[index]
            if span.source_start >= end:
                break
            index += 1
            if span.source_start < start or span.source_end > end or span.source_start < cursor:
                continue
            builder.append(text[cursor:span.source_start])
            content = text[span.content_start:span.content_end]
            if span.kind is MarkdownInlineKind.IMAGE:
                builder.append(
                    f"[Image: {content}]",
                    MarkdownViewerSpanKind.IMAGE_PLACEHOLDER,
                    target=span.target,
                )
            else:
                builder.append(content, _INLINE_KIND[span.kind], target=span.target)
            cursor = span.source_end
        builder.append(text[cursor:end])
        self.index = index


def _render_table_cell(
    inline_cursor: _InlineCursor,
    text: str,
    cell: MarkdownTableCell,
    alignment: MarkdownTableAlignment,
    *,
    header: bool,
) -> MarkdownViewerTableCell:
    builder = _Builder()
    inline_cursor.render(builder, text, cell.content_start, cell.content_end)
    return MarkdownViewerTableCell(
        builder.value(), tuple(builder.spans), alignment, header=header
    )


def _append_separator(builder: _Builder, source_gap: str) -> None:
    if builder.length == 0:
        return
    # One output newline separates adjacent source blocks; a source blank line
    # remains a blank line in the disposable viewer projection.
    if source_gap.count("\n") >= 1:
        builder.append("\n\n")
    else:
        builder.append("\n")


def build_markdown_viewer_plan(
    text: str,
    document_map: MarkdownDocumentMap,
    *,
    source_state_id: int | None = None,
) -> MarkdownViewerPlan:
    """Build a read-only rendered-text plan from the one Markdown authority."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if len(text) != document_map.text_length or digest != document_map.source_sha256:
        raise ValueError("text does not match MarkdownDocumentMap source identity")

    builder = _Builder()
    inline_cursor = _InlineCursor(document_map)
    tables_by_start = {table.source_start: table for table in document_map.tables}
    anchors_by_start = {anchor.source_start: anchor for anchor in document_map.heading_anchors}
    viewer_tables: list[MarkdownViewerTable] = []
    viewer_anchors: list[MarkdownViewerHeadingAnchor] = []
    previous_end = 0
    skip_until = 0
    first = True

    for block in document_map.blocks:
        if block.source_start < skip_until:
            continue
        if not first:
            _append_separator(builder, text[previous_end:block.source_start])
        first = False

        table = tables_by_start.get(block.source_start)
        if table is not None:
            header = tuple(
                _render_table_cell(
                    inline_cursor, text, cell, table.alignments[index], header=True
                )
                for index, cell in enumerate(table.header)
            )
            rows = tuple(
                tuple(
                    _render_table_cell(
                        inline_cursor, text, cell, table.alignments[index], header=False
                    )
                    for index, cell in enumerate(row)
                )
                for row in table.rows
            )
            start, end = builder.append(
                "[Table]", MarkdownViewerSpanKind.TABLE_PLACEHOLDER
            )
            viewer_tables.append(MarkdownViewerTable(start, end, header, rows))
            previous_end = table.source_end
            skip_until = table.source_end
            continue

        previous_end = block.source_end

        if block.kind is MarkdownBlockKind.HEADING:
            start = builder.length
            inline_cursor.render(builder, text, block.content_start, block.content_end)
            builder.mark(start, builder.length, MarkdownViewerSpanKind.HEADING, level=block.level)
            anchor = anchors_by_start.get(block.source_start)
            if anchor is not None:
                viewer_anchors.append(MarkdownViewerHeadingAnchor(
                    anchor.identifier, start, explicit=anchor.explicit
                ))
            continue

        if block.kind is MarkdownBlockKind.PARAGRAPH:
            inline_cursor.render(builder, text, block.content_start, block.content_end)
            continue

        if block.kind is MarkdownBlockKind.BLOCKQUOTE:
            start = builder.length
            inline_cursor.render(builder, text, block.content_start, block.content_end)
            builder.mark(start, builder.length, MarkdownViewerSpanKind.BLOCKQUOTE)
            continue

        if block.kind in (
            MarkdownBlockKind.UNORDERED_LIST_ITEM,
            MarkdownBlockKind.ORDERED_LIST_ITEM,
        ):
            line_start = builder.length
            marker = "•" if block.kind is MarkdownBlockKind.UNORDERED_LIST_ITEM else text[
                block.marker_start:block.marker_end
            ]
            builder.append(marker, MarkdownViewerSpanKind.LIST_MARKER)
            builder.append(" ")
            inline_cursor.render(builder, text, block.content_start, block.content_end)
            builder.mark(line_start, builder.length, MarkdownViewerSpanKind.LIST_ITEM)
            continue

        if block.kind is MarkdownBlockKind.FENCED_CODE:
            code = text[block.content_start:block.content_end]
            if code.endswith("\n"):
                code = code[:-1]
            builder.append(code, MarkdownViewerSpanKind.CODE_BLOCK)
            continue

        if block.kind is MarkdownBlockKind.THEMATIC_BREAK:
            builder.append("────────────", MarkdownViewerSpanKind.THEMATIC_BREAK)
            continue

        raise AssertionError(f"unsupported Markdown block kind: {block.kind!r}")

    rendered = builder.value()
    return MarkdownViewerPlan(
        rendered,
        tuple(builder.spans),
        len(text),
        digest,
        source_state_id=source_state_id,
        tables=tuple(viewer_tables),
        heading_anchors=tuple(viewer_anchors),
    )

