"""Pure frozen-plan authority for explicit Graphium Plus Pandoc output.

The current editor buffer and ``references.md`` remain authoritative inputs.
This module owns no GTK, files, subprocesses, writer, database, index or
persistent export settings.  It only validates a small output surface and
freezes one immutable derived Pandoc plan.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable

from graphium.domain.edit_history import DEFAULT_MAX_HISTORY_PAYLOAD_CHARS
from graphium_plus.bibtex import export_bibliography
from graphium_plus.citations import CitationDocumentMap, citation_diagnostics
from graphium_plus.markdown import MarkdownBlockKind, MarkdownInlineKind, build_markdown_document_map
from graphium_plus.references import ReferenceFileToken, ReferenceRecord


FORMAT_HTML = "html"
FORMAT_DOCX = "docx"
FORMAT_ODT = "odt"
FORMAT_LATEX = "latex"

_REMOTE_MARKDOWN_MEDIA_RE = re.compile(r"!\[[^\]\n]*\]\(\s*<?https?://", re.IGNORECASE)
_REMOTE_HTML_MEDIA_RE = re.compile(
    r"<(?:img|audio|video|source)\b[^>]*\b(?:src|poster)\s*=\s*[\"']?https?://",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PandocIdentity:
    path: str
    version: str
    version_parts: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.path or not self.version or not self.version_parts:
            raise ValueError("Pandoc identity is incomplete")
        if any(not isinstance(part, int) or isinstance(part, bool) or part < 0 for part in self.version_parts):
            raise ValueError("Pandoc version parts are invalid")


@dataclass(frozen=True, slots=True)
class PandocFormat:
    id: str
    label: str
    extension: str
    writer: str
    binary: bool

    def __post_init__(self) -> None:
        if not self.id or not self.label or not self.extension.startswith(".") or not self.writer:
            raise ValueError("Pandoc format descriptor is incomplete")


_FORMATS = (
    PandocFormat(FORMAT_HTML, "HTML", ".html", "html5", False),
    PandocFormat(FORMAT_DOCX, "Microsoft Word", ".docx", "docx", True),
    PandocFormat(FORMAT_ODT, "OpenDocument Text", ".odt", "odt", True),
    PandocFormat(FORMAT_LATEX, "LaTeX source", ".tex", "latex", False),
)
_FORMAT_BY_ID = {item.id: item for item in _FORMATS}


@dataclass(frozen=True, slots=True)
class PandocExportPlan:
    identity: PandocIdentity
    format: PandocFormat
    destination: str
    document_text: str
    document_sha256: str
    source_state_id: int
    document_directory: str
    reference_token: ReferenceFileToken
    reference_records: tuple[ReferenceRecord, ...]
    cited_keys: tuple[str, ...]
    bibliography_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_state_id, int) or isinstance(self.source_state_id, bool) or self.source_state_id < 0:
            raise ValueError("source_state_id must be a non-negative integer")
        if hashlib.sha256(self.document_text.encode("utf-8")).hexdigest() != self.document_sha256:
            raise ValueError("Pandoc plan document digest is inconsistent")
        if self.cited_keys != tuple(record.key for record in self.reference_records):
            raise ValueError("Pandoc cited keys must match reference record order")
        if bool(self.cited_keys) != bool(self.bibliography_text):
            raise ValueError("Pandoc bibliography projection does not match cited-reference state")


class PandocPlanError(ValueError):
    """The current source/reference state cannot enter the Pandoc output lane."""


def pandoc_formats() -> tuple[PandocFormat, ...]:
    return _FORMATS


def pandoc_format(format_id: object) -> PandocFormat:
    key = format_id.casefold() if isinstance(format_id, str) else ""
    try:
        return _FORMAT_BY_ID[key]
    except KeyError as exc:
        raise PandocPlanError("Choose a supported Pandoc output format.") from exc


def _excluded_code_ranges(text: str) -> tuple[tuple[int, int], ...]:
    markdown = build_markdown_document_map(text)
    ranges = [
        (block.source_start, block.source_end)
        for block in markdown.blocks
        if block.kind is MarkdownBlockKind.FENCED_CODE
    ]
    ranges.extend(
        (span.source_start, span.source_end)
        for span in markdown.inline_spans
        if span.kind is MarkdownInlineKind.INLINE_CODE
    )
    ranges.sort()
    return tuple(ranges)


def reject_remote_media(text: str) -> None:
    """Reject media sources that could make external Pandoc access the network.

    Ordinary hyperlinks remain valid.  Existing Plus Markdown structural ranges
    are the only code-exclusion authority; this function does not implement a
    second Markdown scanner.
    """
    if not isinstance(text, str):
        raise TypeError("document text must be a string")
    excluded = _excluded_code_ranges(text)
    starts = tuple(start for start, _end in excluded)

    def hidden(offset: int) -> bool:
        if not starts:
            return False
        index = bisect_right(starts, offset) - 1
        if index < 0:
            return False
        start, end = excluded[index]
        return start <= offset < end

    for pattern in (_REMOTE_MARKDOWN_MEDIA_RE, _REMOTE_HTML_MEDIA_RE):
        if any(not hidden(match.start()) for match in pattern.finditer(text)):
            raise PandocPlanError(
                "Remote images or media are not allowed in Pandoc output; use a local file instead."
            )


def _selected_cited_records(
    text: str, records: tuple[ReferenceRecord, ...]
) -> tuple[tuple[str, ...], tuple[ReferenceRecord, ...]]:
    document = CitationDocumentMap.from_text(text)
    diagnostics = citation_diagnostics(document, records)
    if diagnostics:
        first = diagnostics[0]
        raise PandocPlanError(first.message)
    by_key = {record.key: record for record in records}
    keys = document.cited_keys
    return keys, tuple(by_key[key] for key in keys)


def prepare_pandoc_export_plan(
    *,
    identity: PandocIdentity,
    format_id: str,
    destination: str | Path,
    document_text: str,
    source_state_id: int,
    document_directory: str | Path | None,
    reference_records: Iterable[ReferenceRecord],
    reference_token: ReferenceFileToken,
) -> PandocExportPlan:
    """Freeze one current-buffer document export without mutating authorities."""
    if not isinstance(identity, PandocIdentity):
        raise TypeError("identity must be PandocIdentity")
    if not isinstance(document_text, str):
        raise TypeError("document_text must be a string")
    if len(document_text) > DEFAULT_MAX_HISTORY_PAYLOAD_CHARS:
        raise PandocPlanError(
            f"Pandoc output is limited to {DEFAULT_MAX_HISTORY_PAYLOAD_CHARS:,} document characters."
        )
    if not isinstance(reference_token, ReferenceFileToken):
        raise TypeError("reference_token must be ReferenceFileToken")
    records = tuple(reference_records)
    if any(not isinstance(record, ReferenceRecord) for record in records):
        raise TypeError("reference_records must contain ReferenceRecord values")
    if len({record.key for record in records}) != len(records):
        raise PandocPlanError("Reference Library contains duplicate keys.")

    descriptor = pandoc_format(format_id)
    target = Path(destination).expanduser()
    if not str(target):
        raise PandocPlanError("Choose an output destination.")
    if target.suffix.casefold() != descriptor.extension:
        raise PandocPlanError(
            f"{descriptor.label} output requires the {descriptor.extension} extension."
        )
    target = target.absolute()

    directory = ""
    if document_directory is not None and str(document_directory):
        # Freeze only the requested cwd here. Availability is an execution-time
        # filesystem property and belongs to the process adapter, not this pure plan.
        directory = str(Path(document_directory).expanduser().absolute())

    reject_remote_media(document_text)
    cited_keys, cited_records = _selected_cited_records(document_text, records)
    bibliography_text = ""
    if cited_records:
        exported = export_bibliography(cited_records, flavor="biblatex")
        if not exported.complete:
            first = next(item for item in exported.diagnostics if item.blocking)
            raise PandocPlanError(first.message)
        bibliography_text = exported.text

    digest = hashlib.sha256(document_text.encode("utf-8")).hexdigest()
    return PandocExportPlan(
        identity=identity,
        format=descriptor,
        destination=str(target),
        document_text=document_text,
        document_sha256=digest,
        source_state_id=source_state_id,
        document_directory=directory,
        reference_token=reference_token,
        reference_records=cited_records,
        cited_keys=cited_keys,
        bibliography_text=bibliography_text,
    )


def pandoc_plan_matches_current_source(
    plan: PandocExportPlan,
    *,
    current_text: str,
    current_state_id: int,
    current_reference_token: ReferenceFileToken,
) -> tuple[bool, str]:
    """Revalidate frozen semantic inputs before final publication.

    Target-path revalidation remains owned by Core GuardedFileWriter because only
    that authority can compare and commit filesystem observations safely.
    """
    if not isinstance(plan, PandocExportPlan):
        raise TypeError("plan must be PandocExportPlan")
    if not isinstance(current_text, str):
        raise TypeError("current_text must be a string")
    if not isinstance(current_state_id, int) or isinstance(current_state_id, bool):
        raise TypeError("current_state_id must be an integer")
    if not isinstance(current_reference_token, ReferenceFileToken):
        raise TypeError("current_reference_token must be ReferenceFileToken")
    if current_state_id != plan.source_state_id:
        return False, "The document changed while Pandoc output was being generated."
    digest = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
    if digest != plan.document_sha256 or current_text != plan.document_text:
        return False, "The document changed while Pandoc output was being generated."
    if current_reference_token != plan.reference_token:
        return False, "The Reference Library changed while Pandoc output was being generated."
    return True, ""
