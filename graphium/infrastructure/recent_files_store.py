"""Atomic 0600 XDG-state JSON store for recent-file history."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets


class JsonRecentFilesStore:
    __slots__ = ("path",)

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[str, ...]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ()
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            return ()
        if not isinstance(value, dict) or value.get("version") != 1:
            return ()
        paths = value.get("paths")
        if not isinstance(paths, list):
            return ()
        return tuple(item for item in paths if isinstance(item, str) and item)

    def save(self, paths: tuple[str, ...]) -> None:
        values = tuple(paths)
        if any(not isinstance(item, str) or not item for item in values):
            raise ValueError("recent paths must be non-empty strings")
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Tighten Graphium-owned state directory when possible without mutating ancestors.
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        temp = parent / f".{self.path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        fd = None
        try:
            fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            document = {"version": 1, "paths": list(values)}
            payload = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise OSError("short write while saving recent files")
                offset += written
            # Recent history is convenience state, not document authority.
            # Close before atomic replacement so readers see either the old or new
            # complete JSON, but do not impose document-grade durability barriers
            # (fsync file + directory) on the successful Open/Save-As critical path.
            os.close(fd)
            fd = None
            os.replace(temp, self.path)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
