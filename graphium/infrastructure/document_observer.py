"""Shared strong read-only document observation primitive."""
from __future__ import annotations

import hashlib
import os
import stat

from graphium.domain.document_identity import (
    ContentFingerprint,
    DiskObservation,
    DocumentFileBinding,
    FileObjectIdentity,
    UnsupportedDocumentTypeError,
    UnstableDocumentLoadError,
)
from graphium.domain.document_observation import ObservedDocumentBytes, StrongDocumentObservation

_CHUNK_SIZE = 1024 * 1024


def normalize_logical_path(path: str) -> str:
    """Normalize a user path without replacing its logical/symlink spelling by realpath."""
    if not isinstance(path, str):
        raise TypeError("path must be a string")
    if not path:
        raise ValueError("path must not be empty")
    return os.path.abspath(os.path.normpath(os.path.expanduser(path)))


def _signature(st: os.stat_result) -> tuple[int, int, int, int, int, int, int, int, int]:
    return (
        int(st.st_dev), int(st.st_ino), int(st.st_size), int(st.st_mtime_ns),
        int(getattr(st, "st_ctime_ns", 0)), int(st.st_mode),
        int(getattr(st, "st_uid", 0)), int(getattr(st, "st_gid", 0)),
        int(getattr(st, "st_nlink", 1)),
    )


def _observation(logical: str, canonical: str, st: os.stat_result, digest: str) -> StrongDocumentObservation:
    return StrongDocumentObservation(
        binding=DocumentFileBinding(
            logical_path=logical,
            canonical_path=canonical,
            object_id=FileObjectIdentity(int(st.st_dev), int(st.st_ino)),
        ),
        disk=DiskObservation(
            size=int(st.st_size),
            mtime_ns=int(st.st_mtime_ns),
            mode=int(st.st_mode),
            read_only=(int(st.st_mode) & 0o222) == 0,
            ctime_ns=int(getattr(st, "st_ctime_ns", 0)),
            uid=int(getattr(st, "st_uid", 0)),
            gid=int(getattr(st, "st_gid", 0)),
            nlink=int(getattr(st, "st_nlink", 1)),
        ),
        content_fingerprint=ContentFingerprint("sha256", digest),
    )


def observe_document(
    path: str,
    *,
    capture_bytes: bool = False,
    retries: int = 1,
) -> StrongDocumentObservation | ObservedDocumentBytes:
    """Observe one regular local file strongly without accepting/mutating session state.

    Read-only and multiply-linked files remain observable. The writer has stricter overwrite
    policy; Properties needs these conditions as facts rather than observation failures.
    """
    if not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a non-negative integer")
    logical = normalize_logical_path(path)
    attempts = retries + 1
    last_before = last_after = None
    for _ in range(attempts):
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(logical, flags)
        chunks: list[bytes] | None = [] if capture_bytes else None
        digest = hashlib.sha256()
        total = 0
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise UnsupportedDocumentTypeError(
                    f"Graphium can open only regular local text files: {logical}"
                )
            while True:
                block = os.read(fd, _CHUNK_SIZE)
                if not block:
                    break
                total += len(block)
                digest.update(block)
                if chunks is not None:
                    chunks.append(block)
            after = os.fstat(fd)
        finally:
            os.close(fd)

        descriptor_stable = _signature(before) == _signature(after) and total == int(after.st_size)
        canonical = os.path.realpath(logical)
        path_stable = False
        if descriptor_stable:
            try:
                logical_after = os.stat(logical)
                canonical_after = os.stat(canonical)
            except OSError:
                path_stable = False
            else:
                object_id = (int(after.st_dev), int(after.st_ino))
                path_stable = (
                    (int(logical_after.st_dev), int(logical_after.st_ino)) == object_id
                    and (int(canonical_after.st_dev), int(canonical_after.st_ino)) == object_id
                    and os.path.realpath(logical) == canonical
                )
        if descriptor_stable and path_stable:
            observed = _observation(logical, canonical, after, digest.hexdigest())
            if chunks is None:
                return observed
            return ObservedDocumentBytes(observed, b"".join(chunks))
        last_before, last_after = before, after

    raise UnstableDocumentLoadError(
        "File changed while it was being observed; Graphium did not accept torn evidence "
        f"({logical}; before={_signature(last_before)} after={_signature(last_after)})"
    )
