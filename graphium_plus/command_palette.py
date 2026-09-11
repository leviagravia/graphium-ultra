"""Pure command-palette projection and deterministic ranking for Graphium Plus."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PaletteEntry:
    """Presentation-only projection of one already-owned application command."""

    label: str
    breadcrumb: str
    action_name: str
    target: str | None
    shortcut: str
    order: int


def normalize_palette_text(value: str) -> str:
    """Unicode-stable, whitespace-stable normalization used only for matching."""

    return _SPACE_RE.sub(" ", value.strip().casefold())


def _subsequence_match(needle: str, haystack: str, start: int = 0) -> tuple[int, int, int] | None:
    """Return greedy (first, last, gap-penalty) for an ordered subsequence."""

    if not needle:
        return start, start, 0
    cursor = max(0, int(start))
    first = -1
    previous = -1
    gaps = 0
    for char in needle:
        found = haystack.find(char, cursor)
        if found < 0:
            return None
        if first < 0:
            first = found
        if previous >= 0:
            gaps += max(0, found - previous - 1)
        previous = found
        cursor = found + 1
    return first, previous, gaps


def _is_word_boundary(text: str, index: int) -> bool:
    if index <= 0:
        return True
    before = text[index - 1]
    return not before.isalnum()


def _rank_entry(entry: PaletteEntry, query: str) -> tuple[int, int, int, int] | None:
    normalized_query = normalize_palette_text(query)
    if not normalized_query:
        return 0, 0, 0, entry.order

    label = normalize_palette_text(entry.label)
    breadcrumb = normalize_palette_text(entry.breadcrumb)
    corpus = label if not breadcrumb else f"{label} {breadcrumb}"

    if normalized_query == label:
        return 0, 0, len(label), entry.order
    if label.startswith(normalized_query):
        return 1, 0, len(label), entry.order
    if normalized_query in label:
        return 2, label.index(normalized_query), len(label), entry.order

    tokens = normalized_query.split(" ")
    total_gaps = 0
    all_boundaries = True
    for token in tokens:
        match = _subsequence_match(token, corpus)
        if match is None:
            return None
        first, _last, gaps = match
        all_boundaries = all_boundaries and _is_word_boundary(corpus, first)
        total_gaps += gaps

    quality = 3 if all_boundaries else 4
    return quality, total_gaps, len(label), entry.order


def filter_palette_entries(entries: Iterable[PaletteEntry], query: str) -> tuple[PaletteEntry, ...]:
    """Filter/rank entries deterministically without creating a persistent index."""

    ranked: list[tuple[tuple[int, int, int, int], PaletteEntry]] = []
    for entry in entries:
        rank = _rank_entry(entry, query)
        if rank is not None:
            ranked.append((rank, entry))
    ranked.sort(key=lambda pair: pair[0])
    return tuple(entry for _rank, entry in ranked)
