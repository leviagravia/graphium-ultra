"""Non-binding Save a Copy / Save Version Copy orchestration for Graphium."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re

from graphium.application.document_session import DocumentSession
from graphium.domain.document_identity import FileObjectIdentity
from graphium.domain.document_save import (
    GuardedWriteResult,
    SaveTargetObservation,
    StaleSaveTargetError,
    UnsafeSaveTargetError,
)
from graphium.domain.document_serialization import serialize_document
from graphium.infrastructure.guarded_file_writer import GuardedFileWriter


def normalize_logical_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    return os.path.abspath(os.path.normpath(os.path.expanduser(path)))


class CopyBindingError(RuntimeError):
    pass


@dataclass(frozen=True)
class VersionCopyPlan:
    logical_target_path: str
    number: int


class DocumentCopyService:
    __slots__ = ("session", "writer")

    def __init__(self, *, session: DocumentSession, writer: GuardedFileWriter) -> None:
        if not isinstance(session, DocumentSession):
            raise TypeError("session must be DocumentSession")
        if not isinstance(writer, GuardedFileWriter):
            raise TypeError("writer must be GuardedFileWriter")
        self.session = session
        self.writer = writer

    def _stable_snapshot(self):
        snapshot = self.session.snapshot()
        if snapshot.current_editor_state_id is None or not snapshot.text_is_current:
            raise CopyBindingError(
                "Graphium must synchronize the live editor text to its current state before copying"
            )
        return snapshot

    def observe_target(self, logical_path: str) -> SaveTargetObservation:
        snapshot = self._stable_snapshot()
        target_path = normalize_logical_path(logical_path)
        if snapshot.logical_path is not None:
            active_logical = normalize_logical_path(snapshot.logical_path)
            if target_path == active_logical:
                raise CopyBindingError("Save a Copy cannot target the active document path")
        target = self.writer.observe_target(target_path)
        if snapshot.file_state is not None and target.existing is not None:
            active_id = snapshot.file_state.binding.object_id
            if isinstance(active_id, FileObjectIdentity) and target.existing.object_id == active_id:
                raise CopyBindingError(
                    "Save a Copy cannot target another path to the active physical file"
                )
        return target

    def copy_to(
        self,
        target: SaveTargetObservation,
        *,
        allow_mixed_eol_normalization: bool = False,
    ) -> GuardedWriteResult:
        if not isinstance(target, SaveTargetObservation):
            raise TypeError("target must be SaveTargetObservation")
        snapshot = self._stable_snapshot()
        # Re-check logical/object aliasing at execution boundary; target observations are immutable.
        if snapshot.logical_path is not None and (
            normalize_logical_path(target.logical_target_path)
            == normalize_logical_path(snapshot.logical_path)
        ):
            raise CopyBindingError("copy target became the active logical path")
        if snapshot.file_state is not None and target.existing is not None:
            active_id = snapshot.file_state.binding.object_id
            if active_id is not None and target.existing.object_id == active_id:
                raise CopyBindingError("copy target is the active physical file")
        serialized = serialize_document(
            snapshot.text,
            snapshot.current_representation_profile,
            allow_mixed_eol_normalization=allow_mixed_eol_normalization,
        )
        # Deliberately do not call DocumentSession.accept_* after commit.
        return self.writer.commit(target, serialized.data)

    def plan_named_version_copy(self) -> VersionCopyPlan:
        snapshot = self._stable_snapshot()
        if snapshot.logical_path is None:
            raise CopyBindingError("Untitled documents require an explicit version-copy destination")
        logical = normalize_logical_path(snapshot.logical_path)
        directory = os.path.dirname(logical) or os.curdir
        name = os.path.basename(logical)
        stem, suffix = os.path.splitext(name)
        pattern = re.compile(rf"^{re.escape(stem)}_v([0-9]{{4,}}){re.escape(suffix)}$")
        highest = 0
        for entry in os.listdir(directory):
            match = pattern.match(entry)
            if match:
                highest = max(highest, int(match.group(1)))
        number = highest + 1
        width = max(4, len(str(number)))
        target = os.path.join(directory, f"{stem}_v{number:0{width}d}{suffix}")
        return VersionCopyPlan(normalize_logical_path(target), number)

    def observe_planned_version_target(self, plan: VersionCopyPlan) -> SaveTargetObservation:
        if not isinstance(plan, VersionCopyPlan):
            raise TypeError("plan must be VersionCopyPlan")
        target = self.observe_target(plan.logical_target_path)
        if target.existing is not None:
            # A race between scan and observation is not silently renumbered.
            raise StaleSaveTargetError("planned version-copy target appeared before observation")
        return target
