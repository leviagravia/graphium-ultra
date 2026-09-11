"""GTK-free literal text-search semantics for Graphium.

Graphium intentionally implements only current-document literal search. Query/replacement
fields are single-line. Case-insensitive matching uses Unicode casefold while retaining
exact source-character offsets, including length-changing folds such as ``ß -> ss``.

The Unicode path is deliberately line-bounded: a query cannot cross a newline and
the active editor already enforces a bounded interactive logical-line length. This
avoids folding/caching an entire multi-megabyte document or allocating one offset record
per character. Find Next/Previous never materialize all matches. Replace All can impose
an explicit match cap and fails closed before constructing an excessive edit plan.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


class SearchInputError(ValueError):
    """Raised when a single-line search field violates the current contract."""


class SearchScaleError(RuntimeError):
    """Raised before Replace All can materialize an excessive number of matches."""


@dataclass(frozen=True, order=True)
class SearchMatch:
    start: int
    end: int

    def __post_init__(self) -> None:
        if int(self.start) < 0 or int(self.end) <= int(self.start):
            raise ValueError("search match must be a non-empty ascending range")
        object.__setattr__(self, "start", int(self.start))
        object.__setattr__(self, "end", int(self.end))

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class SearchResult:
    match: SearchMatch | None
    wrapped: bool = False


@dataclass(frozen=True)
class _FoldExpansion:
    folded_start: int
    folded_end: int
    source_start: int
    source_end: int


@dataclass(frozen=True)
class _FoldedLine:
    text: str
    source_length: int
    expansions: tuple[_FoldExpansion, ...]
    folded_expansion_starts: tuple[int, ...]
    source_expansion_ends: tuple[int, ...]

    def source_boundary(self, folded_offset: int) -> int | None:
        pos = int(folded_offset)
        if pos < 0 or pos > len(self.text):
            return None
        if not self.expansions:
            return pos
        index = bisect_right(self.folded_expansion_starts, pos) - 1
        if index < 0:
            return pos
        event = self.expansions[index]
        if pos == event.folded_start:
            return event.source_start
        if pos < event.folded_end:
            return None
        return pos - (event.folded_end - event.source_end)

    def folded_boundary(self, source_offset: int) -> int | None:
        pos = int(source_offset)
        if pos < 0 or pos > self.source_length:
            return None
        if not self.expansions:
            return pos
        index = bisect_right(self.source_expansion_ends, pos) - 1
        if index < 0:
            return pos
        event = self.expansions[index]
        return pos + (event.folded_end - event.source_end)


def validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("search query must be a string")
    if not query:
        raise SearchInputError("search query must not be empty")
    if "\n" in query or "\r" in query:
        raise SearchInputError("search query must be single-line")
    return query


def validate_replacement(replacement: str) -> str:
    if not isinstance(replacement, str):
        raise TypeError("replacement must be a string")
    if "\n" in replacement or "\r" in replacement:
        raise SearchInputError("replacement must be single-line")
    return replacement


def _checked_match_cap(max_matches: int | None) -> int | None:
    if max_matches is None:
        return None
    cap = int(max_matches)
    if cap <= 0:
        raise ValueError("max_matches must be positive when supplied")
    return cap


def _append_bounded(result: list[SearchMatch], match: SearchMatch, cap: int | None) -> None:
    if cap is not None and len(result) >= cap:
        raise SearchScaleError(
            f"search match count exceeds Graphium's bounded Replace All budget ({cap})"
        )
    result.append(match)


def _fold_line(line: str) -> _FoldedLine:
    folded = line.casefold()
    if len(folded) == len(line):
        return _FoldedLine(folded, len(line), (), (), ())

    expansions: list[_FoldExpansion] = []
    delta = 0
    for source_offset, char in enumerate(line):
        piece_length = len(char.casefold())
        if piece_length != 1:
            folded_start = source_offset + delta
            folded_end = folded_start + piece_length
            expansions.append(
                _FoldExpansion(folded_start, folded_end, source_offset, source_offset + 1)
            )
            delta += piece_length - 1
    if len(line) + delta != len(folded):
        raise RuntimeError("Unicode casefold offset accounting invariant failed")
    frozen = tuple(expansions)
    return _FoldedLine(
        folded,
        len(line),
        frozen,
        tuple(event.folded_start for event in frozen),
        tuple(event.source_end for event in frozen),
    )


def _folded_find_forward(line: _FoldedLine, folded_query: str, source_start: int) -> SearchMatch | None:
    cursor = line.folded_boundary(source_start)
    if cursor is None:
        return None
    qlen = len(folded_query)
    while True:
        found = line.text.find(folded_query, cursor)
        if found < 0:
            return None
        transformed_end = found + qlen
        start = line.source_boundary(found)
        end = line.source_boundary(transformed_end)
        if start is not None and end is not None and end > start:
            return SearchMatch(start, end)
        cursor = found + 1


def _folded_find_backward(line: _FoldedLine, folded_query: str, source_end: int) -> SearchMatch | None:
    high = line.folded_boundary(source_end)
    if high is None:
        return None
    qlen = len(folded_query)
    while high >= qlen:
        found = line.text.rfind(folded_query, 0, high)
        if found < 0:
            return None
        transformed_end = found + qlen
        start = line.source_boundary(found)
        end = line.source_boundary(transformed_end)
        if start is not None and end is not None and end > start:
            return SearchMatch(start, end)
        high = transformed_end - 1
    return None


def _line_bounds(text: str, offset: int) -> tuple[int, int]:
    pos = min(max(0, int(offset)), len(text))
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return start, end


def _casefold_find_next_no_wrap(text: str, query: str, start_offset: int) -> SearchMatch | None:
    folded_query = query.casefold()
    line_start, line_end = _line_bounds(text, start_offset)
    cursor = line_start
    local_start = start_offset - line_start
    while cursor <= len(text):
        if cursor != line_start:
            line_end = text.find("\n", cursor)
            if line_end < 0:
                line_end = len(text)
            local_start = 0
        folded_line = _fold_line(text[cursor:line_end])
        local = _folded_find_forward(folded_line, folded_query, local_start)
        if local is not None:
            return SearchMatch(cursor + local.start, cursor + local.end)
        if line_end >= len(text):
            break
        cursor = line_end + 1
    return None


def _casefold_find_previous_no_wrap(text: str, query: str, start_offset: int) -> SearchMatch | None:
    folded_query = query.casefold()
    line_start, line_end = _line_bounds(text, start_offset)
    cursor_start = line_start
    cursor_end = line_end
    local_end = start_offset - line_start
    while True:
        folded_line = _fold_line(text[cursor_start:cursor_end])
        local = _folded_find_backward(folded_line, folded_query, local_end)
        if local is not None:
            return SearchMatch(cursor_start + local.start, cursor_start + local.end)
        if cursor_start == 0:
            break
        cursor_end = cursor_start - 1  # previous newline, excluded from the line
        previous_newline = text.rfind("\n", 0, cursor_end)
        cursor_start = previous_newline + 1
        local_end = cursor_end - cursor_start
    return None


def _casefold_matches(
    text: str,
    query: str,
    *,
    max_matches: int | None = None,
) -> tuple[SearchMatch, ...]:
    folded_query = query.casefold()
    cap = _checked_match_cap(max_matches)
    result: list[SearchMatch] = []
    cursor = 0
    while cursor <= len(text):
        line_end = text.find("\n", cursor)
        if line_end < 0:
            line_end = len(text)
        folded_line = _fold_line(text[cursor:line_end])
        folded_cursor = 0
        qlen = len(folded_query)
        while folded_cursor <= len(folded_line.text) - qlen:
            found = folded_line.text.find(folded_query, folded_cursor)
            if found < 0:
                break
            transformed_end = found + qlen
            start = folded_line.source_boundary(found)
            end = folded_line.source_boundary(transformed_end)
            if start is not None and end is not None and end > start:
                _append_bounded(result, SearchMatch(cursor + start, cursor + end), cap)
                folded_cursor = transformed_end
            else:
                folded_cursor = found + 1
        if line_end >= len(text):
            break
        cursor = line_end + 1
    return tuple(result)


def find_all(
    text: str,
    query: str,
    *,
    match_case: bool = False,
    max_matches: int | None = None,
) -> tuple[SearchMatch, ...]:
    if not isinstance(text, str):
        raise TypeError("search text must be a string")
    query = validate_query(query)
    cap = _checked_match_cap(max_matches)
    if not match_case:
        return _casefold_matches(text, query, max_matches=cap)

    result: list[SearchMatch] = []
    cursor = 0
    qlen = len(query)
    while cursor <= len(text) - qlen:
        found = text.find(query, cursor)
        if found < 0:
            break
        end = found + qlen
        _append_bounded(result, SearchMatch(found, end), cap)
        cursor = end
    return tuple(result)


def is_exact_match(
    text: str,
    query: str,
    start: int,
    end: int,
    *,
    match_case: bool = False,
) -> bool:
    if not isinstance(text, str):
        raise TypeError("search text must be a string")
    query = validate_query(query)
    start = max(0, int(start))
    end = min(len(text), max(start, int(end)))
    selected = text[start:end]
    if not selected or "\n" in selected or "\r" in selected:
        return False
    if match_case:
        return selected == query
    return selected.casefold() == query.casefold()


def find_next(
    text: str,
    query: str,
    start_offset: int,
    *,
    match_case: bool = False,
    wrap: bool = True,
) -> SearchResult:
    if not isinstance(text, str):
        raise TypeError("search text must be a string")
    query = validate_query(query)
    start_offset = min(max(0, int(start_offset)), len(text))
    if match_case:
        found = text.find(query, start_offset)
        match = SearchMatch(found, found + len(query)) if found >= 0 else None
        wrapped_match = None
        if match is None and wrap and start_offset > 0:
            found = text.find(query, 0, start_offset)
            wrapped_match = SearchMatch(found, found + len(query)) if found >= 0 else None
    else:
        match = _casefold_find_next_no_wrap(text, query, start_offset)
        wrapped_match = None
        if match is None and wrap and start_offset > 0:
            candidate = _casefold_find_next_no_wrap(text, query, 0)
            if candidate is not None and candidate.start < start_offset:
                wrapped_match = candidate
    if match is not None:
        return SearchResult(match, False)
    if wrapped_match is not None:
        return SearchResult(wrapped_match, True)
    return SearchResult(None, False)


def find_previous(
    text: str,
    query: str,
    start_offset: int,
    *,
    match_case: bool = False,
    wrap: bool = True,
) -> SearchResult:
    if not isinstance(text, str):
        raise TypeError("search text must be a string")
    query = validate_query(query)
    start_offset = min(max(0, int(start_offset)), len(text))
    if match_case:
        found = text.rfind(query, 0, start_offset)
        match = SearchMatch(found, found + len(query)) if found >= 0 else None
        wrapped_match = None
        if match is None and wrap and start_offset < len(text):
            found = text.rfind(query, start_offset)
            wrapped_match = SearchMatch(found, found + len(query)) if found >= 0 else None
    else:
        match = _casefold_find_previous_no_wrap(text, query, start_offset)
        wrapped_match = None
        if match is None and wrap and start_offset < len(text):
            candidate = _casefold_find_previous_no_wrap(text, query, len(text))
            if candidate is not None and candidate.end > start_offset:
                wrapped_match = candidate
    if match is not None:
        return SearchResult(match, False)
    if wrapped_match is not None:
        return SearchResult(wrapped_match, True)
    return SearchResult(None, False)
