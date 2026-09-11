"""GTK-free explicit spell-session state and replacement authority adapter."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from graphium.application.renderability import ensure_interactive_text_renderable
from graphium.domain.edit_history import EditKind, ReplayOperation, ViewState
from graphium.domain.history import HistoryState
from graphium.domain.spellcheck import WordSpan, iter_word_spans
from graphium.infrastructure.hunspell_session import HunspellResult


class SpellCheckError(RuntimeError): pass
class SpellCheckStateError(SpellCheckError): pass
class SpellCheckStaleError(SpellCheckError): pass


class SpellCheckPhase(str, Enum):
    SCANNING = "scanning"
    WAITING = "waiting"
    ISSUE = "issue"
    COMPLETE = "complete"
    STALE = "stale"
    CLOSED = "closed"


@dataclass(frozen=True)
class SpellCheckRequest:
    sequence: int
    source_state_id: int
    span: WordSpan


@dataclass(frozen=True)
class SpellIssue:
    source_state_id: int
    span: WordSpan
    suggestions: tuple[str, ...]


@dataclass(frozen=True)
class SpellReplacementPlan:
    source_state_id: int
    source_text: str
    final_text: str
    span: WordSpan
    replacement: str
    operations: tuple[ReplayOperation, ...]
    before_view: ViewState
    target_view: ViewState

    @property
    def changed(self) -> bool: return bool(self.operations)


class SpellEditorPort(Protocol):
    @property
    def current_state_id(self) -> int: ...
    def capture_programmatic_source(self) -> HistoryState: ...
    def apply_prevalidated_programmatic_group(
        self, *, operations: tuple[ReplayOperation, ...], expected_source_state_id: int,
        final_text: str, before_view: ViewState, target_view: ViewState,
    ) -> int: ...


def plan_spell_replacement(
    *, source_text: str, source_state_id: int, span: WordSpan, replacement: str,
    before_view: ViewState,
) -> SpellReplacementPlan:
    if not isinstance(source_text, str) or not isinstance(replacement, str):
        raise TypeError("spell replacement text must be strings")
    source_state_id = int(source_state_id)
    if source_state_id <= 0: raise ValueError("source_state_id must be positive")
    if not isinstance(span, WordSpan) or span.end > len(source_text) or source_text[span.start:span.end] != span.text:
        raise ValueError("spell span does not match the source text")
    if not isinstance(before_view, ViewState): raise TypeError("before_view must be ViewState")
    final_text = source_text[:span.start] + replacement + source_text[span.end:]
    ensure_interactive_text_renderable(final_text)
    operations: list[ReplayOperation] = []
    if replacement != span.text:
        operations.append(ReplayOperation(EditKind.DELETE, span.start, span.text))
        if replacement: operations.append(ReplayOperation(EditKind.INSERT, span.start, replacement))
    end = span.start + len(replacement)
    return SpellReplacementPlan(
        source_state_id, source_text, final_text, span, replacement, tuple(operations),
        before_view, ViewState(end, end),
    )


class SpellCheckController:
    """One dialog-lifetime session; external I/O is intentionally outside this class."""
    __slots__ = (
        "editor", "_text", "_source_state_id", "_cursor", "_ignored_all", "_pending",
        "_issue", "_sequence", "_phase",
    )

    def __init__(self, editor: SpellEditorPort) -> None:
        if editor is None: raise TypeError("editor is required")
        snap = editor.capture_programmatic_source()
        if snap.state_id <= 0: raise ValueError("editor snapshot must have a positive state id")
        self.editor = editor; self._text = snap.text; self._source_state_id = snap.state_id
        self._cursor = 0; self._ignored_all: set[str] = set(); self._pending = None
        self._issue = None; self._sequence = 0; self._phase = SpellCheckPhase.SCANNING

    @property
    def phase(self) -> SpellCheckPhase: return self._phase
    @property
    def issue(self) -> SpellIssue | None: return self._issue
    @property
    def ignored_all(self) -> frozenset[str]: return frozenset(self._ignored_all)
    @property
    def source_state_id(self) -> int: return self._source_state_id
    @property
    def cursor(self) -> int: return self._cursor

    def _open(self) -> None:
        if self._phase is SpellCheckPhase.CLOSED: raise SpellCheckStateError("spell session is closed")
        if self._phase is SpellCheckPhase.STALE: raise SpellCheckStaleError("document changed; run spell check again")

    def _fresh(self) -> None:
        self._open()
        if self.editor.current_state_id != self._source_state_id:
            self._phase = SpellCheckPhase.STALE; self._pending = None; self._issue = None
            raise SpellCheckStaleError("document changed; run spell check again")

    def next_request(self) -> SpellCheckRequest | None:
        self._fresh()
        if self._pending is not None or self._issue is not None:
            raise SpellCheckStateError("resolve the current spell request before continuing")
        for span in iter_word_spans(self._text, start=self._cursor):
            self._cursor = span.end
            if span.text in self._ignored_all: continue
            self._sequence += 1
            request = SpellCheckRequest(self._sequence, self._source_state_id, span)
            self._pending = request; self._phase = SpellCheckPhase.WAITING
            return request
        self._phase = SpellCheckPhase.COMPLETE
        return None

    def accept_result(self, request: SpellCheckRequest, result: HunspellResult) -> SpellIssue | None:
        self._fresh()
        if request != self._pending: raise SpellCheckStateError("spell result does not match the pending request")
        if not isinstance(result, HunspellResult) or result.word != request.span.text:
            raise SpellCheckStateError("spell result word does not match the pending token")
        self._pending = None
        if result.correct:
            self._phase = SpellCheckPhase.SCANNING
            return None
        issue = SpellIssue(self._source_state_id, request.span, result.suggestions)
        self._issue = issue; self._phase = SpellCheckPhase.ISSUE
        return issue

    def _current_issue(self) -> SpellIssue:
        self._fresh()
        if self._issue is None: raise SpellCheckStateError("there is no current misspelling")
        return self._issue

    def ignore(self) -> None:
        issue = self._current_issue(); self._cursor = issue.span.end; self._issue = None
        self._phase = SpellCheckPhase.SCANNING

    def ignore_all(self) -> None:
        issue = self._current_issue(); self._ignored_all.add(issue.span.text)
        self._cursor = issue.span.end; self._issue = None; self._phase = SpellCheckPhase.SCANNING

    def replace(self, replacement: str) -> SpellReplacementPlan:
        issue = self._current_issue()
        snap = self.editor.capture_programmatic_source()
        if snap.state_id != self._source_state_id or snap.text != self._text:
            self._phase = SpellCheckPhase.STALE; self._issue = None
            raise SpellCheckStaleError("document changed; run spell check again")
        plan = plan_spell_replacement(
            source_text=self._text, source_state_id=self._source_state_id, span=issue.span,
            replacement=replacement, before_view=ViewState(snap.insert_offset, snap.selection_bound_offset),
        )
        new_state = self.editor.apply_prevalidated_programmatic_group(
            operations=plan.operations, expected_source_state_id=plan.source_state_id,
            final_text=plan.final_text, before_view=plan.before_view, target_view=plan.target_view,
        )
        self._text = plan.final_text; self._source_state_id = new_state
        self._cursor = issue.span.start + len(replacement); self._issue = None
        self._phase = SpellCheckPhase.SCANNING
        return plan

    def close(self) -> None:
        self._pending = None; self._issue = None; self._ignored_all.clear(); self._phase = SpellCheckPhase.CLOSED
