"""Immutable guarded-save contracts for Graphium.

This module is pure domain code.  It describes one captured editor-state save,
writer observations, and truthful commit outcomes; it performs no filesystem I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .document_identity import ContentFingerprint, DocumentFileState, FileObjectIdentity
from .document_serialization import DocumentSerializationProfile


class SaveOperation(str, Enum):
    SAVE = "save"
    SAVE_AS = "save-as"


class SaveTargetExpectation(str, Enum):
    EXPECTED_ABSENT = "expected-absent"
    EXPECTED_EXISTING = "expected-existing"


class SaveDisposition(str, Enum):
    COMMITTED_CONFIRMED = "committed-confirmed"
    COMMITTED_DURABILITY_UNCERTAIN = "committed-durability-uncertain"
    COMMITTED_BASELINE_UNAVAILABLE = "committed-baseline-unavailable"


@dataclass(frozen=True)
class SaveTargetSnapshot:
    """Stable writer-grade evidence for one existing physical target."""

    object_id: FileObjectIdentity
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    uid: int
    gid: int
    nlink: int
    content_fingerprint: ContentFingerprint
    xattrs: tuple[tuple[str, bytes], ...] = ()


@dataclass(frozen=True)
class SaveTargetObservation:
    """Immutable target observation consumed by exactly one guarded commit attempt."""

    expectation: SaveTargetExpectation
    logical_target_path: str
    logical_parent_path: str
    physical_target_path: str
    physical_parent_path: str
    parent_object_id: FileObjectIdentity
    logical_parent_object_id: FileObjectIdentity
    logical_target_is_symlink: bool
    existing: SaveTargetSnapshot | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.expectation, SaveTargetExpectation):
            raise TypeError("expectation must be SaveTargetExpectation")
        for name in (
            "logical_target_path",
            "logical_parent_path",
            "physical_target_path",
            "physical_parent_path",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.parent_object_id, FileObjectIdentity):
            raise TypeError("parent_object_id must be FileObjectIdentity")
        if not isinstance(self.logical_parent_object_id, FileObjectIdentity):
            raise TypeError("logical_parent_object_id must be FileObjectIdentity")
        if self.expectation is SaveTargetExpectation.EXPECTED_ABSENT:
            if self.existing is not None:
                raise ValueError("absent target observation cannot contain existing snapshot")
            if self.logical_target_is_symlink:
                raise ValueError("an absent target cannot be a symlink")
        elif not isinstance(self.existing, SaveTargetSnapshot):
            raise TypeError("existing target observation requires SaveTargetSnapshot")


@dataclass(frozen=True)
class DocumentSaveIntent:
    """One immutable captured editor state prepared for persistence."""

    operation: SaveOperation
    editor_state_id: int
    text_to_write: str
    serialization: DocumentSerializationProfile
    target: SaveTargetObservation
    expected_active_file_state: DocumentFileState | None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, SaveOperation):
            raise TypeError("operation must be SaveOperation")
        if int(self.editor_state_id) <= 0:
            raise ValueError("editor_state_id must be positive")
        if not isinstance(self.text_to_write, str):
            raise TypeError("text_to_write must be a string")
        if not isinstance(self.serialization, DocumentSerializationProfile):
            raise TypeError("serialization must be DocumentSerializationProfile")
        if not isinstance(self.target, SaveTargetObservation):
            raise TypeError("target must be SaveTargetObservation")
        if self.expected_active_file_state is not None and not isinstance(
            self.expected_active_file_state, DocumentFileState
        ):
            raise TypeError("expected_active_file_state must be DocumentFileState or None")


@dataclass(frozen=True)
class GuardedWriteResult:
    disposition: SaveDisposition
    logical_target_path: str
    committed_fingerprint: ContentFingerprint
    file_state: DocumentFileState | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentSaveResult:
    operation: SaveOperation
    editor_state_id: int
    disposition: SaveDisposition
    logical_target_path: str
    committed_fingerprint: ContentFingerprint
    file_state: DocumentFileState | None
    warnings: tuple[str, ...] = ()

    @property
    def committed(self) -> bool:
        return True


class DocumentSaveError(RuntimeError):
    """Base class for pre-commit save failures.

    These errors are retry-safe because the authoritative target was not committed.
    """

    committed = False


class SaveBindingError(DocumentSaveError):
    """The active session has no stable binding/state suitable for this operation."""


class StaleSaveTargetError(DocumentSaveError):
    """The target no longer matches the accepted/observed version."""


class UnsafeSaveTargetError(DocumentSaveError):
    """The target topology or metadata cannot enter Graphium's safe save lane."""


class GuardedWriteError(DocumentSaveError):
    """Staging/sync/namespace work failed before a commit occurred."""
