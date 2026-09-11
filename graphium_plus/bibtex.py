"""Pure BibTeX/BibLaTeX interoperability for Graphium Plus.

`references.md` remains the sole persistent bibliographic authority.  This module
only converts text representations to/from :class:`ReferenceRecord` values.  It
owns no files, writer, database, index, subprocess, network access or GTK state.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import re
from typing import Iterable

from graphium_plus.references import ReferenceRecord, is_valid_reference_key


_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_:+.\-/]*")
_FIELD_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:+.\-/]*$")
_ENTRY_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:+.\-/]*$")

# BibTeX's conventional month macros.  Keeping the symbolic spelling would leak
# format-specific macro state into the Graphium authority, so imports normalize
# them to readable text.
_DEFAULT_MACROS = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April",
    "may": "May", "jun": "June", "jul": "July", "aug": "August",
    "sep": "September", "oct": "October", "nov": "November", "dec": "December",
}


@dataclass(frozen=True, slots=True)
class BibDiagnostic:
    line: int
    kind: str
    message: str
    blocking: bool = True

    def __post_init__(self) -> None:
        if self.line < 1:
            raise ValueError("Bib diagnostic line must be positive")
        if not self.kind or not self.message:
            raise ValueError("Bib diagnostic fields must be non-empty")


@dataclass(frozen=True, slots=True)
class BibImportResult:
    records: tuple[ReferenceRecord, ...]
    diagnostics: tuple[BibDiagnostic, ...] = ()

    @property
    def safe_to_apply(self) -> bool:
        return not any(item.blocking for item in self.diagnostics)


@dataclass(frozen=True, slots=True)
class BibExportResult:
    text: str
    diagnostics: tuple[BibDiagnostic, ...] = ()

    @property
    def complete(self) -> bool:
        return not any(item.blocking for item in self.diagnostics)


class _Scanner:
    __slots__ = ("text", "length", "pos", "newlines", "diagnostics", "macros")

    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)
        self.pos = 0
        self.newlines = tuple(index for index, char in enumerate(text) if char == "\n")
        self.diagnostics: list[BibDiagnostic] = []
        self.macros = dict(_DEFAULT_MACROS)

    def line(self, pos: int | None = None) -> int:
        where = self.pos if pos is None else max(0, min(pos, self.length))
        return bisect_right(self.newlines, where - 1) + 1

    def diagnostic(self, pos: int, kind: str, message: str, *, blocking: bool = True) -> None:
        self.diagnostics.append(BibDiagnostic(self.line(pos), kind, message, blocking))

    def _skip_space_comments(self) -> None:
        while self.pos < self.length:
            char = self.text[self.pos]
            if char.isspace():
                self.pos += 1
                continue
            if char == "%":
                newline = self.text.find("\n", self.pos + 1)
                self.pos = self.length if newline < 0 else newline + 1
                continue
            break

    def _identifier(self) -> str:
        self._skip_space_comments()
        match = _IDENT_RE.match(self.text, self.pos)
        if match is None:
            return ""
        self.pos = match.end()
        return match.group(0)

    def _consume_balanced(self, opener: str, closer: str) -> str | None:
        if self.pos >= self.length or self.text[self.pos] != opener:
            return None
        start = self.pos
        self.pos += 1
        depth = 1
        quote = False
        escaped = False
        while self.pos < self.length:
            char = self.text[self.pos]
            if escaped:
                escaped = False
                self.pos += 1
                continue
            if char == "\\":
                escaped = True
                self.pos += 1
                continue
            if char == '"' and opener != '"':
                quote = not quote
                self.pos += 1
                continue
            if not quote:
                if char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
                    if depth == 0:
                        value = self.text[start + 1:self.pos]
                        self.pos += 1
                        return value
            self.pos += 1
        self.diagnostic(start, "unterminated", f"Unterminated {opener}{closer} block.")
        return None

    def _quoted_atom(self) -> str | None:
        if self.pos >= self.length or self.text[self.pos] != '"':
            return None
        start = self.pos
        self.pos += 1
        out: list[str] = []
        escaped = False
        brace_depth = 0
        while self.pos < self.length:
            char = self.text[self.pos]
            if escaped:
                out.extend(("\\", char))
                escaped = False
                self.pos += 1
                continue
            if char == "\\":
                escaped = True
                self.pos += 1
                continue
            if char == "{":
                brace_depth += 1
                out.append(char)
                self.pos += 1
                continue
            if char == "}" and brace_depth:
                brace_depth -= 1
                out.append(char)
                self.pos += 1
                continue
            if char == '"' and brace_depth == 0:
                self.pos += 1
                return "".join(out)
            out.append(char)
            self.pos += 1
        self.diagnostic(start, "unterminated-string", "Unterminated quoted Bib value.")
        return None

    def _bare_atom(self, closer: str) -> tuple[str, bool]:
        start = self.pos
        while self.pos < self.length and self.text[self.pos] not in {",", "#", closer}:
            if self.text[self.pos] == "%":
                break
            self.pos += 1
        token = self.text[start:self.pos].strip()
        if not token:
            return "", False
        if token.isdigit():
            return token, False
        macro = self.macros.get(token.casefold())
        if macro is not None:
            return macro, False
        self.diagnostic(start, "unresolved-macro", f"Unresolved Bib macro: {token}")
        return token, True

    def _value(self, closer: str) -> tuple[str, bool] | None:
        pieces: list[str] = []
        unresolved = False
        while True:
            self._skip_space_comments()
            if self.pos >= self.length:
                return None
            char = self.text[self.pos]
            if char == "{":
                raw = self._consume_balanced("{", "}")
                if raw is None:
                    return None
                pieces.append(raw)
            elif char == '"':
                raw = self._quoted_atom()
                if raw is None:
                    return None
                pieces.append(raw)
            else:
                raw, atom_unresolved = self._bare_atom(closer)
                if not raw:
                    self.diagnostic(self.pos, "missing-value", "Expected a Bib field value.")
                    return None
                pieces.append(raw)
                unresolved = unresolved or atom_unresolved
            self._skip_space_comments()
            if self.pos < self.length and self.text[self.pos] == "#":
                self.pos += 1
                continue
            break
        return _normalize_bib_text("".join(pieces)), unresolved

    def _fields(self, closer: str) -> tuple[dict[str, str], int] | None:
        fields: dict[str, str] = {}
        entry_start = self.pos
        while self.pos < self.length:
            self._skip_space_comments()
            if self.pos < self.length and self.text[self.pos] == closer:
                self.pos += 1
                return fields, entry_start
            if self.pos < self.length and self.text[self.pos] == ",":
                self.pos += 1
                continue
            field_pos = self.pos
            name = self._identifier()
            if not name:
                self.diagnostic(field_pos, "malformed-field", "Expected a Bib field name.")
                self._recover_to_entry_end(closer)
                return None
            self._skip_space_comments()
            if self.pos >= self.length or self.text[self.pos] != "=":
                self.diagnostic(field_pos, "missing-equals", f"Expected '=' after Bib field {name}.")
                self._recover_to_entry_end(closer)
                return None
            self.pos += 1
            value = self._value(closer)
            if value is None:
                self._recover_to_entry_end(closer)
                return None
            field_name = name.casefold()
            if field_name in fields:
                self.diagnostic(field_pos, "duplicate-field", f"Duplicate Bib field: {name}")
            else:
                fields[field_name] = value[0]
            self._skip_space_comments()
            if self.pos < self.length and self.text[self.pos] == ",":
                self.pos += 1
        self.diagnostic(entry_start, "unterminated-entry", "Bib entry has no closing delimiter.")
        return None

    def _recover_to_entry_end(self, closer: str) -> None:
        depth = 0
        quote = False
        escaped = False
        while self.pos < self.length:
            char = self.text[self.pos]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = not quote
            elif not quote:
                if char == "{":
                    depth += 1
                elif char == "}" and depth:
                    depth -= 1
                elif char == closer and depth == 0:
                    self.pos += 1
                    return
            self.pos += 1


def _normalize_bib_text(value: str) -> str:
    """Normalize whitespace and remove only plain Bib case-protection groups.

    We deliberately do *not* implement TeX.  Braces that appear to belong to a
    control sequence (for example ``\\textit{Title}``) or contain a backslash
    are preserved so import never corrupts unknown LaTeX syntax merely to make
    the canonical library prettier.  Plain groups such as ``{DNA}`` are unwrapped
    for human-readable Graphium presentation.
    """

    def render(segment: str) -> str:
        out: list[str] = []
        index = 0
        while index < len(segment):
            char = segment[index]
            if char == "\\" and index + 1 < len(segment):
                out.append(segment[index:index + 2])
                index += 2
                continue
            if char != "{":
                out.append(char)
                index += 1
                continue

            depth = 1
            escaped = False
            end = index + 1
            while end < len(segment) and depth:
                current = segment[end]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                end += 1
            if depth:
                # Unbalanced input is preserved. The exporter will brace-escape
                # it rather than silently dropping syntax.
                out.append(segment[index:])
                break

            raw_inner = segment[index + 1:end - 1]
            inner = render(raw_inner)
            prefix = "".join(out)
            command_group = bool(re.search(r"\\(?:[A-Za-z]+|.)$", prefix))
            if command_group or "\\" in raw_inner:
                out.extend(("{", inner, "}"))
            else:
                out.append(inner)
            index = end
        return "".join(out)

    return " ".join(render(value).split())


def _split_people(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    result: list[str] = []
    start = 0
    depth = 0
    escaped = False
    index = 0
    lower = value.casefold()
    while index < len(value):
        char = value[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}" and depth:
            depth -= 1
            index += 1
            continue
        if depth == 0 and lower.startswith(" and ", index):
            person = _normalize_bib_text(value[start:index])
            if person:
                result.append(person)
            index += 5
            start = index
            continue
        index += 1
    person = _normalize_bib_text(value[start:])
    if person:
        result.append(person)
    return tuple(result)


def _set_mapped(
    mapped: dict[str, object], extras: list[tuple[str, str]], attribute: str,
    field_name: str, value: str, diagnostics: list[BibDiagnostic], line: int,
) -> None:
    previous = mapped.get(attribute)
    if previous not in (None, "", (), []) and previous != value:
        extras.append((field_name, value))
        diagnostics.append(
            BibDiagnostic(line, "field-conflict", f"Preserved conflicting Bib field: {field_name}", False)
        )
        return
    mapped[attribute] = value


def _record_from_fields(
    entry_type: str, key: str, fields: dict[str, str], *, line: int,
    diagnostics: list[BibDiagnostic],
) -> ReferenceRecord | None:
    if not is_valid_reference_key(key):
        diagnostics.append(BibDiagnostic(line, "invalid-key", f"Invalid citation key: {key}"))
        return None

    mapped: dict[str, object] = {"type": entry_type.casefold(), "authors": (), "editors": ()}
    extras: list[tuple[str, str]] = []
    for name, value in fields.items():
        if name == "author":
            mapped["authors"] = _split_people(value)
        elif name == "editor":
            mapped["editors"] = _split_people(value)
        elif name == "title":
            mapped["title"] = value
        elif name == "year":
            _set_mapped(mapped, extras, "year", name, value, diagnostics, line)
        elif name == "date":
            # BibLaTeX date can carry YYYY-MM-DD.  Graphium's current canonical
            # model owns only a human-readable year field, so retain full date as
            # an extra while deriving the leading year when needed.
            if not mapped.get("year"):
                match = re.match(r"^(\d{4})", value)
                if match:
                    mapped["year"] = match.group(1)
            extras.append((name, value))
        elif name in {"journal", "journaltitle", "booktitle"}:
            _set_mapped(mapped, extras, "container_title", name, value, diagnostics, line)
        elif name == "publisher":
            mapped["publisher"] = value
        elif name in {"address", "location"}:
            _set_mapped(mapped, extras, "location", name, value, diagnostics, line)
        elif name == "volume":
            mapped["volume"] = value
        elif name in {"number", "issue"}:
            _set_mapped(mapped, extras, "issue", name, value, diagnostics, line)
        elif name == "pages":
            mapped["pages"] = value
        elif name == "doi":
            mapped["doi"] = value
        elif name == "isbn":
            mapped["isbn"] = value
        elif name == "issn":
            mapped["issn"] = value
        elif name == "url":
            mapped["url"] = value
        elif name in {"language", "langid"}:
            _set_mapped(mapped, extras, "language", name, value, diagnostics, line)
        else:
            extras.append((name, value))
            if name in {"crossref", "xref", "xdata"}:
                diagnostics.append(
                    BibDiagnostic(
                        line,
                        "unresolved-inheritance",
                        f"Bib field '{name}' requires external inheritance that Graphium does not resolve.",
                    )
                )

    title = str(mapped.get("title", ""))
    if not title:
        diagnostics.append(BibDiagnostic(line, "missing-title", f"{key}: reference title is required"))
        return None
    try:
        return ReferenceRecord(
            key=key,
            title=title,
            type=str(mapped.get("type", "other")),
            authors=tuple(mapped.get("authors", ())),
            year=str(mapped.get("year", "")),
            editors=tuple(mapped.get("editors", ())),
            container_title=str(mapped.get("container_title", "")),
            publisher=str(mapped.get("publisher", "")),
            location=str(mapped.get("location", "")),
            volume=str(mapped.get("volume", "")),
            issue=str(mapped.get("issue", "")),
            pages=str(mapped.get("pages", "")),
            doi=str(mapped.get("doi", "")),
            isbn=str(mapped.get("isbn", "")),
            issn=str(mapped.get("issn", "")),
            url=str(mapped.get("url", "")),
            language=str(mapped.get("language", "")),
            extra_fields=tuple(extras),
        )
    except ValueError as exc:
        diagnostics.append(BibDiagnostic(line, "invalid-record", f"{key}: {exc}"))
        return None


def import_bibliography(text: object) -> BibImportResult:
    """Parse BibTeX/BibLaTeX text into canonical reference records.

    The function is pure and intentionally does not merge into or save the
    Graphium reference library.  Conflicts and unresolved Bib inheritance/macros
    remain explicit diagnostics for the later UI/controller layer.
    """
    if not isinstance(text, str):
        return BibImportResult((), (BibDiagnostic(1, "not-text", "Bibliography input is not text."),))
    scanner = _Scanner(text)
    records: list[ReferenceRecord] = []
    seen: set[str] = set()

    while scanner.pos < scanner.length:
        scanner._skip_space_comments()
        if scanner.pos >= scanner.length:
            break
        if scanner.text[scanner.pos] != "@":
            next_at = scanner.text.find("@", scanner.pos + 1)
            scanner.diagnostic(scanner.pos, "stray-text", "Ignored text outside a Bib entry.", blocking=False)
            scanner.pos = scanner.length if next_at < 0 else next_at
            continue
        entry_pos = scanner.pos
        scanner.pos += 1
        entry_type = scanner._identifier().casefold()
        if not entry_type:
            scanner.diagnostic(entry_pos, "missing-entry-type", "Expected a Bib entry type after '@'.")
            continue
        scanner._skip_space_comments()
        if scanner.pos >= scanner.length or scanner.text[scanner.pos] not in "{(":
            scanner.diagnostic(entry_pos, "missing-entry-open", f"@{entry_type} has no opening delimiter.")
            continue
        opener = scanner.text[scanner.pos]
        closer = "}" if opener == "{" else ")"
        scanner.pos += 1

        if entry_type == "comment":
            scanner._recover_to_entry_end(closer)
            continue
        if entry_type == "preamble":
            scanner._recover_to_entry_end(closer)
            scanner.diagnostic(entry_pos, "ignored-preamble", "Ignored Bib preamble.", blocking=False)
            continue
        if entry_type == "string":
            scanner._skip_space_comments()
            macro_pos = scanner.pos
            macro_name = scanner._identifier().casefold()
            scanner._skip_space_comments()
            if not macro_name or scanner.pos >= scanner.length or scanner.text[scanner.pos] != "=":
                scanner.diagnostic(macro_pos, "malformed-string", "Malformed @string definition.")
                scanner._recover_to_entry_end(closer)
                continue
            scanner.pos += 1
            parsed = scanner._value(closer)
            scanner._skip_space_comments()
            if parsed is None:
                scanner._recover_to_entry_end(closer)
                continue
            if parsed[1]:
                scanner.diagnostic(macro_pos, "unresolved-string", f"@string {macro_name} depends on an unresolved macro.")
            else:
                scanner.macros[macro_name] = parsed[0]
            if scanner.pos < scanner.length and scanner.text[scanner.pos] == ",":
                scanner.pos += 1
                scanner._skip_space_comments()
            if scanner.pos < scanner.length and scanner.text[scanner.pos] == closer:
                scanner.pos += 1
            else:
                scanner.diagnostic(entry_pos, "malformed-string", "@string has unexpected trailing content.")
                scanner._recover_to_entry_end(closer)
            continue

        key_start = scanner.pos
        comma = scanner.text.find(",", scanner.pos)
        close = scanner.text.find(closer, scanner.pos)
        if comma < 0 or (close >= 0 and close < comma):
            scanner.diagnostic(entry_pos, "missing-key-comma", f"@{entry_type} entry has no key/field separator.")
            scanner._recover_to_entry_end(closer)
            continue
        key = scanner.text[key_start:comma].strip()
        scanner.pos = comma + 1
        parsed_fields = scanner._fields(closer)
        if parsed_fields is None:
            continue
        fields, _ = parsed_fields
        line = scanner.line(entry_pos)
        record = _record_from_fields(entry_type, key, fields, line=line, diagnostics=scanner.diagnostics)
        if record is None:
            continue
        if record.key in seen:
            scanner.diagnostics.append(BibDiagnostic(line, "duplicate-key", f"Duplicate citation key: {record.key}"))
            continue
        seen.add(record.key)
        records.append(record)

    return BibImportResult(tuple(records), tuple(scanner.diagnostics))


def _balanced_braces(value: str) -> bool:
    depth = 0
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _braced(value: str) -> str:
    clean = " ".join(value.split())
    if not _balanced_braces(clean):
        clean = clean.replace("{", r"\{").replace("}", r"\}")
    return "{" + clean + "}"


def _extra_field_name(label: str) -> str:
    candidate = label.strip().casefold().replace(" ", "_")
    return candidate if _FIELD_IDENT_RE.fullmatch(candidate) else ""


def export_bibliography(
    records: Iterable[ReferenceRecord], *, flavor: str = "biblatex"
) -> BibExportResult:
    """Serialize canonical references as deterministic BibTeX or BibLaTeX text."""
    values = tuple(records)
    if any(not isinstance(record, ReferenceRecord) for record in values):
        raise TypeError("records must contain ReferenceRecord values")
    normalized = flavor.casefold() if isinstance(flavor, str) else ""
    if normalized not in {"bibtex", "biblatex"}:
        raise ValueError("flavor must be 'bibtex' or 'biblatex'")

    diagnostics: list[BibDiagnostic] = []
    seen: set[str] = set()
    chunks: list[str] = []
    for index, record in enumerate(values, 1):
        if record.key in seen:
            diagnostics.append(BibDiagnostic(index, "duplicate-key", f"Duplicate citation key: {record.key}"))
            continue
        seen.add(record.key)
        entry_type = record.type if _ENTRY_TYPE_RE.fullmatch(record.type) else "misc"
        if entry_type != record.type:
            diagnostics.append(
                BibDiagnostic(index, "normalized-type", f"Exported unsupported entry type '{record.type}' as misc.", False)
            )

        fields: list[tuple[str, str]] = []
        if record.authors:
            fields.append(("author", " and ".join(record.authors)))
        if record.editors:
            fields.append(("editor", " and ".join(record.editors)))
        fields.append(("title", record.title))
        if record.year:
            fields.append(("year", record.year))
        if record.container_title:
            if entry_type == "article":
                fields.append(("journaltitle" if normalized == "biblatex" else "journal", record.container_title))
            else:
                fields.append(("booktitle", record.container_title))
        if record.publisher:
            fields.append(("publisher", record.publisher))
        if record.location:
            fields.append(("location" if normalized == "biblatex" else "address", record.location))
        if record.volume:
            fields.append(("volume", record.volume))
        if record.issue:
            fields.append(("number", record.issue))
        if record.pages:
            fields.append(("pages", record.pages))
        if record.doi:
            fields.append(("doi", record.doi))
        if record.isbn:
            fields.append(("isbn", record.isbn))
        if record.issn:
            fields.append(("issn", record.issn))
        if record.url:
            fields.append(("url", record.url))
        if record.language:
            fields.append(("langid" if normalized == "biblatex" else "language", record.language))

        emitted = {name for name, _value in fields}
        for label, value in record.extra_fields:
            name = _extra_field_name(label)
            if not name:
                diagnostics.append(
                    BibDiagnostic(index, "unexportable-extra-field", f"Skipped extra field with invalid Bib name: {label}")
                )
                continue
            if name in emitted:
                diagnostics.append(
                    BibDiagnostic(index, "duplicate-export-field", f"Skipped duplicate exported field: {name}", False)
                )
                continue
            emitted.add(name)
            fields.append((name, value))

        lines = [f"@{entry_type}{{{record.key},"]
        for name, value in fields:
            if value:
                lines.append(f"  {name} = {_braced(value)},")
        lines.append("}")
        chunks.append("\n".join(lines))

    return BibExportResult("\n\n".join(chunks) + ("\n" if chunks else ""), tuple(diagnostics))


@dataclass(frozen=True, slots=True)
class BibImportConflict:
    """One explicit whole-record key conflict for the A4B import workflow."""

    key: str
    existing: ReferenceRecord
    incoming: ReferenceRecord


@dataclass(frozen=True, slots=True)
class BibImportAnalysis:
    """Pure preview of how imported records relate to the canonical library."""

    new_records: tuple[ReferenceRecord, ...]
    identical_records: tuple[ReferenceRecord, ...]
    conflicts: tuple[BibImportConflict, ...]


@dataclass(frozen=True, slots=True)
class BibImportPlan:
    """Explicit, deterministic whole-record import result; never an implicit merge."""

    records: tuple[ReferenceRecord, ...]
    added_keys: tuple[str, ...]
    replaced_keys: tuple[str, ...]
    unchanged_keys: tuple[str, ...]


def analyze_bibliography_import(
    existing_records: Iterable[ReferenceRecord],
    incoming_records: Iterable[ReferenceRecord],
) -> BibImportAnalysis:
    """Classify imported records without mutating or merging either collection."""
    existing = tuple(existing_records)
    incoming = tuple(incoming_records)
    if any(not isinstance(record, ReferenceRecord) for record in existing + incoming):
        raise TypeError("bibliography import values must be ReferenceRecord instances")
    existing_by_key = {record.key: record for record in existing}
    if len(existing_by_key) != len(existing):
        raise ValueError("existing reference library contains duplicate keys")
    incoming_keys = {record.key for record in incoming}
    if len(incoming_keys) != len(incoming):
        raise ValueError("incoming bibliography contains duplicate keys")

    new_records: list[ReferenceRecord] = []
    identical: list[ReferenceRecord] = []
    conflicts: list[BibImportConflict] = []
    for record in incoming:
        current = existing_by_key.get(record.key)
        if current is None:
            new_records.append(record)
        elif current == record:
            identical.append(record)
        else:
            conflicts.append(BibImportConflict(record.key, current, record))
    return BibImportAnalysis(tuple(new_records), tuple(identical), tuple(conflicts))


def plan_bibliography_import(
    existing_records: Iterable[ReferenceRecord],
    incoming_records: Iterable[ReferenceRecord],
    *,
    conflict_policy: str,
) -> BibImportPlan:
    """Apply an explicit whole-record conflict policy to canonical records.

    `keep-existing` imports only new keys; `replace-existing` replaces entire
    conflicting records in-place.  No field-by-field or automatic merge exists.
    """
    existing = tuple(existing_records)
    incoming = tuple(incoming_records)
    analysis = analyze_bibliography_import(existing, incoming)
    policy = conflict_policy.casefold() if isinstance(conflict_policy, str) else ""
    if policy not in {"keep-existing", "replace-existing"}:
        raise ValueError("conflict_policy must be 'keep-existing' or 'replace-existing'")

    incoming_by_key = {record.key: record for record in incoming}
    conflict_keys = {item.key for item in analysis.conflicts}
    if policy == "replace-existing":
        result = tuple(
            incoming_by_key.get(record.key, record) if record.key in conflict_keys else record
            for record in existing
        ) + analysis.new_records
        replaced = tuple(item.key for item in analysis.conflicts)
    else:
        result = existing + analysis.new_records
        replaced = ()
    return BibImportPlan(
        records=result,
        added_keys=tuple(record.key for record in analysis.new_records),
        replaced_keys=replaced,
        unchanged_keys=tuple(record.key for record in analysis.identical_records)
        + (() if policy == "replace-existing" else tuple(item.key for item in analysis.conflicts)),
    )
