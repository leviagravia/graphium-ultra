"""GTK-free document outline projection for Graphium Plus.

The editable document remains the sole authority.  The Outliner is a disposable
projection of headings from the single Plus MarkdownDocumentMap and owns no tree,
file, document, save, Undo, or persistence state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .markdown import MarkdownDocumentMap


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    level: int
    title: str
    source_start: int
    content_start: int
    section_end: int

    def __post_init__(self) -> None:
        if not 1 <= self.level <= 6:
            raise ValueError("outline heading level must be 1..6")
        if self.source_start < 0 or self.content_start < self.source_start:
            raise ValueError("invalid outline source position")
        if self.section_end < self.content_start:
            raise ValueError("outline section end precedes heading content")


@dataclass(frozen=True, slots=True)
class OutlineProjection:
    source_length: int
    source_sha256: str
    entries: tuple[OutlineEntry, ...]

    def matches_text(self, text: str) -> bool:
        return (
            len(text) == self.source_length
            and hashlib.sha256(text.encode("utf-8")).hexdigest() == self.source_sha256
        )

    def entry_at_or_before(self, offset: int) -> OutlineEntry | None:
        """Return the nearest heading at/before offset using monotonic binary search."""
        if not self.entries:
            return None
        offset = max(0, min(int(offset), self.source_length))
        low, high = 0, len(self.entries) - 1
        found = -1
        while low <= high:
            mid = low + (high - low) // 2
            if self.entries[mid].source_start <= offset:
                found = mid
                low = mid + 1
            else:
                high = mid - 1
        return None if found < 0 else self.entries[found]


def build_outline_projection(text: str, document_map: MarkdownDocumentMap) -> OutlineProjection:
    """Project heading rows from the exact captured map; reject stale offsets."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(document_map, MarkdownDocumentMap):
        raise TypeError("document_map must be MarkdownDocumentMap")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if len(text) != document_map.text_length or digest != document_map.source_sha256:
        raise ValueError("text does not match MarkdownDocumentMap source identity")
    entries = tuple(
        OutlineEntry(
            level=heading.level or 1,
            title=text[heading.content_start:heading.content_end].strip(),
            source_start=heading.source_start,
            content_start=heading.content_start,
            section_end=heading.section_end if heading.section_end is not None else len(text),
        )
        for heading in document_map.headings
    )
    return OutlineProjection(len(text), digest, entries)
