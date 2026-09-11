"""Local human-readable bibliographic reference authority for Graphium Plus.

The canonical library is one UTF-8 Markdown file under Graphium Plus' XDG data
root.  This module owns bibliographic record semantics, deterministic parsing /
serialization, bounded on-demand lookup and conflict-aware persistence.  Physical
writes are delegated to Core's existing :class:`GuardedFileWriter`; no second
writer, database, indexer, watcher or background service is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Iterable, Protocol

from graphium.paths import resolve_xdg_paths


_HEADER = "# Graphium Plus References v1"
_KEY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9_])?$")
_FIELD_LABELS = (
    ("Type", "type"),
    ("Author", "authors"),
    ("Title", "title"),
    ("Year", "year"),
    ("Editor", "editors"),
    ("Container Title", "container_title"),
    ("Publisher", "publisher"),
    ("Location", "location"),
    ("Volume", "volume"),
    ("Issue", "issue"),
    ("Pages", "pages"),
    ("DOI", "doi"),
    ("ISBN", "isbn"),
    ("ISSN", "issn"),
    ("URL", "url"),
    ("Language", "language"),
)
_KNOWN_FIELDS = {label.casefold(): attribute for label, attribute in _FIELD_LABELS}


def normalize_reference_key(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def is_valid_reference_key(value: object) -> bool:
    key = normalize_reference_key(value)
    return bool(key and _KEY_RE.fullmatch(key))


def _single_line(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(part.strip() for part in value.splitlines() if part.strip()).strip()


def _many(values: object) -> tuple[str, ...]:
    source: Iterable[object]
    if isinstance(values, str):
        source = values.splitlines()
    elif isinstance(values, Iterable):
        source = values
    else:
        source = ()
    result: list[str] = []
    for value in source:
        clean = _single_line(value)
        if clean and clean not in result:
            result.append(clean)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    key: str
    title: str
    type: str = "other"
    authors: tuple[str, ...] = ()
    year: str = ""
    editors: tuple[str, ...] = ()
    container_title: str = ""
    publisher: str = ""
    location: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    isbn: str = ""
    issn: str = ""
    url: str = ""
    language: str = ""
    extra_fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        key = normalize_reference_key(self.key)
        title = _single_line(self.title)
        if not is_valid_reference_key(key):
            raise ValueError("reference key is invalid")
        if not title:
            raise ValueError("reference title is required")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "type", _single_line(self.type).casefold() or "other")
        object.__setattr__(self, "authors", _many(self.authors))
        object.__setattr__(self, "editors", _many(self.editors))
        for name in (
            "year", "container_title", "publisher", "location", "volume", "issue",
            "pages", "doi", "isbn", "issn", "url", "language",
        ):
            object.__setattr__(self, name, _single_line(getattr(self, name)))
        extras: list[tuple[str, str]] = []
        for item in self.extra_fields:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("extra reference fields must be (label, value) pairs")
            label, value = _single_line(item[0]), _single_line(item[1])
            if label:
                extras.append((label, value))
        object.__setattr__(self, "extra_fields", tuple(extras))

    @property
    def primary_author(self) -> str:
        return self.authors[0] if self.authors else ""

    @property
    def display_label(self) -> str:
        author = self.primary_author or "Unknown author"
        prefix = f"{author}, {self.year}" if self.year else author
        return f"{prefix} — {self.title} [{self.key}]"

    @property
    def search_text(self) -> str:
        return "\n".join(
            (
                self.key,
                self.title,
                self.type,
                self.year,
                *self.authors,
                *self.editors,
                self.container_title,
                self.publisher,
                self.location,
                self.doi,
                self.isbn,
                self.issn,
                self.url,
                self.language,
                *(f"{label} {value}" for label, value in self.extra_fields),
            )
        ).casefold()


@dataclass(frozen=True, slots=True)
class ReferenceDiagnostic:
    line: int
    kind: str
    message: str
    blocking: bool = True

    def __post_init__(self) -> None:
        if self.line < 1:
            raise ValueError("reference diagnostic line must be positive")
        if not self.kind or not self.message:
            raise ValueError("reference diagnostic fields must be non-empty")


@dataclass(frozen=True, slots=True)
class ReferenceFileToken:
    exists: bool
    size: int = 0
    sha256: str = ""

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError("reference token size cannot be negative")
        if self.exists and len(self.sha256) != 64:
            raise ValueError("existing reference token requires SHA-256")
        if not self.exists and (self.size or self.sha256):
            raise ValueError("absent reference token cannot carry content identity")


@dataclass(frozen=True, slots=True)
class ReferenceLibrarySnapshot:
    records: tuple[ReferenceRecord, ...]
    token: ReferenceFileToken
    diagnostics: tuple[ReferenceDiagnostic, ...] = ()

    @property
    def writable(self) -> bool:
        return not any(item.blocking for item in self.diagnostics)


@dataclass(frozen=True, slots=True)
class ReferenceSaveResult:
    status: str
    snapshot: ReferenceLibrarySnapshot
    message: str = ""

    @property
    def saved(self) -> bool:
        return self.status == "saved"


def default_reference_library_path(env: dict[str, str] | None = None) -> Path:
    return resolve_xdg_paths(env, namespace="graphium-plus").data / "references.md"


def reference_file_token(path: str | Path) -> ReferenceFileToken:
    target = Path(path)
    try:
        st = target.lstat()
    except FileNotFoundError:
        return ReferenceFileToken(False)
    if stat.S_ISLNK(st.st_mode):
        raise ValueError("reference library must not be a symbolic link")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("reference library must be a regular file")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return ReferenceFileToken(True, int(st.st_size), digest.hexdigest())


def serialize_reference_library(records: Iterable[ReferenceRecord]) -> str:
    values = tuple(records)
    if any(not isinstance(record, ReferenceRecord) for record in values):
        raise TypeError("records must contain ReferenceRecord values")
    seen: set[str] = set()
    for record in values:
        if record.key in seen:
            raise ValueError(f"duplicate reference key: {record.key}")
        seen.add(record.key)

    lines = [_HEADER, ""]
    for record in values:
        lines.extend((f"## {record.key}", ""))
        for label, attribute in _FIELD_LABELS:
            value = getattr(record, attribute)
            if attribute in {"authors", "editors"}:
                for item in value:
                    lines.append(f"{label}: {item}")
            else:
                lines.append(f"{label}: {value}")
        for label, value in record.extra_fields:
            if label.casefold() not in _KNOWN_FIELDS:
                lines.append(f"{label}: {value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_reference_library(
    text: object,
) -> tuple[tuple[ReferenceRecord, ...], tuple[ReferenceDiagnostic, ...]]:
    if not isinstance(text, str):
        return (), (ReferenceDiagnostic(1, "not-text", "Reference library is not text."),)
    lines = text.splitlines()
    diagnostics: list[ReferenceDiagnostic] = []
    first = next(((index + 1, line.strip()) for index, line in enumerate(lines) if line.strip()), None)
    if first is not None and first[1] != _HEADER:
        diagnostics.append(
            ReferenceDiagnostic(first[0], "header", f"Expected reference library header: {_HEADER}")
        )

    records: list[ReferenceRecord] = []
    seen: set[str] = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("## "):
            index += 1
            continue
        start_line = index + 1
        key = line[3:].strip()
        index += 1
        fields: dict[str, object] = {"authors": [], "editors": []}
        extras: list[tuple[str, str]] = []
        while index < len(lines) and not lines[index].startswith("## "):
            current = lines[index]
            if current.strip() and ":" in current:
                label, value = current.split(":", 1)
                clean_label = _single_line(label)
                clean_value = value.strip()
                attribute = _KNOWN_FIELDS.get(clean_label.casefold())
                if attribute in {"authors", "editors"}:
                    if clean_value:
                        fields[attribute].append(clean_value)  # type: ignore[union-attr]
                elif attribute:
                    fields[attribute] = clean_value
                elif clean_label:
                    extras.append((clean_label, clean_value))
            index += 1

        if not key:
            diagnostics.append(ReferenceDiagnostic(start_line, "missing-key", "Reference heading has no key."))
            continue
        if key in seen:
            diagnostics.append(
                ReferenceDiagnostic(start_line, "duplicate-key", f"Duplicate reference key: {key}")
            )
            continue
        try:
            record = ReferenceRecord(
                key=key,
                title=str(fields.get("title", "")),
                type=str(fields.get("type", "other")),
                authors=tuple(fields["authors"]),  # type: ignore[arg-type]
                year=str(fields.get("year", "")),
                editors=tuple(fields["editors"]),  # type: ignore[arg-type]
                container_title=str(fields.get("container_title", "")),
                publisher=str(fields.get("publisher", "")),
                location=str(fields.get("location", "")),
                volume=str(fields.get("volume", "")),
                issue=str(fields.get("issue", "")),
                pages=str(fields.get("pages", "")),
                doi=str(fields.get("doi", "")),
                isbn=str(fields.get("isbn", "")),
                issn=str(fields.get("issn", "")),
                url=str(fields.get("url", "")),
                language=str(fields.get("language", "")),
                extra_fields=tuple(extras),
            )
        except ValueError as exc:
            diagnostics.append(ReferenceDiagnostic(start_line, "invalid-record", f"{key}: {exc}"))
            continue
        seen.add(record.key)
        records.append(record)
    return tuple(records), tuple(diagnostics)


def search_references(
    records: Iterable[ReferenceRecord], query: str, *, limit: int = 50
) -> tuple[ReferenceRecord, ...]:
    values = tuple(records)
    if any(not isinstance(record, ReferenceRecord) for record in values):
        raise TypeError("records must contain ReferenceRecord values")
    if not isinstance(query, str):
        raise TypeError("reference query must be text")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 500:
        raise ValueError("reference search limit must be 1..500")
    tokens = tuple(part.casefold() for part in query.split() if part.strip())
    if not tokens:
        return values[:limit]

    ranked: list[tuple[tuple[int, int, str], ReferenceRecord]] = []
    for record in values:
        haystack = record.search_text
        if not all(token in haystack for token in tokens):
            continue
        key = record.key.casefold()
        title = record.title.casefold()
        author = record.primary_author.casefold()
        exact_key = 0 if query.casefold() == key else 1
        prefix = 0 if key.startswith(query.casefold()) else 1
        human_prefix = 0 if title.startswith(query.casefold()) or author.startswith(query.casefold()) else 1
        ranked.append(((exact_key, prefix + human_prefix, record.key.casefold()), record))
    ranked.sort(key=lambda item: item[0])
    return tuple(record for _score, record in ranked[:limit])


class ReferenceWriterPort(Protocol):
    """Narrow projection of Core's already-owned physical writer authority."""

    def observe_target(self, path: str): ...
    def commit(self, observation, data: bytes): ...


