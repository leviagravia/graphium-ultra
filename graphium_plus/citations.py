"""Pandoc citation authority and pure insertion/diagnostic plans for Graphium Plus.

Citation scanning consumes the existing Plus Markdown structural map for code
exclusion.  It never reparses fenced/inline code, performs no I/O and owns no
editor, history or persistence state.
"""
from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
import hashlib
import re
from typing import Iterable

from graphium.application.renderability import ensure_interactive_text_renderable
from graphium.domain.edit_history import (
    DEFAULT_MAX_HISTORY_PAYLOAD_CHARS,
    EditKind,
    ReplayOperation,
    ViewState,
)
from graphium_plus.markdown import (
    MarkdownBlockKind,
    MarkdownInlineKind,
    build_markdown_document_map,
)
from graphium_plus.references import (
    ReferenceRecord,
    is_valid_reference_key,
    normalize_reference_key,
)


_KEY_BODY = r"[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9_])?"
_ITEM_RE = re.compile(rf"(?<![A-Za-z0-9_@])@(?P<key>{_KEY_BODY})(?=$|[\s\],;.!?])")
_BRACKET_RE = re.compile(r"\[(?P<body>[^\]\n]*@[^\]\n]*)\]")


class CitationError(ValueError):
    """Rejected citation operation or malformed citation request."""


@dataclass(frozen=True, slots=True)
class CitationItem:
    key: str
    key_start: int
    key_end: int

    def __post_init__(self) -> None:
        key = normalize_reference_key(self.key)
        if not is_valid_reference_key(key):
            raise ValueError("citation key is invalid")
        if self.key_start < 0 or self.key_end <= self.key_start:
            raise ValueError("citation key range is invalid")
        object.__setattr__(self, "key", key)


@dataclass(frozen=True, slots=True)
class CitationCluster:
    source_start: int
    source_end: int
    items: tuple[CitationItem, ...]
    raw: str
    bracketed: bool

    def __post_init__(self) -> None:
        if self.source_start < 0 or self.source_end <= self.source_start:
            raise ValueError("citation cluster range is invalid")
        if not self.items:
            raise ValueError("citation cluster requires at least one item")
        if any(
            item.key_start < self.source_start or item.key_end > self.source_end
            for item in self.items
        ):
            raise ValueError("citation item lies outside cluster")

    @property
    def keys(self) -> tuple[str, ...]:
        result: list[str] = []
        for item in self.items:
            if item.key not in result:
                result.append(item.key)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class CitationDocumentMap:
    text_length: int
    source_sha256: str
    clusters: tuple[CitationCluster, ...]

    def matches_text(self, text: str) -> bool:
        return isinstance(text, str) and len(text) == self.text_length and hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest() == self.source_sha256

    @property
    def cited_keys(self) -> tuple[str, ...]:
        result: list[str] = []
        for cluster in self.clusters:
            for key in cluster.keys:
                if key not in result:
                    result.append(key)
        return tuple(result)

    @classmethod
    def from_text(cls, text: str) -> "CitationDocumentMap":
        if not isinstance(text, str):
            raise TypeError("citation source must be text")
        markdown = build_markdown_document_map(text)
        excluded = [
            (block.source_start, block.source_end)
            for block in markdown.blocks
            if block.kind is MarkdownBlockKind.FENCED_CODE
        ]
        excluded.extend(
            (span.source_start, span.source_end)
            for span in markdown.inline_spans
            if span.kind is MarkdownInlineKind.INLINE_CODE
        )
        excluded.sort()
        excluded_starts = tuple(start for start, _end in excluded)

        def inside(offset: int) -> bool:
            if not excluded_starts:
                return False
            index = bisect_right(excluded_starts, offset) - 1
            if index < 0:
                return False
            start, end = excluded[index]
            return start <= offset < end

        clusters: list[CitationCluster] = []
        for match in _BRACKET_RE.finditer(text):
            if inside(match.start()):
                continue
            items: list[CitationItem] = []
            body_start = match.start("body")
            for item_match in _ITEM_RE.finditer(match.group("body")):
                key_start = body_start + item_match.start("key")
                key_end = body_start + item_match.end("key")
                if not inside(key_start):
                    items.append(CitationItem(item_match.group("key"), key_start, key_end))
            if items:
                clusters.append(
                    CitationCluster(
                        match.start(), match.end(), tuple(items), match.group(0), True
                    )
                )

        bracket_ranges = tuple((cluster.source_start, cluster.source_end) for cluster in clusters)
        bracket_starts = tuple(start for start, _end in bracket_ranges)

        def inside_bracket(offset: int) -> bool:
            if not bracket_starts:
                return False
            index = bisect_right(bracket_starts, offset) - 1
            if index < 0:
                return False
            start, end = bracket_ranges[index]
            return start <= offset < end

        for match in _ITEM_RE.finditer(text):
            at_start = match.start("key") - 1
            if inside(at_start) or inside_bracket(at_start):
                continue
            clusters.append(
                CitationCluster(
                    at_start,
                    match.end("key"),
                    (CitationItem(match.group("key"), match.start("key"), match.end("key")),),
                    text[at_start:match.end("key")],
                    False,
                )
            )
        clusters.sort(key=lambda cluster: (cluster.source_start, cluster.source_end))
        return cls(
            len(text), hashlib.sha256(text.encode("utf-8")).hexdigest(), tuple(clusters)
        )


