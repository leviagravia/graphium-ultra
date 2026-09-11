"""Bounded local-file input adapter for bibliography interchange.

This module reads one explicit import file only.  It owns no persistent library,
writer, watcher, index, database, network access or GTK state.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


MAX_BIB_IMPORT_BYTES = 16 * 1024 * 1024


def read_bibliography_text(
    path: str | Path, *, max_bytes: int = MAX_BIB_IMPORT_BYTES
) -> str:
    """Read one stable local regular UTF-8 file without following symlinks."""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    target = Path(path)
    path_state = target.lstat()
    if stat.S_ISLNK(path_state.st_mode) or not stat.S_ISREG(path_state.st_mode):
        raise ValueError("Bibliography import must be a regular local file, not a symlink.")
    if path_state.st_size > max_bytes:
        raise ValueError(f"Bibliography import exceeds the {max_bytes // (1024 * 1024)} MiB limit.")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Bibliography import is not a regular file.")
        if (path_state.st_dev, path_state.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Bibliography path changed before Graphium Plus opened it.")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise ValueError(f"Bibliography import exceeds the {max_bytes // (1024 * 1024)} MiB limit.")
            chunks.append(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)

    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or total != after.st_size:
        raise ValueError("Bibliography file changed while Graphium Plus was reading it.")
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Bibliography import must be UTF-8 text.") from exc