class MarkdownReferenceLibraryStore:
    """One canonical Markdown library using the Core-owned physical writer."""

    __slots__ = ("path", "_writer")

    def __init__(self, writer: ReferenceWriterPort, path: str | Path | None = None) -> None:
        if writer is None or not callable(getattr(writer, "observe_target", None)) or not callable(getattr(writer, "commit", None)):
            raise TypeError("writer must provide Core observe_target/commit authority")
        self.path = Path(path) if path is not None else default_reference_library_path()
        self._writer = writer

    def load(self) -> ReferenceLibrarySnapshot:
        try:
            token = reference_file_token(self.path)
        except (OSError, ValueError) as exc:
            return ReferenceLibrarySnapshot(
                (), ReferenceFileToken(False),
                (ReferenceDiagnostic(1, "unsafe-file", str(exc)),),
            )
        if not token.exists:
            return ReferenceLibrarySnapshot((), token, ())
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return ReferenceLibrarySnapshot(
                (), token, (ReferenceDiagnostic(1, "read-error", str(exc)),)
            )
        # Refuse a file that changed while it was being read.
        try:
            after = reference_file_token(self.path)
        except (OSError, ValueError) as exc:
            return ReferenceLibrarySnapshot(
                (), token, (ReferenceDiagnostic(1, "stale-read", str(exc)),)
            )
        if after != token:
            return ReferenceLibrarySnapshot(
                (), after,
                (ReferenceDiagnostic(1, "stale-read", "Reference library changed while loading."),),
            )
        records, diagnostics = parse_reference_library(raw)
        return ReferenceLibrarySnapshot(records, token, diagnostics)

    def save(
        self,
        records: Iterable[ReferenceRecord],
        expected_token: ReferenceFileToken,
    ) -> ReferenceSaveResult:
        if not isinstance(expected_token, ReferenceFileToken):
            raise TypeError("expected_token must be ReferenceFileToken")
        try:
            current = reference_file_token(self.path)
        except (OSError, ValueError) as exc:
            snapshot = ReferenceLibrarySnapshot(
                (), expected_token, (ReferenceDiagnostic(1, "unsafe-file", str(exc)),)
            )
            return ReferenceSaveResult("error", snapshot, str(exc))
        if current != expected_token:
            snapshot = self.load()
            return ReferenceSaveResult(
                "conflict", snapshot, "Reference library changed outside Graphium Plus."
            )
        values = tuple(records)
        try:
            payload = serialize_reference_library(values)
        except (TypeError, ValueError) as exc:
            snapshot = ReferenceLibrarySnapshot(values, current)
            return ReferenceSaveResult("error", snapshot, str(exc))

        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        try:
            observation = self._writer.observe_target(str(self.path))
            # Close the gap between the user-visible load token and writer observation.
            if reference_file_token(self.path) != expected_token:
                snapshot = self.load()
                return ReferenceSaveResult(
                    "conflict", snapshot, "Reference library changed before save."
                )
            self._writer.commit(observation, payload.encode("utf-8"))
        except Exception as exc:
            snapshot = self.load()
            return ReferenceSaveResult("error", snapshot, str(exc))
        snapshot = self.load()
        if snapshot.diagnostics:
            return ReferenceSaveResult(
                "error", snapshot, "Saved reference library did not reload cleanly."
            )
        return ReferenceSaveResult("saved", snapshot)
