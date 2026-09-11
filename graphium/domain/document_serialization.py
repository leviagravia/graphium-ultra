"""Strict, GTK-free document serialization policy for Graphium.

The editor representation is normalized to LF. Serialization restores the accepted
encoding/BOM/EOL profile at the byte boundary. No filesystem mutation occurs here.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass

from .document_identity import BomKind, DocumentFileState, LineEnding


class DocumentSerializationError(ValueError):
    """Editor text cannot be serialized losslessly under the selected profile."""


class MixedLineEndingConfirmationRequired(DocumentSerializationError):
    """A mixed-EOL source requires explicit normalization consent before saving."""


@dataclass(frozen=True)
class DocumentSerializationProfile:
    encoding: str
    bom: BomKind
    line_ending: LineEnding
    mixed_source: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.encoding, str) or not self.encoding:
            raise ValueError("encoding must be a non-empty string")
        if not isinstance(self.bom, BomKind):
            raise TypeError("bom must be BomKind")
        if not isinstance(self.line_ending, LineEnding):
            raise TypeError("line_ending must be LineEnding")


_USER_ENCODING_PROFILES = frozenset({
    ("utf-8", BomKind.NONE),
    ("utf-8", BomKind.UTF8),
    ("utf-16-le", BomKind.UTF16_LE),
    ("utf-16-be", BomKind.UTF16_BE),
    ("utf-32-le", BomKind.UTF32_LE),
    ("utf-32-be", BomKind.UTF32_BE),
})


def representation_with_encoding(
    profile: DocumentSerializationProfile, *, encoding: str, bom: BomKind
) -> DocumentSerializationProfile:
    """Return a user-selectable encoding profile without granting mixed-EOL consent."""
    if not isinstance(profile, DocumentSerializationProfile):
        raise TypeError("profile must be DocumentSerializationProfile")
    if (encoding, bom) not in _USER_ENCODING_PROFILES:
        raise ValueError("encoding/BOM profile is not a supported Graphium conversion target")
    return DocumentSerializationProfile(encoding, bom, profile.line_ending, profile.mixed_source)


def representation_with_line_ending(
    profile: DocumentSerializationProfile, *, line_ending: LineEnding
) -> DocumentSerializationProfile:
    """Return a concrete EOL target; this explicitly resolves any mixed-source condition."""
    if not isinstance(profile, DocumentSerializationProfile):
        raise TypeError("profile must be DocumentSerializationProfile")
    if line_ending not in (LineEnding.LF, LineEnding.CRLF, LineEnding.CR):
        raise ValueError("line ending must be LF, CRLF or CR")
    return DocumentSerializationProfile(profile.encoding, profile.bom, line_ending, False)


@dataclass(frozen=True)
class SerializedDocument:
    data: bytes
    profile: DocumentSerializationProfile


def profile_for_document(file_state: DocumentFileState | None) -> DocumentSerializationProfile:
    """Return Graphium's representation policy for an accepted document state."""
    if file_state is None:
        return DocumentSerializationProfile(
            encoding="utf-8",
            bom=BomKind.NONE,
            line_ending=LineEnding.LF,
            mixed_source=False,
        )
    eol = file_state.load.eol
    line_ending = eol.dominant
    if line_ending is LineEnding.NONE:
        # With no historical separator, LF is Graphium's default if editing later
        # introduces one.
        line_ending = LineEnding.LF
    return DocumentSerializationProfile(
        encoding=file_state.load.encoding,
        bom=file_state.load.bom,
        line_ending=line_ending,
        mixed_source=bool(eol.mixed),
    )


def _separator(kind: LineEnding) -> str:
    if kind is LineEnding.LF:
        return "\n"
    if kind is LineEnding.CRLF:
        return "\r\n"
    if kind is LineEnding.CR:
        return "\r"
    return "\n"


def _bom_bytes(kind: BomKind) -> bytes:
    return {
        BomKind.NONE: b"",
        BomKind.UTF8: codecs.BOM_UTF8,
        BomKind.UTF16_LE: codecs.BOM_UTF16_LE,
        BomKind.UTF16_BE: codecs.BOM_UTF16_BE,
        BomKind.UTF32_LE: codecs.BOM_UTF32_LE,
        BomKind.UTF32_BE: codecs.BOM_UTF32_BE,
    }[kind]


def serialize_document(
    text: str,
    profile: DocumentSerializationProfile,
    *,
    allow_mixed_eol_normalization: bool = False,
) -> SerializedDocument:
    """Serialize LF-normalized editor text to exact bytes without touching disk."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if "\x00" in text:
        raise DocumentSerializationError(
            "NUL characters are outside Graphium plain-text document scope."
        )
    if not isinstance(profile, DocumentSerializationProfile):
        raise TypeError("profile must be DocumentSerializationProfile")
    if profile.mixed_source and not allow_mixed_eol_normalization:
        raise MixedLineEndingConfirmationRequired(
            "This document was loaded with mixed line endings. Saving requires explicit "
            "confirmation because Graphium will normalize them to the dominant style."
        )

    separator = _separator(profile.line_ending)
    serialized_text = text if separator == "\n" else text.replace("\n", separator)
    try:
        payload = serialized_text.encode(profile.encoding, errors="strict")
    except (LookupError, UnicodeEncodeError) as exc:
        raise DocumentSerializationError(
            f"The document cannot be represented losslessly as {profile.encoding}."
        ) from exc

    return SerializedDocument(data=_bom_bytes(profile.bom) + payload, profile=profile)