@dataclass(frozen=True, slots=True)
class CitationDiagnostic:
    kind: str
    key: str
    offset: int
    message: str


@dataclass(frozen=True, slots=True)
class CitationLookup:
    status: str
    key: str | None = None
    keys: tuple[str, ...] = ()
    cluster: CitationCluster | None = None

    def __post_init__(self) -> None:
        if self.status not in {"none", "unique", "ambiguous"}:
            raise ValueError("citation lookup status is invalid")


@dataclass(frozen=True, slots=True)
class CitationEditPlan:
    source_state_id: int
    source_text: str
    final_text: str
    operations: tuple[ReplayOperation, ...]
    before_view: ViewState
    target_view: ViewState
    citation_text: str


def normalize_locator(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(part.strip() for part in value.splitlines() if part.strip()).lstrip(",").strip()


def format_pandoc_citation(key: object, locator: object = "") -> str:
    key_text = normalize_reference_key(key)
    if not is_valid_reference_key(key_text):
        raise CitationError("citation key is invalid")
    locator_text = normalize_locator(locator)
    if "]" in locator_text:
        raise CitationError("citation locator cannot contain a closing bracket")
    return f"[@{key_text}, {locator_text}]" if locator_text else f"[@{key_text}]"


def citation_lookup_at(text: str, offset: int) -> CitationLookup:
    if not isinstance(text, str):
        raise TypeError("citation source must be text")
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise TypeError("citation offset must be an integer")
    position = max(0, min(len(text), offset))
    for cluster in CitationDocumentMap.from_text(text).clusters:
        if not (cluster.source_start <= position < cluster.source_end):
            continue
        for item in cluster.items:
            if item.key_start - 1 <= position < item.key_end:
                return CitationLookup("unique", item.key, (item.key,), cluster)
        if len(cluster.keys) == 1:
            return CitationLookup("unique", cluster.keys[0], cluster.keys, cluster)
        return CitationLookup("ambiguous", None, cluster.keys, cluster)
    return CitationLookup("none")


def citation_diagnostics(
    document: CitationDocumentMap,
    records: Iterable[ReferenceRecord],
) -> tuple[CitationDiagnostic, ...]:
    if not isinstance(document, CitationDocumentMap):
        raise TypeError("document must be CitationDocumentMap")
    values = tuple(records)
    if any(not isinstance(record, ReferenceRecord) for record in values):
        raise TypeError("records must contain ReferenceRecord values")
    available = {record.key for record in values}
    result: list[CitationDiagnostic] = []
    for cluster in document.clusters:
        for item in cluster.items:
            if item.key not in available:
                result.append(
                    CitationDiagnostic(
                        "missing-reference",
                        item.key,
                        item.key_start,
                        f"Citation key is missing from the Reference Library: {item.key}",
                    )
                )
    return tuple(result)


def plan_insert_citation(
    *,
    source_text: str,
    source_state_id: int,
    before_view: ViewState,
    key: str,
    locator: str = "",
    records: Iterable[ReferenceRecord] = (),
) -> CitationEditPlan:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be text")
    if not isinstance(before_view, ViewState):
        raise TypeError("before_view must be ViewState")
    if int(source_state_id) <= 0:
        raise CitationError("source_state_id must be positive")
    if max(before_view.insert_offset, before_view.selection_bound_offset) > len(source_text):
        raise CitationError("view offset exceeds source text length")
    if before_view.insert_offset != before_view.selection_bound_offset:
        raise CitationError("citation insertion requires a collapsed caret")

    values = tuple(records)
    if values:
        keys = {record.key for record in values}
        if key not in keys:
            raise CitationError(f"reference key is not available: {key}")
    citation = format_pandoc_citation(key, locator)
    offset = before_view.insert_offset
    operation = ReplayOperation(EditKind.INSERT, offset, citation)
    if len(citation) > DEFAULT_MAX_HISTORY_PAYLOAD_CHARS:
        raise CitationError("citation exceeds Graphium's bounded Undo payload budget")
    final_text = source_text[:offset] + citation + source_text[offset:]
    ensure_interactive_text_renderable(final_text)
    target = ViewState(offset + len(citation), offset + len(citation))
    return CitationEditPlan(
        source_state_id,
        source_text,
        final_text,
        (operation,),
        before_view,
        target,
        citation,
    )
