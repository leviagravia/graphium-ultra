"""GTK-free deterministic academic review for Graphium Plus.

This module aggregates existing Markdown, scholarly-note, citation and reference
library diagnostics and adds a deliberately small writing-hygiene layer.  It
never mutates document/reference data, performs no I/O and creates no second
Markdown or bibliography authority.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import re
from graphium_plus.academic_notes import ScholarlyNotesMap
from graphium_plus.citations import CitationDocumentMap, citation_diagnostics
from graphium_plus.markdown import (
    MarkdownBlockKind,
    MarkdownInlineKind,
    build_markdown_document_map,
)
from graphium_plus.references import (
    ReferenceFileToken,
    ReferenceLibrarySnapshot,
)


_INTERNAL_SPACES_RE = re.compile(r"(?<=\S) {2,}(?=\S)")
_TRAILING_HSPACE_RE = re.compile(r"[ \t]+$")


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    scope: str
    offset: int | None = None
    line: int | None = None
    length: int = 0

    def __post_init__(self) -> None:
        if self.scope not in {"document", "reference-library"}:
            raise ValueError("review target scope is invalid")
        if self.offset is not None and self.offset < 0:
            raise ValueError("review target offset cannot be negative")
        if self.line is not None and self.line < 1:
            raise ValueError("review target line must be positive")
        if self.length < 0:
            raise ValueError("review target length cannot be negative")


@dataclass(frozen=True, slots=True)
class AcademicReviewDiagnostic:
    category: str
    kind: str
    severity: str
    message: str
    target: ReviewTarget
    key: str = ""

    def __post_init__(self) -> None:
        if self.category not in {"markdown", "footnotes", "citations", "references", "writing-hygiene"}:
            raise ValueError("academic review category is invalid")
        if self.severity not in {"error", "warning"}:
            raise ValueError("academic review severity is invalid")
        if not self.kind or not self.message:
            raise ValueError("academic review diagnostic fields must be non-empty")


@dataclass(frozen=True, slots=True)
class AcademicReviewReport:
    source_length: int
    source_sha256: str
    reference_token: ReferenceFileToken
    diagnostics: tuple[AcademicReviewDiagnostic, ...]

    def matches(self, text: str, reference_token: ReferenceFileToken) -> bool:
        return (
            isinstance(text, str)
            and isinstance(reference_token, ReferenceFileToken)
            and len(text) == self.source_length
            and hashlib.sha256(text.encode("utf-8")).hexdigest() == self.source_sha256
            and reference_token == self.reference_token
        )

    @property
    def errors(self) -> tuple[AcademicReviewDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "error")

    @property
    def warnings(self) -> tuple[AcademicReviewDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "warning")


@dataclass(frozen=True, slots=True)
class _Line:
    number: int
    start: int
    content_end: int
    logical: str


def _lines(text: str) -> tuple[_Line, ...]:
    result: list[_Line] = []
    offset = 0
    for number, raw in enumerate(text.splitlines(keepends=True), start=1):
        logical = raw.rstrip("\r\n")
        result.append(_Line(number, offset, offset + len(logical), logical))
        offset += len(raw)
    if text and not result:
        result.append(_Line(1, 0, len(text), text))
    return tuple(result)


def _line_for_offset(starts: tuple[int, ...], offset: int) -> int:
    if not starts:
        return 1
    return max(1, bisect_right(starts, max(0, offset)))


def _inside(offset: int, ranges: tuple[tuple[int, int], ...], starts: tuple[int, ...]) -> bool:
    if not starts:
        return False
    index = bisect_right(starts, offset) - 1
    if index < 0:
        return False
    start, end = ranges[index]
    return start <= offset < end


def _overlaps(start: int, end: int, ranges: tuple[tuple[int, int], ...], starts: tuple[int, ...]) -> bool:
    if start >= end or not ranges:
        return False
    index = max(0, bisect_right(starts, start) - 1)
    while index < len(ranges):
        lo, hi = ranges[index]
        if lo >= end:
            return False
        if start < hi and end > lo:
            return True
        index += 1
    return False


def _hygiene_diagnostics(text: str) -> tuple[AcademicReviewDiagnostic, ...]:
    markdown = build_markdown_document_map(text)
    inline_code = tuple(
        sorted(
            (span.source_start, span.source_end)
            for span in markdown.inline_spans
            if span.kind is MarkdownInlineKind.INLINE_CODE
        )
    )
    inline_starts = tuple(start for start, _ in inline_code)
    fenced = tuple(
        sorted(
            (block.source_start, block.source_end)
            for block in markdown.blocks
            if block.kind is MarkdownBlockKind.FENCED_CODE
        )
    )
    fenced_starts = tuple(start for start, _ in fenced)
    line_values = _lines(text)

    result: list[AcademicReviewDiagnostic] = []

    # Multiple spaces are examined only inside structural content ranges, not
    # Markdown indentation/markers. Inline code remains opaque.
    for block in markdown.blocks:
        if block.kind in {MarkdownBlockKind.FENCED_CODE, MarkdownBlockKind.THEMATIC_BREAK}:
            continue
        content = text[block.content_start:block.content_end]
        for match in _INTERNAL_SPACES_RE.finditer(content):
            start = block.content_start + match.start()
            end = block.content_start + match.end()
            if _overlaps(start, end, inline_code, inline_starts):
                continue
            result.append(
                AcademicReviewDiagnostic(
                    "writing-hygiene",
                    "multiple-internal-spaces",
                    "warning",
                    "Multiple consecutive spaces appear inside prose.",
                    ReviewTarget("document", start, block.line_start, end - start),
                )
            )

    # Markdown uses exactly two trailing spaces as an intentional hard line
    # break. Preserve that syntax and flag only other trailing horizontal space.
    for line in line_values:
        if _inside(line.start, fenced, fenced_starts):
            continue
        match = _TRAILING_HSPACE_RE.search(line.logical)
        if match is None:
            continue
        trailing = match.group(0)
        if trailing == "  ":
            continue
        start = line.start + match.start()
        result.append(
            AcademicReviewDiagnostic(
                "writing-hygiene",
                "trailing-whitespace",
                "warning",
                "Trailing whitespace appears at the end of the line.",
                ReviewTarget("document", start, line.number, len(trailing)),
            )
        )

    result.sort(key=lambda item: (item.target.offset if item.target.offset is not None else 10**18, item.kind))
    return tuple(result)


def build_academic_review(
    text: str,
    reference_snapshot: ReferenceLibrarySnapshot,
) -> AcademicReviewReport:
    if not isinstance(text, str):
        raise TypeError("academic review source must be text")
    if not isinstance(reference_snapshot, ReferenceLibrarySnapshot):
        raise TypeError("reference_snapshot must be ReferenceLibrarySnapshot")

    markdown = build_markdown_document_map(text)
    notes = ScholarlyNotesMap.from_text(text)
    citations = CitationDocumentMap.from_text(text)
    line_starts = tuple(line.start for line in _lines(text))
    result: list[AcademicReviewDiagnostic] = []

    for item in markdown.diagnostics:
        result.append(
            AcademicReviewDiagnostic(
                "markdown",
                item.kind,
                "error",
                item.message,
                ReviewTarget("document", item.offset, item.line, 1),
            )
        )

    for item in notes.diagnostics:
        result.append(
            AcademicReviewDiagnostic(
                "footnotes",
                item.kind,
                "error",
                item.message,
                ReviewTarget("document", item.offset, _line_for_offset(line_starts, item.offset), len(item.label) + 3),
                item.label,
            )
        )

    for item in citation_diagnostics(citations, reference_snapshot.records):
        result.append(
            AcademicReviewDiagnostic(
                "citations",
                item.kind,
                "error",
                item.message,
                ReviewTarget("document", item.offset, _line_for_offset(line_starts, item.offset), len(item.key)),
                item.key,
            )
        )

    for item in reference_snapshot.diagnostics:
        result.append(
            AcademicReviewDiagnostic(
                "references",
                item.kind,
                "error" if item.blocking else "warning",
                item.message,
                ReviewTarget("reference-library", None, item.line, 0),
            )
        )

    result.extend(_hygiene_diagnostics(text))
    category_order = {"markdown": 0, "footnotes": 1, "citations": 2, "references": 3, "writing-hygiene": 4}
    result.sort(
        key=lambda item: (
            0 if item.target.scope == "document" else 1,
            item.target.offset if item.target.offset is not None else item.target.line or 0,
            category_order[item.category],
            item.kind,
            item.key,
        )
    )
    return AcademicReviewReport(
        len(text),
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        reference_snapshot.token,
        tuple(result),
    )


def academic_review_text(report: AcademicReviewReport) -> str:
    if not isinstance(report, AcademicReviewReport):
        raise TypeError("report must be AcademicReviewReport")
    if not report.diagnostics:
        return "Academic Review: no deterministic issues found."
    lines = [
        f"Academic Review: {len(report.errors)} errors, {len(report.warnings)} warnings.",
        "",
    ]
    current = None
    labels = {
        "markdown": "Markdown",
        "footnotes": "Footnotes",
        "citations": "Citations",
        "references": "References",
        "writing-hygiene": "Writing Hygiene",
    }
    for item in report.diagnostics:
        if item.category != current:
            if current is not None:
                lines.append("")
            current = item.category
            lines.append(labels[item.category] + ":")
        location = (
            f"line {item.target.line}"
            if item.target.line is not None
            else item.target.scope
        )
        lines.append(f"• {item.message} ({location})")
    return "\n".join(lines)
