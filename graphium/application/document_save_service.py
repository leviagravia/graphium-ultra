"""GTK-free save orchestration for Graphium.

The service captures one stable session state, serializes before mutation, delegates
all physical writes to the single GuardedFileWriter authority, and advances only the exact
captured editor state after a namespace commit.
"""
from __future__ import annotations

from graphium.application.document_session import DocumentSession
from graphium.domain.document_save import (
    DocumentSaveIntent,
    DocumentSaveResult,
    SaveBindingError,
    SaveOperation,
    SaveTargetObservation,
)
from graphium.domain.document_serialization import serialize_document
from graphium.infrastructure.guarded_file_writer import GuardedFileWriter


class DocumentSaveService:
    __slots__ = ("session", "writer")

    def __init__(self, *, session: DocumentSession, writer: GuardedFileWriter) -> None:
        if not isinstance(session, DocumentSession):
            raise TypeError("session must be DocumentSession")
        if not isinstance(writer, GuardedFileWriter):
            raise TypeError("writer must be GuardedFileWriter")
        self.session = session
        self.writer = writer

    def _capture(
        self,
        operation: SaveOperation,
        target: SaveTargetObservation,
    ) -> DocumentSaveIntent:
        snapshot = self.session.snapshot()
        state_id = snapshot.current_editor_state_id
        if state_id is None or state_id <= 0:
            raise SaveBindingError(
                "Graphium must establish a stable current editor state before saving"
            )
        if not snapshot.text_is_current:
            raise SaveBindingError(
                "Graphium must synchronize the live editor text to the exact current state before saving"
            )
        if operation is SaveOperation.SAVE:
            if snapshot.logical_path is None or snapshot.file_state is None:
                raise SaveBindingError("ordinary Save requires an accepted active file baseline")
            if target.logical_target_path != snapshot.logical_path:
                raise SaveBindingError("ordinary Save target differs from the active document")
        profile = snapshot.current_representation_profile
        return DocumentSaveIntent(
            operation=operation,
            editor_state_id=state_id,
            text_to_write=snapshot.text,
            serialization=profile,
            target=target,
            expected_active_file_state=snapshot.file_state,
        )

    def save(self, *, allow_mixed_eol_normalization: bool = False) -> DocumentSaveResult:
        snapshot = self.session.snapshot()
        if snapshot.logical_path is None or snapshot.file_state is None:
            raise SaveBindingError("ordinary Save requires a named document with an accepted baseline")
        target = self.writer.observe_target(
            snapshot.logical_path,
            expected_file_state=snapshot.file_state,
        )
        return self.execute_observed(
            SaveOperation.SAVE,
            target,
            allow_mixed_eol_normalization=allow_mixed_eol_normalization,
        )

    def observe_save_as_target(self, logical_path: str) -> SaveTargetObservation:
        """Observe a Save As destination through the sole physical writer authority."""
        return self.writer.observe_target(logical_path)

    def save_as(
        self,
        target: SaveTargetObservation,
        *,
        allow_mixed_eol_normalization: bool = False,
    ) -> DocumentSaveResult:
        if not isinstance(target, SaveTargetObservation):
            raise TypeError("target must be a SaveTargetObservation")
        snapshot = self.session.snapshot()
        if snapshot.file_state is not None and target.existing is not None:
            active_id = snapshot.file_state.binding.object_id
            if active_id is not None and target.existing.object_id == active_id:
                # A Save As chooser may resolve to the current logical path/symlink target.
                # Route through the ordinary Save guard rather than creating a rebinding lane.
                return self.save(
                    allow_mixed_eol_normalization=allow_mixed_eol_normalization
                )
        return self.execute_observed(
            SaveOperation.SAVE_AS,
            target,
            allow_mixed_eol_normalization=allow_mixed_eol_normalization,
        )

    def execute_observed(
        self,
        operation: SaveOperation,
        target: SaveTargetObservation,
        *,
        allow_mixed_eol_normalization: bool = False,
    ) -> DocumentSaveResult:
        intent = self._capture(operation, target)
        serialized = serialize_document(
            intent.text_to_write,
            intent.serialization,
            allow_mixed_eol_normalization=allow_mixed_eol_normalization,
        )
        outcome = self.writer.commit(intent.target, serialized.data)

        # The namespace commit happened.  Mark exactly the captured editor state saved,
        # even if the user edited again while I/O was in flight.  A missing post-save
        # baseline leaves the logical binding intact but forces the next ordinary Save to
        # fail closed until a new baseline is explicitly re-established.
        self.session.accept_committed_save(
            intent.editor_state_id,
            logical_path=outcome.logical_target_path,
            file_state=outcome.file_state,
            representation_profile=intent.serialization,
        )
        return DocumentSaveResult(
            operation=intent.operation,
            editor_state_id=intent.editor_state_id,
            disposition=outcome.disposition,
            logical_target_path=outcome.logical_target_path,
            committed_fingerprint=outcome.committed_fingerprint,
            file_state=outcome.file_state,
            warnings=outcome.warnings,
        )
