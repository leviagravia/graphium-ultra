"""Single GTK-free Markdown structural authority for Graphium Plus.

The editable Graphium Gtk.TextBuffer remains the only mutable document authority.
This module derives immutable, disposable structural source maps from a captured
text snapshot. It performs no I/O, imports no GTK modules, and owns no presentation
or document state. Graphium Ultra presentation layers must consume this authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import re


_ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^ {0,3}(>)[ \t]?(.*)$")
_UL_RE = re.compile(r"^ {0,3}([-+*])[ \t]+(.*)$")
_OL_RE = re.compile(r"^ {0,3}(\d{1,9}[.)])[ \t]+(.*)$")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_THEMATIC_RE = re.compile(
    r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
_TRAILING_HASH_RE = re.compile(r"[ \t]+#+[ \t]*$")
_HEADING_ATTRIBUTE_RE = re.compile(r"(?:^|[ \t]+)\{([^{}\r\n]*)\}[ \t]*$")
_TABLE_DELIMITER_CELL_RE = re.compile(r"^:?-{3,}:?$")
_TABLE_MAX_COLUMNS = 32
_TABLE_MAX_ROWS = 512
_TABLE_MAX_CELL_CHARS = 8_192
_TABLE_MAX_SOURCE_CHARS = 262_144


class MarkdownBlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    FENCED_CODE = "fenced-code"
    BLOCKQUOTE = "blockquote"
    UNORDERED_LIST_ITEM = "unordered-list-item"
    ORDERED_LIST_ITEM = "ordered-list-item"
    THEMATIC_BREAK = "thematic-break"


class MarkdownInlineKind(str, Enum):
    EMPHASIS = "emphasis"
    STRONG = "strong"
    INLINE_CODE = "inline-code"
    LINK = "link"
    IMAGE = "image"


class MarkdownTableAlignment(str, Enum):
    DEFAULT = "default"
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class MarkdownDiagnostic:
    kind: str
    line: int
    offset: int
    message: str


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    kind: MarkdownBlockKind
    source_start: int
    source_end: int
    content_start: int
    content_end: int
    line_start: int
    line_end: int
    marker_start: int | None = None
    marker_end: int | None = None
    level: int | None = None
    section_end: int | None = None

    def __post_init__(self) -> None:
        if not (0 <= self.source_start <= self.content_start <= self.content_end <= self.source_end):
            raise ValueError("invalid Markdown block source range")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("invalid Markdown block line range")
        if (self.marker_start is None) != (self.marker_end is None):
            raise ValueError("marker range must be complete")
        if self.marker_start is not None and not (
            self.source_start <= self.marker_start <= self.marker_end <= self.source_end
        ):
            raise ValueError("marker range falls outside Markdown block")
        if self.level is not None and not 1 <= self.level <= 6:
            raise ValueError("heading level must be 1..6")
        if self.section_end is not None and self.section_end < self.source_end:
            raise ValueError("section end cannot precede heading end")


@dataclass(frozen=True, slots=True)
class MarkdownInlineSpan:
    kind: MarkdownInlineKind
    source_start: int
    source_end: int
    content_start: int
    content_end: int
    line: int
    target: str | None = None

    def __post_init__(self) -> None:
        if not (0 <= self.source_start <= self.content_start <= self.content_end <= self.source_end):
            raise ValueError("invalid Markdown inline range")
        if self.source_end <= self.source_start:
            raise ValueError("Markdown inline span must be non-empty")
        if self.line < 1:
            raise ValueError("Markdown inline line must be one-based")


@dataclass(frozen=True, slots=True)
class MarkdownHeadingAnchor:
    source_start: int
    source_end: int
    identifier: str
    explicit: bool = False

    def __post_init__(self) -> None:
        if not (0 <= self.source_start < self.source_end):
            raise ValueError("invalid Markdown heading-anchor source range")
        if not _valid_heading_identifier(self.identifier):
            raise ValueError("invalid Markdown heading-anchor identifier")


@dataclass(frozen=True, slots=True)
class MarkdownTableCell:
    content_start: int
    content_end: int
    line: int
    column: int

    def __post_init__(self) -> None:
        if not (0 <= self.content_start <= self.content_end):
            raise ValueError("invalid Markdown table cell range")
        if self.line < 1 or self.column < 1:
            raise ValueError("Markdown table cell position must be one-based")


@dataclass(frozen=True, slots=True)
class MarkdownTable:
    source_start: int
    source_end: int
    line_start: int
    line_end: int
    alignments: tuple[MarkdownTableAlignment, ...]
    header: tuple[MarkdownTableCell, ...]
    rows: tuple[tuple[MarkdownTableCell, ...], ...]

    def __post_init__(self) -> None:
        columns = len(self.header)
        if columns < 1 or len(self.alignments) != columns:
            raise ValueError("invalid Markdown table column count")
        if not (0 <= self.source_start < self.source_end):
            raise ValueError("invalid Markdown table source range")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("invalid Markdown table line range")
        if any(len(row) != columns for row in self.rows):
            raise ValueError("Markdown table rows must match header columns")


@dataclass(frozen=True, slots=True)
class MarkdownDocumentMap:
    text_length: int
    source_sha256: str
    blocks: tuple[MarkdownBlock, ...]
    inline_spans: tuple[MarkdownInlineSpan, ...]
    diagnostics: tuple[MarkdownDiagnostic, ...]
    tables: tuple[MarkdownTable, ...] = ()
    heading_anchors: tuple[MarkdownHeadingAnchor, ...] = ()

    @property
    def headings(self) -> tuple[MarkdownBlock, ...]:
        return tuple(block for block in self.blocks if block.kind is MarkdownBlockKind.HEADING)

    @property
    def links(self) -> tuple[MarkdownInlineSpan, ...]:
        return tuple(
            span for span in self.inline_spans
            if span.kind in (MarkdownInlineKind.LINK, MarkdownInlineKind.IMAGE)
        )

@dataclass(frozen=True, slots=True)
class _Line:
    number: int
    start: int
    end: int
    content_end: int
    logical: str


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(text: str) -> tuple[_Line, ...]:
    result: list[_Line] = []
    offset = 0
    for number, chunk in enumerate(text.splitlines(keepends=True), start=1):
        logical = chunk.rstrip("\r\n")
        result.append(_Line(number, offset, offset + len(chunk), offset + len(logical), logical))
        offset += len(chunk)
    if text and not result:
        result.append(_Line(1, 0, len(text), len(text), text))
    return tuple(result)


def _closing_fence(line: str, marker: str, minimum: int) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    run = 0
    for character in stripped:
        if character != marker:
            break
        run += 1
    return run >= minimum and stripped[run:].strip(" \t") == ""


def _atx_title_range(raw: str, absolute_start: int) -> tuple[int, int]:
    """Return the visible ATX heading title range; explicit IDs stay literal source."""
    title_end = len(raw)
    closing = _TRAILING_HASH_RE.search(raw)
    if closing:
        title_end = closing.start()
    while title_end > 0 and raw[title_end - 1] in " \t":
        title_end -= 1
    title_start = 0
    while title_start < title_end and raw[title_start] in " \t":
        title_start += 1
    return absolute_start + title_start, absolute_start + title_end


def _setext_candidate(logical: str) -> bool:
    """Setext may only promote an otherwise plain, non-blank source line."""
    if not logical.strip() or len(logical) - len(logical.lstrip(" ")) > 3:
        return False
    return not any((
        _ATX_RE.match(logical),
        _FENCE_RE.match(logical),
        _BLOCKQUOTE_RE.match(logical),
        _UL_RE.match(logical),
        _OL_RE.match(logical),
        _THEMATIC_RE.match(logical),
    ))


def _find_unescaped(text: str, needle: str, start: int, end: int) -> int:
    index = start
    while index < end:
        found = text.find(needle, index, end)
        if found < 0:
            return -1
        backslashes = 0
        probe = found - 1
        while probe >= start and text[probe] == "\\":
            backslashes += 1
            probe -= 1
        if backslashes % 2 == 0:
            return found
        index = found + len(needle)
    return -1


def _link_end(text: str, open_paren: int, end: int) -> int:
    depth = 1
    index = open_paren + 1
    while index < end:
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _scan_inline(text: str, start: int, end: int, line: int) -> tuple[MarkdownInlineSpan, ...]:
    spans: list[MarkdownInlineSpan] = []
    index = start
    while index < end:
        if text[index] == "\\":
            index += 2
            continue

        if text[index] == "`":
            run_end = index + 1
            while run_end < end and text[run_end] == "`":
                run_end += 1
            marker = text[index:run_end]
            close = text.find(marker, run_end, end)
            if close >= 0:
                spans.append(MarkdownInlineSpan(
                    MarkdownInlineKind.INLINE_CODE, index, close + len(marker),
                    run_end, close, line,
                ))
                index = close + len(marker)
                continue

        image = text.startswith("![", index)
        if image or text[index] == "[":
            label_open = index + 1 if image else index
            label_start = label_open + 1
            close_label = _find_unescaped(text, "]", label_start, end)
            if close_label >= label_start and close_label + 1 < end and text[close_label + 1] == "(":
                close_link = _link_end(text, close_label + 1, end)
                if close_link >= 0:
                    target = text[close_label + 2:close_link].strip()
                    spans.append(MarkdownInlineSpan(
                        MarkdownInlineKind.IMAGE if image else MarkdownInlineKind.LINK,
                        index, close_link + 1, label_start, close_label, line,
                        target=target,
                    ))
                    index = close_link + 1
                    continue

        strong_marker = None
        if text.startswith("**", index):
            strong_marker = "**"
        elif text.startswith("__", index):
            strong_marker = "__"
        if strong_marker is not None:
            close = _find_unescaped(text, strong_marker, index + 2, end)
            if close > index + 2:
                spans.append(MarkdownInlineSpan(
                    MarkdownInlineKind.STRONG, index, close + 2, index + 2, close, line,
                ))
                index = close + 2
                continue

        if text[index] in "*_":
            marker = text[index]
            close = _find_unescaped(text, marker, index + 1, end)
            if close > index + 1:
                spans.append(MarkdownInlineSpan(
                    MarkdownInlineKind.EMPHASIS, index, close + 1, index + 1, close, line,
                ))
                index = close + 1
                continue
        index += 1
    return tuple(spans)


def _valid_heading_identifier(identifier: str) -> bool:
    if not identifier or not (identifier[0].isalpha() or identifier[0] == "_"):
        return False
    return all(character.isalnum() or character in "_-." for character in identifier[1:])


def _heading_attribute_identifier(raw: str) -> tuple[int | None, str | None]:
    """Return trailing attribute start and one valid explicit heading ID, if any."""
    match = _HEADING_ATTRIBUTE_RE.search(raw.rstrip(" \t"))
    if match is None:
        return None, None
    content = match.group(1).strip()
    if not content or any(character in content for character in "\"'"):
        return None, None
    tokens = tuple(content.split())
    ids = tuple(token[1:] for token in tokens if token.startswith("#"))
    recognized = all(
        token.startswith("#")
        or (len(token) > 1 and token.startswith("."))
        or ("=" in token and bool(token.split("=", 1)[0]))
        for token in tokens
    )
    if not recognized:
        return None, None
    if len(ids) != 1 or not _valid_heading_identifier(ids[0]):
        return match.start(), None
    return match.start(), ids[0]


def _automatic_heading_identifier(value: str) -> str:
    filtered = "".join(
        character for character in value.lower()
        if character.isspace() or character.isalnum() or character in "_-."
    )
    candidate = re.sub(r"\s+", "-", filtered)
    first_letter = next((index for index, char in enumerate(candidate) if char.isalpha()), None)
    if first_letter is None:
        return "section"
    return candidate[first_letter:] or "section"


def _build_heading_anchors(
    text: str,
    blocks: list[MarkdownBlock],
    inline_spans: list[MarkdownInlineSpan],
) -> tuple[MarkdownHeadingAnchor, ...]:
    anchors: list[MarkdownHeadingAnchor] = []
    used: set[str] = set()
    span_index = 0
    for heading in (block for block in blocks if block.kind is MarkdownBlockKind.HEADING):
        raw = text[heading.content_start:heading.content_end]
        attribute_at, explicit_identifier = _heading_attribute_identifier(raw)
        content_end = heading.content_end if attribute_at is None else heading.content_start + attribute_at
        if explicit_identifier is not None:
            identifier = explicit_identifier
            explicit = True
        else:
            while span_index < len(inline_spans) and inline_spans[span_index].source_end <= heading.content_start:
                span_index += 1
            parts: list[str] = []
            cursor = heading.content_start
            probe = span_index
            while probe < len(inline_spans):
                span = inline_spans[probe]
                if span.source_start >= content_end:
                    break
                probe += 1
                if (
                    span.source_start < heading.content_start
                    or span.source_end > content_end
                    or span.source_start < cursor
                ):
                    continue
                parts.append(text[cursor:span.source_start])
                parts.append(text[span.content_start:span.content_end])
                cursor = span.source_end
            parts.append(text[cursor:content_end])
            span_index = probe
            base = _automatic_heading_identifier("".join(parts).strip())
            identifier = base
            suffix = 1
            while identifier in used:
                identifier = f"{base}-{suffix}"
                suffix += 1
            explicit = False
        used.add(identifier)
        anchors.append(MarkdownHeadingAnchor(
            heading.source_start, heading.source_end, identifier, explicit=explicit
        ))
    return tuple(anchors)


def _table_pipe_positions(logical: str) -> tuple[int, ...]:
    positions: list[int] = []
    code_run = 0
    index = 0
    while index < len(logical):
        character = logical[index]
        if character == "\\":
            index += 2
            continue
        if character == "`":
            run_end = index + 1
            while run_end < len(logical) and logical[run_end] == "`":
                run_end += 1
            run = run_end - index
            if code_run == 0:
                code_run = run
            elif code_run == run:
                code_run = 0
            index = run_end
            continue
        if character == "|" and code_run == 0:
            positions.append(index)
        index += 1
    return tuple(positions)


def _split_table_row(line: _Line) -> tuple[MarkdownTableCell, ...] | None:
    logical = line.logical
    indent = len(logical) - len(logical.lstrip(" "))
    if indent > 3:
        return None
    pipes = _table_pipe_positions(logical)
    if not pipes:
        return None

    content_left = indent
    content_right = len(logical.rstrip(" \t"))
    if content_right <= content_left:
        return None

    separators = list(pipes)
    if separators and separators[0] == content_left:
        content_left += 1
        separators.pop(0)
    if separators and separators[-1] == content_right - 1:
        content_right -= 1
        separators.pop()

    ranges: list[tuple[int, int]] = []
    cursor = content_left
    for separator in separators:
        if separator < cursor or separator > content_right:
            return None
        ranges.append((cursor, separator))
        cursor = separator + 1
    ranges.append((cursor, content_right))

    cells: list[MarkdownTableCell] = []
    for column, (left, right) in enumerate(ranges, start=1):
        if right < left:
            return None
        raw = logical[left:right]
        leading = len(raw) - len(raw.lstrip(" \t"))
        trailing_text = raw.rstrip(" \t")
        trailing = len(raw) - len(trailing_text)
        start = line.start + left + leading
        end = line.start + right - trailing
        if end < start:
            end = start
        cells.append(MarkdownTableCell(start, end, line.number, column))
    return tuple(cells) if cells else None


def _delimiter_alignment(token: str) -> MarkdownTableAlignment | None:
    stripped = token.strip(" \t")
    if _TABLE_DELIMITER_CELL_RE.fullmatch(stripped) is None:
        return None
    if stripped.startswith(":") and stripped.endswith(":"):
        return MarkdownTableAlignment.CENTER
    if stripped.startswith(":"):
        return MarkdownTableAlignment.LEFT
    if stripped.endswith(":"):
        return MarkdownTableAlignment.RIGHT
    return MarkdownTableAlignment.DEFAULT


def _scan_tables(
    text: str,
    lines: tuple[_Line, ...],
    blocks: list[MarkdownBlock],
    inline_spans: list[MarkdownInlineSpan],
) -> tuple[MarkdownTable, ...]:
    paragraph_lines = {
        block.line_start: block
        for block in blocks
        if block.kind is MarkdownBlockKind.PARAGRAPH and block.line_start == block.line_end
    }
    image_spans = tuple(
        span for span in inline_spans if span.kind is MarkdownInlineKind.IMAGE
    )
    tables: list[MarkdownTable] = []
    index = 0
    while index + 1 < len(lines):
        header_line = lines[index]
        delimiter_line = lines[index + 1]
        if header_line.number not in paragraph_lines or delimiter_line.number not in paragraph_lines:
            index += 1
            continue
        header = _split_table_row(header_line)
        delimiter = _split_table_row(delimiter_line)
        if header is None or delimiter is None or len(header) != len(delimiter):
            index += 1
            continue
        if len(header) > _TABLE_MAX_COLUMNS or any(
            cell.content_end - cell.content_start > _TABLE_MAX_CELL_CHARS
            for cell in header
        ):
            index += 1
            continue
        if any(
            cell.content_end - cell.content_start > _TABLE_MAX_CELL_CHARS
            for cell in delimiter
        ):
            index += 1
            continue
        alignments: list[MarkdownTableAlignment] = []
        for cell in delimiter:
            alignment = _delimiter_alignment(text[cell.content_start:cell.content_end])
            if alignment is None:
                break
            alignments.append(alignment)
        if len(alignments) != len(header):
            index += 1
            continue

        rows: list[tuple[MarkdownTableCell, ...]] = []
        probe = index + 2
        rejected = False
        while probe < len(lines):
            row_line = lines[probe]
            if row_line.number not in paragraph_lines:
                break
            row = _split_table_row(row_line)
            if row is None or len(row) != len(header):
                break
            if any(
                cell.content_end - cell.content_start > _TABLE_MAX_CELL_CHARS
                for cell in row
            ):
                rejected = True
                break
            if len(rows) + 2 > _TABLE_MAX_ROWS:
                rejected = True
                break
            rows.append(row)
            probe += 1

        last_line = lines[probe - 1] if rows else delimiter_line
        source_start = header_line.start
        source_end = last_line.end
        if source_end - source_start > _TABLE_MAX_SOURCE_CHARS:
            rejected = True
        if any(
            source_start <= span.source_start and span.source_end <= source_end
            for span in image_spans
        ):
            rejected = True
        if rejected:
            index += 1
            continue

        tables.append(MarkdownTable(
            source_start,
            source_end,
            header_line.number,
            last_line.number,
            tuple(alignments),
            header,
            tuple(rows),
        ))
        index = probe
    return tuple(tables)


def _set_heading_section_ends(blocks: list[MarkdownBlock], text_length: int) -> None:
    heading_positions = [i for i, block in enumerate(blocks) if block.kind is MarkdownBlockKind.HEADING]
    stack: list[int] = []
    for block_index in heading_positions:
        current = blocks[block_index]
        assert current.level is not None
        while stack and (blocks[stack[-1]].level or 0) >= current.level:
            finished = stack.pop()
            blocks[finished] = replace(blocks[finished], section_end=current.source_start)
        stack.append(block_index)
    while stack:
        finished = stack.pop()
        blocks[finished] = replace(blocks[finished], section_end=text_length)


def build_markdown_document_map(text: str) -> MarkdownDocumentMap:
    """Build the one immutable Markdown source map for a captured text snapshot."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    lines = _lines(text)
    blocks: list[MarkdownBlock] = []
    inline_spans: list[MarkdownInlineSpan] = []
    diagnostics: list[MarkdownDiagnostic] = []

    skip_until_line_index = -1

    fence_marker: str | None = None
    fence_minimum = 0
    fence_start: _Line | None = None
    fence_content_start = 0

    for line_index, line in enumerate(lines):
        if line_index <= skip_until_line_index:
            continue
        logical = line.logical

        if fence_marker is not None:
            if _closing_fence(logical, fence_marker, fence_minimum):
                assert fence_start is not None
                blocks.append(MarkdownBlock(
                    MarkdownBlockKind.FENCED_CODE,
                    fence_start.start, line.end, fence_content_start, line.start,
                    fence_start.number, line.number,
                    fence_start.start + len(fence_start.logical) - len(fence_start.logical.lstrip(" ")),
                    fence_start.start + len(fence_start.logical) - len(fence_start.logical.lstrip(" ")) + fence_minimum,
                ))
                fence_marker = None
                fence_minimum = 0
                fence_start = None
            continue

        fence = _FENCE_RE.match(logical)
        if fence:
            run = fence.group(1)
            fence_marker = run[0]
            fence_minimum = len(run)
            fence_start = line
            fence_content_start = line.end
            continue

        heading = _ATX_RE.match(logical)
        if heading:
            marker = heading.group(1)
            raw = heading.group(2) or ""
            raw_start = line.start + (heading.start(2) if heading.group(2) is not None else len(logical))
            title_start, title_end = _atx_title_range(raw, raw_start)
            marker_start = line.start + heading.start(1)
            blocks.append(MarkdownBlock(
                MarkdownBlockKind.HEADING, line.start, line.end,
                title_start, title_end, line.number, line.number,
                marker_start, marker_start + len(marker), level=len(marker),
            ))
            inline_spans.extend(_scan_inline(text, title_start, title_end, line.number))
            continue

        if _setext_candidate(logical) and line_index + 1 < len(lines):
            underline = lines[line_index + 1]
            setext = _SETEXT_RE.match(underline.logical)
            if setext:
                content_start = line.start + len(logical) - len(logical.lstrip(" \t"))
                content_end = line.content_end
                marker_start = underline.start + setext.start(1)
                marker_end = underline.start + setext.end(1)
                level = 1 if setext.group(1).startswith("=") else 2
                blocks.append(MarkdownBlock(
                    MarkdownBlockKind.HEADING, line.start, underline.end,
                    content_start, content_end, line.number, underline.number,
                    marker_start, marker_end, level=level,
                ))
                inline_spans.extend(_scan_inline(text, content_start, content_end, line.number))
                skip_until_line_index = line_index + 1
                continue

        if _THEMATIC_RE.match(logical):
            blocks.append(MarkdownBlock(
                MarkdownBlockKind.THEMATIC_BREAK, line.start, line.end,
                line.start, line.content_end, line.number, line.number,
                line.start, line.content_end,
            ))
            continue

        quote = _BLOCKQUOTE_RE.match(logical)
        if quote:
            marker_start = line.start + quote.start(1)
            content_start = line.start + quote.start(2)
            blocks.append(MarkdownBlock(
                MarkdownBlockKind.BLOCKQUOTE, line.start, line.end,
                content_start, line.content_end, line.number, line.number,
                marker_start, marker_start + 1,
            ))
            inline_spans.extend(_scan_inline(text, content_start, line.content_end, line.number))
            continue

        unordered = _UL_RE.match(logical)
        if unordered:
            marker_start = line.start + unordered.start(1)
            content_start = line.start + unordered.start(2)
            blocks.append(MarkdownBlock(
                MarkdownBlockKind.UNORDERED_LIST_ITEM, line.start, line.end,
                content_start, line.content_end, line.number, line.number,
                marker_start, marker_start + len(unordered.group(1)),
            ))
            inline_spans.extend(_scan_inline(text, content_start, line.content_end, line.number))
            continue

        ordered = _OL_RE.match(logical)
        if ordered:
            marker_start = line.start + ordered.start(1)
            content_start = line.start + ordered.start(2)
            blocks.append(MarkdownBlock(
                MarkdownBlockKind.ORDERED_LIST_ITEM, line.start, line.end,
                content_start, line.content_end, line.number, line.number,
                marker_start, marker_start + len(ordered.group(1)),
            ))
            inline_spans.extend(_scan_inline(text, content_start, line.content_end, line.number))
            continue

        if logical.strip():
            content_start = line.start + len(logical) - len(logical.lstrip(" \t"))
            content_end = line.content_end
            blocks.append(MarkdownBlock(
                MarkdownBlockKind.PARAGRAPH, line.start, line.end,
                content_start, content_end, line.number, line.number,
            ))
            inline_spans.extend(_scan_inline(text, content_start, content_end, line.number))

    if fence_marker is not None and fence_start is not None:
        marker_start = fence_start.start + len(fence_start.logical) - len(fence_start.logical.lstrip(" "))
        blocks.append(MarkdownBlock(
            MarkdownBlockKind.FENCED_CODE, fence_start.start, len(text),
            fence_content_start, len(text), fence_start.number, lines[-1].number,
            marker_start, marker_start + fence_minimum,
        ))
        diagnostics.append(MarkdownDiagnostic(
            "unterminated-fenced-code", fence_start.number, fence_start.start,
            "Fenced code block is not terminated before end of document.",
        ))

    _set_heading_section_ends(blocks, len(text))
    tables = _scan_tables(text, lines, blocks, inline_spans)
    heading_anchors = _build_heading_anchors(text, blocks, inline_spans)

    return MarkdownDocumentMap(
        len(text), _digest(text), tuple(blocks), tuple(inline_spans), tuple(diagnostics),
        tables, heading_anchors,
    )


