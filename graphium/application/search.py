"""GTK-free current-document search/replace command authority."""
from __future__ import annotations

from dataclasses import dataclass

from graphium.application.renderability import ensure_interactive_text_renderable
from graphium.domain.edit_history import (
    DEFAULT_MAX_HISTORY_PAYLOAD_CHARS,
    EditKind,
    ReplayOperation,
    ViewState,
)
from graphium.domain.text_search import (
    SearchInputError,
    SearchScaleError,
    SearchMatch,
    SearchResult,
    find_all,
    find_next,
    find_previous,
    is_exact_match,
    validate_query,
    validate_replacement,
)


# An explicit Replace All is allowed to be substantial, but Graphium must not build
# hundreds of thousands or millions of Python match/delta objects in one UI command.
# The cap is checked during match enumeration, before final text or replay operations
# are materialized. The independent history-payload cap remains authoritative too.
MAX_REPLACE_ALL_MATCHES = 50_000


@dataclass(frozen=True)
class ReplacementPlan:
    source_state_id: int
    source_text: str
    final_text: str
    operations: tuple[ReplayOperation, ...]
    before_view: ViewState
    target_view: ViewState
    changed_count: int

    def __post_init__(self) -> None:
        if int(self.source_state_id) <= 0:
            raise ValueError("replacement plan source state id must be positive")
        if not isinstance(self.source_text, str) or not isinstance(self.final_text, str):
            raise TypeError("replacement plan text must be strings")
        if not isinstance(self.before_view, ViewState) or not isinstance(self.target_view, ViewState):
            raise TypeError("replacement plan views must be ViewState")
        if int(self.changed_count) < 0:
            raise ValueError("replacement plan changed_count must be non-negative")

    @property
    def payload_chars(self) -> int:
        return sum(len(operation.text) for operation in self.operations)

    @property
    def changed(self) -> bool:
        return self.changed_count > 0


def _mapped_offset(offset: int, matches: tuple[SearchMatch, ...], replacement: str) -> int:
    """Map a pre-replacement character offset to the resulting text deterministically."""
    original = max(0, int(offset))
    delta = 0
    for match in matches:
        if original < match.start:
            break
        mapped_start = match.start + delta
        if original == match.start:
            return mapped_start
        if original <= match.end:
            return mapped_start + len(replacement)
        delta += len(replacement) - match.length
    return original + delta


