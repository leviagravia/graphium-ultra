"""Bounded local-image resolution for the native Markdown Viewer.

This module consumes image targets already produced by MarkdownDocumentMap.  It
never parses Markdown and never loads pixels; GTK/GdkPixbuf decoding belongs to
the presentation adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import stat
from urllib.parse import unquote, urlsplit


MAX_IMAGE_FILE_BYTES = 32 * 1024 * 1024


class MarkdownViewerImageStatus(str, Enum):
    READY = "ready"
    EMPTY = "empty"
    REMOTE_OR_URI = "remote-or-uri"
    UNBOUND_RELATIVE = "unbound-relative"
    INVALID_PATH = "invalid-path"
    MISSING = "missing"
    NOT_REGULAR = "not-regular"
    TOO_LARGE = "too-large"


@dataclass(frozen=True, slots=True)
class MarkdownViewerImageResolution:
    status: MarkdownViewerImageStatus
    target: str
    path: str | None = None

    @property
    def ready(self) -> bool:
        return self.status is MarkdownViewerImageStatus.READY and self.path is not None


def resolve_markdown_viewer_image(
    target: str | None,
    *,
    document_path: str | None,
) -> MarkdownViewerImageResolution:
    """Resolve one already-parsed Markdown image target to a bounded local file."""
    raw = "" if target is None else str(target).strip()
    if not raw:
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.EMPTY, raw)
    if raw.startswith("//"):
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.REMOTE_OR_URI, raw)

    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.REMOTE_OR_URI, raw)
    try:
        local = unquote(parts.path)
    except Exception:
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.INVALID_PATH, raw)
    if not local or "\x00" in local:
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.INVALID_PATH, raw)

    if os.path.isabs(local):
        candidate = os.path.abspath(local)
    else:
        if not document_path:
            return MarkdownViewerImageResolution(MarkdownViewerImageStatus.UNBOUND_RELATIVE, raw)
        candidate = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(document_path)), local))

    try:
        info = os.stat(candidate)
    except (OSError, ValueError):
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.MISSING, raw, candidate)
    if not stat.S_ISREG(info.st_mode):
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.NOT_REGULAR, raw, candidate)
    if info.st_size > MAX_IMAGE_FILE_BYTES:
        return MarkdownViewerImageResolution(MarkdownViewerImageStatus.TOO_LARGE, raw, candidate)
    return MarkdownViewerImageResolution(MarkdownViewerImageStatus.READY, raw, candidate)
