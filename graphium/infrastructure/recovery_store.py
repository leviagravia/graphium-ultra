"""Private durable storage and live-process ownership for Graphium crash recovery.

This store writes only UUID-derived files below its fixed recovery root. It intentionally
has no API that accepts an arbitrary destination path, and it is unrelated to normal
user-document save/serialization infrastructure.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import stat
from dataclasses import dataclass
from typing import Final

from graphium.domain.recovery_artifact import (
    CorruptRecoveryArtifactError,
    RecoveryRecord,
    canonical_recovery_uuid,
    decode_recovery_record,
    encode_recovery_record,
)


_ARTIFACT_SUFFIX: Final = ".recovery"
_LOCK_SUFFIX: Final = ".lock"


class RecoveryStorageError(OSError):
    """Recovery state could not be persisted or read safely."""


class RecoveryArtifactLockedError(RecoveryStorageError):
    """The recovery generation is owned by another live Graphium process."""


@dataclass(frozen=True)
class StagedRecoveryArtifact:
    """Durably staged private bytes that have not yet been published as current recovery."""

    artifact_uuid: str
    temp_path: Path
    final_path: Path


def _open_flags(base: int) -> int:
    flags = base
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _ensure_regular_private_file(fd: int) -> None:
    mode = os.fstat(fd).st_mode
    if not stat.S_ISREG(mode):
        raise RecoveryStorageError("recovery storage entry is not a regular file")


class RecoveryOwnershipLock:
    __slots__ = ("path", "_fd")

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd = fd

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "RecoveryOwnershipLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def __del__(self) -> None:
        fd = getattr(self, "_fd", None)
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            self._fd = None


class RecoveryArtifactStore:
    """One-root recovery store with UUID-derived artifact and advisory-lock paths."""

    __slots__ = ("root",)

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def artifact_path(self, artifact_uuid: str) -> Path:
        value = canonical_recovery_uuid(artifact_uuid)
        return self.root / f"{value}{_ARTIFACT_SUFFIX}"

    def lock_path(self, artifact_uuid: str) -> Path:
        value = canonical_recovery_uuid(artifact_uuid)
        return self.root / f"{value}{_LOCK_SUFFIX}"

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        if not self.root.is_dir():
            raise RecoveryStorageError("recovery root is not a directory")

    def acquire_ownership(self, artifact_uuid: str) -> RecoveryOwnershipLock:
        path = self.lock_path(artifact_uuid)
        self._ensure_root()
        fd = os.open(path, _open_flags(os.O_RDWR | os.O_CREAT), 0o600)
        try:
            _ensure_regular_private_file(fd)
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RecoveryArtifactLockedError(
                    errno.EWOULDBLOCK, "recovery artifact is owned by a live process", str(path)
                ) from exc
            return RecoveryOwnershipLock(path, fd)
        except BaseException:
            os.close(fd)
            raise

    def is_locked(self, artifact_uuid: str) -> bool:
        path = self.lock_path(artifact_uuid)
        try:
            fd = os.open(path, _open_flags(os.O_RDWR))
        except FileNotFoundError:
            return False
        try:
            _ensure_regular_private_file(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
        finally:
            os.close(fd)

    def stage(self, record: RecoveryRecord) -> StagedRecoveryArtifact:
        if not isinstance(record, RecoveryRecord):
            raise TypeError("record must be RecoveryRecord")
        final_path = self.artifact_path(record.artifact_uuid)
        self._ensure_root()
        payload = encode_recovery_record(record)
        expected_length = len(payload)
        expected_digest = hashlib.sha256(payload).digest()
        temp = self.root / (
            f".{record.artifact_uuid}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        fd: int | None = None
        staged_ok = False
        try:
            fd = os.open(
                temp,
                _open_flags(os.O_RDWR | os.O_CREAT | os.O_EXCL),
                0o600,
            )
            _ensure_regular_private_file(fd)
            os.fchmod(fd, 0o600)
            offset = 0
            while offset < expected_length:
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise RecoveryStorageError("short write while persisting recovery artifact")
                offset += written
            os.fsync(fd)
            if os.fstat(fd).st_size != expected_length:
                raise RecoveryStorageError("recovery temp length verification failed")
            os.lseek(fd, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            read_length = 0
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                read_length += len(chunk)
                digest.update(chunk)
            if read_length != expected_length or digest.digest() != expected_digest:
                raise RecoveryStorageError("recovery temp digest verification failed")
            os.close(fd)
            fd = None
            staged_ok = True
            return StagedRecoveryArtifact(record.artifact_uuid, temp, final_path)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if not staged_ok:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass

    def publish(self, staged: StagedRecoveryArtifact) -> Path:
        if not isinstance(staged, StagedRecoveryArtifact):
            raise TypeError("staged must be StagedRecoveryArtifact")
        if staged.final_path != self.artifact_path(staged.artifact_uuid):
            raise RecoveryStorageError("staged recovery destination does not belong to this store")
        if staged.temp_path.parent != self.root or staged.temp_path.suffix != ".tmp":
            raise RecoveryStorageError("staged recovery temp file does not belong to this store")
        os.replace(staged.temp_path, staged.final_path)
        dir_fd = os.open(self.root, _open_flags(os.O_RDONLY))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return staged.final_path

    @staticmethod
    def discard_staged(staged: StagedRecoveryArtifact) -> None:
        if not isinstance(staged, StagedRecoveryArtifact):
            raise TypeError("staged must be StagedRecoveryArtifact")
        try:
            staged.temp_path.unlink()
        except FileNotFoundError:
            pass

    def write(self, record: RecoveryRecord) -> Path:
        staged = self.stage(record)
        try:
            return self.publish(staged)
        finally:
            self.discard_staged(staged)

    def artifact_uuids(self) -> tuple[str, ...]:
        """List published recovery UUIDs without creating the recovery root."""
        try:
            entries = list(os.scandir(self.root))
        except FileNotFoundError:
            return ()
        except NotADirectoryError as exc:
            raise RecoveryStorageError("recovery root is not a directory") from exc
        result: list[str] = []
        for entry in entries:
            name = entry.name
            if not name.endswith(_ARTIFACT_SUFFIX) or not entry.is_file(follow_symlinks=False):
                continue
            raw_uuid = name[:-len(_ARTIFACT_SUFFIX)]
            try:
                result.append(canonical_recovery_uuid(raw_uuid))
            except ValueError:
                continue
        return tuple(sorted(result))

    def claim_existing(self, artifact_uuid: str) -> RecoveryOwnershipLock:
        """Claim an already-published artifact; never create the recovery directory/artifact."""
        path = self.artifact_path(artifact_uuid)
        fd = os.open(path, _open_flags(os.O_RDONLY))
        try:
            _ensure_regular_private_file(fd)
        finally:
            os.close(fd)
        return self.acquire_ownership(artifact_uuid)

    def load(self, artifact_uuid: str) -> RecoveryRecord:
        path = self.artifact_path(artifact_uuid)
        try:
            fd = os.open(path, _open_flags(os.O_RDONLY))
        except FileNotFoundError:
            raise
        try:
            _ensure_regular_private_file(fd)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
        record = decode_recovery_record(b"".join(chunks))
        if record.artifact_uuid != canonical_recovery_uuid(artifact_uuid):
            raise CorruptRecoveryArtifactError("artifact UUID does not match recovery filename")
        return record

    def remove(self, artifact_uuid: str) -> bool:
        path = self.artifact_path(artifact_uuid)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        if self.root.exists():
            dir_fd = os.open(self.root, _open_flags(os.O_RDONLY))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        return True