def _build_final_text(source: str, matches: tuple[SearchMatch, ...], replacement: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.append(source[cursor:match.start])
        pieces.append(replacement)
        cursor = match.end
    pieces.append(source[cursor:])
    return "".join(pieces)


def _changed_matches(
    source: str,
    matches: tuple[SearchMatch, ...],
    replacement: str,
) -> tuple[SearchMatch, ...]:
    return tuple(match for match in matches if source[match.start:match.end] != replacement)


def _operations_descending(
    source: str,
    matches: tuple[SearchMatch, ...],
    replacement: str,
) -> tuple[ReplayOperation, ...]:
    result: list[ReplayOperation] = []
    for match in reversed(matches):
        original = source[match.start:match.end]
        if original == replacement:
            continue
        result.append(ReplayOperation(EditKind.DELETE, match.start, original))
        if replacement:
            result.append(ReplayOperation(EditKind.INSERT, match.start, replacement))
    return tuple(result)


class SearchController:
    """Small command-state authority; no background scan/cache/history database."""

    __slots__ = ("query", "replacement", "match_case")

    def __init__(self) -> None:
        self.query = ""
        self.replacement = ""
        self.match_case = False

    @property
    def has_query(self) -> bool:
        return bool(self.query)

    def configure(
        self,
        *,
        query: str | None = None,
        replacement: str | None = None,
        match_case: bool | None = None,
    ) -> None:
        if query is not None:
            self.query = validate_query(query)
        if replacement is not None:
            self.replacement = validate_replacement(replacement)
        if match_case is not None:
            self.match_case = bool(match_case)

    def clear_query(self) -> None:
        self.query = ""

    def find_next(self, text: str, start_offset: int) -> SearchResult:
        if not self.query:
            return SearchResult(None, False)
        return find_next(text, self.query, start_offset, match_case=self.match_case, wrap=True)

    def find_previous(self, text: str, start_offset: int) -> SearchResult:
        if not self.query:
            return SearchResult(None, False)
        return find_previous(text, self.query, start_offset, match_case=self.match_case, wrap=True)

    def selection_is_match(self, text: str, start: int, end: int) -> bool:
        if not self.query:
            return False
        return is_exact_match(text, self.query, start, end, match_case=self.match_case)

    def build_replace_all_plan(
        self,
        *,
        source_text: str,
        source_state_id: int,
        before_view: ViewState,
    ) -> ReplacementPlan:
        if not self.query:
            raise SearchInputError("Replace All requires a non-empty query")
        # Case-sensitive query==replacement is provably a no-op and does not need
        # to enumerate a high-density document merely to discover that fact.
        if self.match_case and self.query == self.replacement:
            return ReplacementPlan(
                source_state_id,
                source_text,
                source_text,
                (),
                before_view,
                before_view,
                0,
            )
        matches = find_all(
            source_text,
            self.query,
            match_case=self.match_case,
            max_matches=MAX_REPLACE_ALL_MATCHES,
        )
        changed = _changed_matches(source_text, matches, self.replacement)
        if not changed:
            return ReplacementPlan(
                source_state_id,
                source_text,
                source_text,
                (),
                before_view,
                before_view,
                0,
            )
        payload_chars = sum(
            match.length + (len(self.replacement) if self.replacement else 0)
            for match in changed
        )
        if payload_chars > DEFAULT_MAX_HISTORY_PAYLOAD_CHARS:
            raise SearchScaleError(
                "Replace All exceeds Graphium's bounded Undo payload budget "
                f"({payload_chars} > {DEFAULT_MAX_HISTORY_PAYLOAD_CHARS} characters)"
            )
        final_text = _build_final_text(source_text, matches, self.replacement)
        ensure_interactive_text_renderable(final_text)
        target_view = ViewState(
            _mapped_offset(before_view.insert_offset, matches, self.replacement),
            _mapped_offset(before_view.selection_bound_offset, matches, self.replacement),
        )
        return ReplacementPlan(
            source_state_id,
            source_text,
            final_text,
            _operations_descending(source_text, changed, self.replacement),
            before_view,
            target_view,
            len(changed),
        )

    def build_replace_one_plan(
        self,
        *,
        source_text: str,
        source_state_id: int,
        before_view: ViewState,
        selection_start: int,
        selection_end: int,
    ) -> ReplacementPlan | None:
        if not self.query:
            raise SearchInputError("Replace requires a non-empty query")
        start = min(int(selection_start), int(selection_end))
        end = max(int(selection_start), int(selection_end))
        if self.selection_is_match(source_text, start, end):
            match = SearchMatch(start, end)
        else:
            anchor = end if end > start else before_view.insert_offset
            result = self.find_next(source_text, anchor)
            match = result.match
        if match is None:
            return None

        original = source_text[match.start:match.end]
        if original == self.replacement:
            # No history/state change; navigate to another occurrence if available.
            result = find_next(
                source_text,
                self.query,
                match.end,
                match_case=self.match_case,
                wrap=True,
            )
            target = result.match
            target_view = (
                ViewState(target.end, target.start) if target is not None else ViewState(match.end, match.end)
            )
            return ReplacementPlan(
                source_state_id,
                source_text,
                source_text,
                (),
                before_view,
                target_view,
                0,
            )

        final_text = source_text[:match.start] + self.replacement + source_text[match.end:]
        ensure_interactive_text_renderable(final_text)
        replacement_end = match.start + len(self.replacement)
        next_result = find_next(
            final_text,
            self.query,
            replacement_end,
            match_case=self.match_case,
            wrap=True,
        )
        next_match = next_result.match
        target_view = (
            ViewState(next_match.end, next_match.start)
            if next_match is not None
            else ViewState(replacement_end, replacement_end)
        )
        operations = [ReplayOperation(EditKind.DELETE, match.start, original)]
        if self.replacement:
            operations.append(ReplayOperation(EditKind.INSERT, match.start, self.replacement))
        return ReplacementPlan(
            source_state_id,
            source_text,
            final_text,
            tuple(operations),
            before_view,
            target_view,
            1,
        )
