"""Immutable active-document identity and accepted-load metadata for Graphium.

Derived by selective adaptation from Calamus W116 document identity semantics.
This module is pure domain code: no filesystem I/O and no GTK dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BomKind(str, Enum):
    NONE = "none"
    UTF8 = "utf-8"
    UTF16_LE = "utf-16-le"
    UTF16_BE = "utf-16-be"
    UTF32_LE = "utf-32-le"
    UTF32_BE = "utf-32-be"


class LineEnding(str, Enum):
    NONE = "none"
    LF = "lf"
    CRLF = "crlf"
    CR = "cr"


@dataclass(frozen=True)
class FileObjectIdentity:
    """Local filesystem object evidence; never content identity."""

    device: int
    inode: int


@dataclass(frozen=True)
class DocumentFileBinding:
    """Logical path plus separately observed physical filesystem identity."""

    logical_path: str
    canonical_path: str | None
    object_id: FileObjectIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.logical_path, str) or not self.logical_path:
            raise ValueError("logical_path must be a non-empty string")
        if self.canonical_path is not None and not isinstance(self.canonical_path, str):
            raise TypeError("canonical_path must be a string or None")
        if self.object_id is not None and not isinstance(self.object_id, FileObjectIdentity):
            raise TypeError("object_id must be FileObjectIdentity or None")


@dataclass(frozen=True)
class LineEndingProfile:
    dominant: LineEnding
    mixed: bool
    final_newline: bool
    lf_count: int = 0
    crlf_count: int = 0
    cr_count: int = 0


@dataclass(frozen=True)
class DocumentLoadMetadata:
    encoding: str
    bom: BomKind
    eol: LineEndingProfile


@dataclass(frozen=True)
class DiskObservation:
    size: int
    mtime_ns: int
    mode: int
    read_only: bool
    ctime_ns: int | None = None
    uid: int | None = None
    gid: int | None = None
    nlink: int | None = None


@dataclass(frozen=True)
class ContentFingerprint:
    algorithm: str
    hex_digest: str


@dataclass(frozen=True)
class DocumentFileState:
    binding: DocumentFileBinding
    load: DocumentLoadMetadata
    disk: DiskObservation
    content_fingerprint: ContentFingerprint


@dataclass(frozen=True)
class DocumentLoadResult:
    text: str
    file_state: DocumentFileState
    large_file: bool
    warnings: tuple[str, ...] = ()

    @property
    def target_path(self) -> str:
        return self.file_state.binding.logical_path


class DocumentLoadError(ValueError):
    """Base class for typed Graphium document-load failures."""


class UnsupportedDocumentTypeError(DocumentLoadError):
    """The selected target is not a regular local file suitable for visiting."""


class UnstableDocumentLoadError(DocumentLoadError):
    """The filesystem object changed while Graphium was reading it."""


class UnsupportedDocumentEncodingError(DocumentLoadError, UnicodeError):
    """The selected document cannot be decoded by the supported codec policy."""


class UnsupportedDocumentContentError(DocumentLoadError):
    """Decoded content is outside Graphium's plain-text scope."""
