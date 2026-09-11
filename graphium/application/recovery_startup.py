"""Startup-only discovery and restoration of one orphan Graphium recovery artifact."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from graphium.domain.document_identity import DocumentLoadResult
from graphium.domain.recovery_artifact import (
    RecoveryDocumentKind,
    RecoveryNamedBaseline,
    RecoveryRecord,
)
from graphium.infrastructure.document_loader import load_document
from graphium.infrastructure.document_observer import normalize_logical_path
from graphium.infrastructure.recovery_store import (
    RecoveryArtifactLockedError,
    RecoveryArtifactStore,
    RecoveryOwnershipLock,
)


class RecoveryStartupDecision(str, Enum):
    RECOVER = "recover"
    DISCARD = "discard"
    START_WITHOUT = "start_without"


class RecoveryStartupStatus(str, Enum):
    NONE = "none"
    RECOVERED_BOUND = "recovered_bound"
    RECOVERED_UNBOUND = "recovered_unbound"
    DISCARDED = "discarded"
    DEFERRED = "deferred"
    FAILED = "failed"


@dataclass(frozen=True)
class RecoveryStartupResult:
    status: RecoveryStartupStatus
    artifact_uuid: str | None = None
    provenance_path: str | None = None

    @property
    def recovered(self) -> bool:
        return self.status in {
            RecoveryStartupStatus.RECOVERED_BOUND,
            RecoveryStartupStatus.RECOVERED_UNBOUND,
        }


@dataclass(frozen=True)
class ClaimedRecoveryCandidate:
    record: RecoveryRecord
    ownership: RecoveryOwnershipLock


class RecoveryStartupUIPort(Protocol):
    def choose_startup_recovery(self, record: RecoveryRecord) -> RecoveryStartupDecision: ...
    def show_recovered_unbound(self, provenance_path: str, reason: str) -> None: ...
    def show_error(self, title: str, message: str) -> None: ...


class RecoveredEditorPort(Protocol):
    def initialize_recovered_named(
        self, result: DocumentLoadResult, text: str, current_profile: object
    ) -> object: ...
    def initialize_recovered_unbound(self, text: str, current_profile: object) -> object: ...


class RecoveryRestorePort(Protocol):
    def install_recovered(
        self,
        record: RecoveryRecord,
        ownership: RecoveryOwnershipLock,
        installer: Callable[[], object],
    ) -> None: ...


def named_baseline_matches(result: DocumentLoadResult, baseline: RecoveryNamedBaseline) -> bool:
    state = result.file_state
    binding = state.binding
    current_object = binding.object_id
    if binding.logical_path != normalize_logical_path(baseline.logical_path):
        return False
    if binding.canonical_path != baseline.canonical_path:
        return False
    if baseline.device is None or baseline.inode is None:
        if current_object is not None:
            return False
    elif current_object is None or (current_object.device, current_object.inode) != (
        baseline.device,
        baseline.inode,
    ):
        return False
    fingerprint = state.content_fingerprint
    return fingerprint.algorithm.lower() == "sha256" and fingerprint.hex_digest == baseline.content_sha256


class RecoveryStartupCoordinator:
    """Find, claim, present and restore at most one orphan artifact for one launch."""

    __slots__ = ("store", "editor", "recovery", "ui", "loader")

    def __init__(
        self,
        *,
        store: RecoveryArtifactStore,
        editor: RecoveredEditorPort,
        recovery: RecoveryRestorePort,
        ui: RecoveryStartupUIPort,
        loader: Callable[[str], DocumentLoadResult] = load_document,
    ) -> None:
        self.store = store
        self.editor = editor
        self.recovery = recovery
        self.ui = ui
        self.loader = loader

    @staticmethod
    def _matches_explicit_path(record: RecoveryRecord, explicit_path: str | None) -> bool:
        if explicit_path is None:
            return True
        if record.document_kind is not RecoveryDocumentKind.NAMED or record.named_baseline is None:
            return False
        try:
            return normalize_logical_path(record.named_baseline.logical_path) == normalize_logical_path(explicit_path)
        except (TypeError, ValueError):
            return False

    def discover(self, explicit_path: str | None = None) -> ClaimedRecoveryCandidate | None:
        records: list[RecoveryRecord] = []
        for artifact_uuid in self.store.artifact_uuids():
            try:
                record = self.store.load(artifact_uuid)
            except Exception:
                continue
            if self._matches_explicit_path(record, explicit_path):
                records.append(record)
        records.sort(key=lambda item: (item.captured_at_ns, item.artifact_uuid), reverse=True)
        for record in records:
            try:
                ownership = self.store.claim_existing(record.artifact_uuid)
            except (FileNotFoundError, RecoveryArtifactLockedError, OSError):
                continue
            try:
                current = self.store.load(record.artifact_uuid)
                if not self._matches_explicit_path(current, explicit_path):
                    ownership.release()
                    continue
                return ClaimedRecoveryCandidate(current, ownership)
            except Exception:
                ownership.release()
        return None

    def run(self, explicit_path: str | None = None) -> RecoveryStartupResult:
        try:
            candidate = self.discover(explicit_path)
        except Exception as exc:
            self.ui.show_error("Crash recovery could not be checked", str(exc))
            return RecoveryStartupResult(RecoveryStartupStatus.FAILED)
        if candidate is None:
            return RecoveryStartupResult(RecoveryStartupStatus.NONE)
        record, ownership = candidate.record, candidate.ownership
        provenance = None if record.named_baseline is None else record.named_baseline.logical_path
        try:
            decision = self.ui.choose_startup_recovery(record)
        except Exception as exc:
            ownership.release()
            self.ui.show_error("Crash recovery could not be presented", str(exc))
            return RecoveryStartupResult(RecoveryStartupStatus.FAILED, record.artifact_uuid, provenance)
        if decision is RecoveryStartupDecision.START_WITHOUT:
            ownership.release()
            return RecoveryStartupResult(RecoveryStartupStatus.DEFERRED, record.artifact_uuid, provenance)
        if decision is RecoveryStartupDecision.DISCARD:
            try:
                self.store.remove(record.artifact_uuid)
            except Exception as exc:
                ownership.release()
                self.ui.show_error("Recovery was not discarded", str(exc))
                return RecoveryStartupResult(RecoveryStartupStatus.FAILED, record.artifact_uuid, provenance)
            ownership.release()
            return RecoveryStartupResult(RecoveryStartupStatus.DISCARDED, record.artifact_uuid, provenance)
        if decision is not RecoveryStartupDecision.RECOVER:
            ownership.release()
            self.ui.show_error("Crash recovery could not be presented", "Invalid recovery choice.")
            return RecoveryStartupResult(RecoveryStartupStatus.FAILED, record.artifact_uuid, provenance)
        try:
            if record.document_kind is RecoveryDocumentKind.NAMED and record.named_baseline is not None:
                try:
                    loaded = self.loader(record.named_baseline.logical_path)
                    matched = named_baseline_matches(loaded, record.named_baseline)
                    reason = "The original file no longer matches the saved baseline from the interrupted session."
                except Exception as exc:
                    loaded, matched = None, False
                    reason = f"The original file could not be safely revalidated: {exc}"
                if matched and loaded is not None:
                    self.recovery.install_recovered(
                        record,
                        ownership,
                        lambda: self.editor.initialize_recovered_named(
                            loaded, record.text, record.current_profile
                        ),
                    )
                    return RecoveryStartupResult(
                        RecoveryStartupStatus.RECOVERED_BOUND,
                        record.artifact_uuid,
                        provenance,
                    )
                self.recovery.install_recovered(
                    record,
                    ownership,
                    lambda: self.editor.initialize_recovered_unbound(
                        record.text, record.current_profile
                    ),
                )
                self.ui.show_recovered_unbound(record.named_baseline.logical_path, reason)
                return RecoveryStartupResult(
                    RecoveryStartupStatus.RECOVERED_UNBOUND,
                    record.artifact_uuid,
                    provenance,
                )
            self.recovery.install_recovered(
                record,
                ownership,
                lambda: self.editor.initialize_recovered_unbound(record.text, record.current_profile),
            )
            return RecoveryStartupResult(
                RecoveryStartupStatus.RECOVERED_UNBOUND,
                record.artifact_uuid,
                None,
            )
        except Exception as exc:
            if ownership.held:
                ownership.release()
            self.ui.show_error("Recovered content could not be opened", str(exc))
            return RecoveryStartupResult(RecoveryStartupStatus.FAILED, record.artifact_uuid, provenance)
