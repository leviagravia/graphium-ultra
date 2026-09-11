"""Read-only document facts and strong Check Now classification for Graphium."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from graphium.application.document_session import DocumentSession
from graphium.domain.document_identity import BomKind, DocumentFileState, LineEnding
from graphium.domain.document_observation import StrongDocumentObservation


class CheckNowStatus(str, Enum):
    UNCHANGED = "unchanged"
    CONTENT_CHANGED = "content-changed"
    METADATA_CHANGED = "metadata-changed"
    REPLACED_OR_RETARGETED = "replaced-or-retargeted"
    MISSING = "missing"
    UNAVAILABLE_OR_UNSTABLE = "unavailable-or-unstable"


@dataclass(frozen=True)
class DocumentPropertiesSnapshot:
    logical_path: str | None
    canonical_path: str | None
    size: int | None
    mtime_ns: int | None
    encoding: str
    bom: BomKind
    eol: LineEnding
    eol_mixed: bool
    modified: bool
    read_only: bool | None
    nlink: int | None


@dataclass(frozen=True)
class CheckNowResult:
    status: CheckNowStatus
    detail: str = ""


def _disk_tuple(state) -> tuple:
    return (
        state.size,
        state.mtime_ns,
        state.mode,
        state.ctime_ns,
        state.uid,
        state.gid,
        state.nlink,
        state.read_only,
    )


def classify_fresh_observation(
    accepted: DocumentFileState,
    fresh: StrongDocumentObservation,
) -> CheckNowStatus:
    if accepted.binding.canonical_path != fresh.binding.canonical_path:
        return CheckNowStatus.REPLACED_OR_RETARGETED
    if accepted.binding.object_id != fresh.binding.object_id:
        return CheckNowStatus.REPLACED_OR_RETARGETED
    if accepted.content_fingerprint != fresh.content_fingerprint:
        return CheckNowStatus.CONTENT_CHANGED
    if _disk_tuple(accepted.disk) != _disk_tuple(fresh.disk):
        return CheckNowStatus.METADATA_CHANGED
    return CheckNowStatus.UNCHANGED


class DocumentObserverPort(Protocol):
    def __call__(self, path: str, *, capture_bytes: bool = False, retries: int = 1) -> StrongDocumentObservation: ...


class DocumentPropertiesController:
    __slots__ = ("session", "observer")

    def __init__(
        self,
        *,
        session: DocumentSession,
        observer: DocumentObserverPort,
    ) -> None:
        if not isinstance(session, DocumentSession):
            raise TypeError("session must be DocumentSession")
        self.session = session
        self.observer = observer

    def snapshot(self) -> DocumentPropertiesSnapshot:
        session = self.session.snapshot()
        state = session.file_state
        profile = session.current_representation_profile
        return DocumentPropertiesSnapshot(
            logical_path=state.binding.logical_path if state else None,
            canonical_path=state.binding.canonical_path if state else None,
            size=state.disk.size if state else None,
            mtime_ns=state.disk.mtime_ns if state else None,
            encoding=profile.encoding,
            bom=profile.bom,
            eol=profile.line_ending,
            eol_mixed=profile.mixed_source,
            modified=session.modified,
            read_only=state.disk.read_only if state else None,
            nlink=state.disk.nlink if state else None,
        )

    def check_now(self) -> CheckNowResult:
        before = self.session.snapshot()
        accepted = before.file_state
        if accepted is None or before.logical_path is None:
            return CheckNowResult(CheckNowStatus.UNAVAILABLE_OR_UNSTABLE, "Document is not saved")
        try:
            fresh = self.observer(before.logical_path, capture_bytes=False, retries=1)
            if not isinstance(fresh, StrongDocumentObservation):
                raise TypeError("observer returned captured bytes unexpectedly")
            result = CheckNowResult(classify_fresh_observation(accepted, fresh))
        except FileNotFoundError:
            result = CheckNowResult(CheckNowStatus.MISSING, "The active logical path no longer exists")
        except Exception as exc:
            result = CheckNowResult(CheckNowStatus.UNAVAILABLE_OR_UNSTABLE, str(exc))
        # Fail visibly if a future refactor accidentally lets observation accept/mutate session truth.
        if self.session.snapshot() != before:
            raise RuntimeError("Check Now mutated active document/session authority")
        return result
