"""Cheap status projection with no document-wide scanning."""
from __future__ import annotations

from dataclasses import dataclass

from graphium.domain.document_identity import BomKind, LineEnding
from graphium.domain.document_serialization import DocumentSerializationProfile


@dataclass(frozen=True)
class CompactStatus:
    line: int
    column: int
    encoding: str
    eol: str
    saved_state: str

    @property
    def position_text(self) -> str:
        return f"Ln {self.line}, Col {self.column}"

    @property
    def document_text(self) -> str:
        return f"{self.encoding} · {self.eol} · {self.saved_state}"


def _encoding_label(profile: DocumentSerializationProfile) -> str:
    labels = {
        "utf-8": "UTF-8",
        "utf-16-le": "UTF-16 LE",
        "utf-16-be": "UTF-16 BE",
        "utf-32-le": "UTF-32 LE",
        "utf-32-be": "UTF-32 BE",
    }
    label = labels.get(profile.encoding.lower(), profile.encoding.upper())
    return f"{label} BOM" if profile.bom is not BomKind.NONE else label


def _eol_label(profile: DocumentSerializationProfile) -> str:
    label = {LineEnding.LF: "LF", LineEnding.CRLF: "CRLF", LineEnding.CR: "CR"}[
        profile.line_ending
    ]
    return f"Mixed EOL ({label})" if profile.mixed_source else label


def project_compact_status(
    *,
    line: int,
    column: int,
    representation_profile: DocumentSerializationProfile,
    modified: bool,
) -> CompactStatus:
    line = int(line)
    column = int(column)
    if line < 1 or column < 1:
        raise ValueError("line and column must be 1-based positive integers")
    if not isinstance(representation_profile, DocumentSerializationProfile):
        raise TypeError("representation_profile must be DocumentSerializationProfile")
    return CompactStatus(
        line=line,
        column=column,
        encoding=_encoding_label(representation_profile),
        eol=_eol_label(representation_profile),
        saved_state="Modified" if modified else "Saved",
    )
