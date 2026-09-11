"""Stable byte-oriented local document loader for Graphium.

Loading and Properties share one strong read-only filesystem observation primitive.
"""
from __future__ import annotations

import codecs
import os  # retained as shared monkeypatch seam for hostile-read regression tests
import re

from graphium.domain.document_identity import (
    BomKind,
    DocumentFileState,
    DocumentLoadMetadata,
    DocumentLoadResult,
    LineEnding,
    LineEndingProfile,
    UnsupportedDocumentContentError,
    UnsupportedDocumentEncodingError,
)
from graphium.domain.document_observation import ObservedDocumentBytes
from graphium.infrastructure.document_observer import observe_document, normalize_logical_path

DEFAULT_LARGE_FILE_BYTES = 1_000_000
_EOL_RE = re.compile(r"\r\n|\r|\n")

def _decode_bytes(raw: bytes) -> tuple[str, str, BomKind]:
    candidates = (
        (codecs.BOM_UTF32_LE, "utf-32-le", BomKind.UTF32_LE),
        (codecs.BOM_UTF32_BE, "utf-32-be", BomKind.UTF32_BE),
        (codecs.BOM_UTF8, "utf-8", BomKind.UTF8),
        (codecs.BOM_UTF16_LE, "utf-16-le", BomKind.UTF16_LE),
        (codecs.BOM_UTF16_BE, "utf-16-be", BomKind.UTF16_BE),
    )
    payload = raw
    encoding = "utf-8"
    bom = BomKind.NONE
    for marker, codec_name, kind in candidates:
        if raw.startswith(marker):
            payload = raw[len(marker):]
            encoding = codec_name
            bom = kind
            break
    try:
        return payload.decode(encoding, errors="strict"), encoding, bom
    except UnicodeDecodeError as exc:
        raise UnsupportedDocumentEncodingError(
            f"Unsupported or invalid document encoding ({encoding})"
        ) from exc


def _line_ending_profile(text: str) -> LineEndingProfile:
    counts = {LineEnding.LF: 0, LineEnding.CRLF: 0, LineEnding.CR: 0}
    first_index: dict[LineEnding, int | None] = {
        LineEnding.LF: None,
        LineEnding.CRLF: None,
        LineEnding.CR: None,
    }
    mapping = {"\n": LineEnding.LF, "\r\n": LineEnding.CRLF, "\r": LineEnding.CR}

    for match in _EOL_RE.finditer(text):
        kind = mapping[match.group(0)]
        counts[kind] += 1
        if first_index[kind] is None:
            first_index[kind] = match.start()

    present = [kind for kind, count in counts.items() if count]
    if not present:
        dominant = LineEnding.NONE
    else:
        maximum = max(counts[kind] for kind in present)
        tied = [kind for kind in present if counts[kind] == maximum]
        dominant = min(tied, key=lambda kind: int(first_index[kind]))

    return LineEndingProfile(
        dominant=dominant,
        mixed=len(present) > 1,
        final_newline=text.endswith(("\r\n", "\n", "\r")),
        lf_count=counts[LineEnding.LF],
        crlf_count=counts[LineEnding.CRLF],
        cr_count=counts[LineEnding.CR],
    )


def load_document(
    path: str,
    *,
    retries: int = 1,
    large_file_threshold: int = DEFAULT_LARGE_FILE_BYTES,
) -> DocumentLoadResult:
    """Load one local Graphium document under the current visit contract."""
    if not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a non-negative integer")
    if not isinstance(large_file_threshold, int) or large_file_threshold <= 0:
        raise ValueError("large_file_threshold must be a positive integer")

    observed = observe_document(path, capture_bytes=True, retries=retries)
    assert isinstance(observed, ObservedDocumentBytes)
    raw = observed.raw
    strong = observed.observation
    decoded, encoding, bom = _decode_bytes(raw)
    if "\x00" in decoded:
        raise UnsupportedDocumentContentError(
            "The selected file contains NUL characters and is outside Graphium plain-text scope"
        )

    eol = _line_ending_profile(decoded)
    normalized_text = decoded.replace("\r\n", "\n").replace("\r", "\n")
    file_state = DocumentFileState(
        binding=strong.binding,
        load=DocumentLoadMetadata(encoding=encoding, bom=bom, eol=eol),
        disk=strong.disk,
        content_fingerprint=strong.content_fingerprint,
    )
    return DocumentLoadResult(
        text=normalized_text,
        file_state=file_state,
        large_file=len(raw) >= large_file_threshold,
    )
