"""GTK-free bounded recent-file history for Graphium."""
from __future__ import annotations

import os
from typing import Protocol


def normalize_logical_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    return os.path.abspath(os.path.normpath(os.path.expanduser(path)))


MAX_RECENT_FILES = 10


class RecentFilesStorePort(Protocol):
    def load(self) -> tuple[str, ...]: ...
    def save(self, paths: tuple[str, ...]) -> None: ...


class RecentFilesController:
    """Lazy MRU file history; never document/session restoration authority."""

    __slots__ = ("store", "_loaded", "_paths")

    def __init__(self, store: RecentFilesStorePort) -> None:
        if store is None:
            raise TypeError("store is required")
        self.store = store
        self._loaded = False
        self._paths: tuple[str, ...] = ()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            loaded = tuple(self.store.load())
        except Exception:
            # Corrupt/unavailable convenience state must not affect document truth.
            loaded = ()
        clean: list[str] = []
        for value in loaded:
            if not isinstance(value, str) or not value:
                continue
            try:
                logical = normalize_logical_path(value)
            except Exception:
                continue
            if logical not in clean:
                clean.append(logical)
            if len(clean) >= MAX_RECENT_FILES:
                break
        self._paths = tuple(clean)
        self._loaded = True

    @property
    def paths(self) -> tuple[str, ...]:
        self._ensure_loaded()
        return self._paths

    def touch(self, path: str) -> tuple[str, ...]:
        self._ensure_loaded()
        logical = normalize_logical_path(path)
        candidate = (logical,) + tuple(item for item in self._paths if item != logical)
        candidate = candidate[:MAX_RECENT_FILES]
        # Publish in-memory state only after durable persistence succeeds.
        self.store.save(candidate)
        self._paths = candidate
        return candidate

    def clear(self) -> None:
        self._ensure_loaded()
        self.store.save(())
        self._paths = ()
