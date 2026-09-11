"""Bounded stale-safe UTF-8 authority files for Companion clients."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

_MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CompanionFileToken:
    exists: bool
    size: int = 0
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class CompanionTextSnapshot:
    text: str
    token: CompanionFileToken


class CompanionAuthorityError(RuntimeError):
    pass


def _token(path: Path, *, max_bytes: int = _MAX_BYTES) -> CompanionFileToken:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return CompanionFileToken(False)
    if stat.S_ISLNK(st.st_mode):
        raise CompanionAuthorityError("Companion authority must not be a symbolic link.")
    if not stat.S_ISREG(st.st_mode):
        raise CompanionAuthorityError("Companion authority must be a regular file.")
    if st.st_size > max_bytes:
        raise CompanionAuthorityError(f"Companion authority exceeds {max_bytes} bytes.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    if remaining <= 0 and stream.read(1):
        raise CompanionAuthorityError(f"Companion authority exceeds {max_bytes} bytes.")
    return CompanionFileToken(True, int(st.st_size), digest.hexdigest())


class CheckedTextAuthority:
    """Owns observation/staleness only; Core GuardedFileWriter owns physical writes."""

    def __init__(self, writer, path: str | os.PathLike[str], *, max_bytes: int = _MAX_BYTES) -> None:
        self.writer = writer
        self.path = Path(path)
        self.max_bytes = int(max_bytes)

    def load(self) -> CompanionTextSnapshot:
        before = _token(self.path, max_bytes=self.max_bytes)
        if not before.exists:
            return CompanionTextSnapshot("", before)
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise CompanionAuthorityError(f"Could not read Companion authority: {exc}") from exc
        if len(raw) > self.max_bytes:
            raise CompanionAuthorityError(f"Companion authority exceeds {self.max_bytes} bytes.")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CompanionAuthorityError("Companion authority is not valid UTF-8.") from exc
        after = _token(self.path, max_bytes=self.max_bytes)
        if after != before:
            raise CompanionAuthorityError("Companion authority changed while it was being read.")
        return CompanionTextSnapshot(text, after)

    def save(self, text: str, expected: CompanionFileToken) -> CompanionTextSnapshot:
        if not isinstance(text, str):
            raise TypeError("Companion authority text must be str")
        data = text.encode("utf-8")
        if len(data) > self.max_bytes:
            raise CompanionAuthorityError(f"Companion authority exceeds {self.max_bytes} bytes.")
        current = _token(self.path, max_bytes=self.max_bytes)
        if current != expected:
            raise CompanionAuthorityError("Companion authority changed on disk; Refresh before editing.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        observation = self.writer.observe_target(str(self.path))
        if _token(self.path, max_bytes=self.max_bytes) != expected:
            raise CompanionAuthorityError("Companion authority changed immediately before save.")
        try:
            self.writer.commit(observation, data)
        except Exception as exc:
            raise CompanionAuthorityError(f"Companion authority save failed: {exc}") from exc
        loaded = self.load()
        if loaded.text != text:
            raise CompanionAuthorityError("Companion authority verification failed after save.")
        return loaded
