"""Bounded crash-recovery state machine for one Graphium document process."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Protocol

from graphium.application.document_session import DocumentSession, DocumentSessionSnapshot
from graphium.domain.document_identity import ContentFingerprint
from graphium.domain.history import HistoryState
from graphium.domain.recovery_artifact import (
    RecoveryDocumentKind,
    RecoveryNamedBaseline,
    RecoveryRecord,
    new_recovery_uuid,
)
from graphium.infrastructure.recovery_store import RecoveryArtifactStore, RecoveryOwnershipLock


RECOVERY_DELAY_SECONDS = 30


class RecoveryCapturePort(Protocol):
    def capture_full(self) -> HistoryState: ...


class RecoverySchedulerPort(Protocol):
    def schedule_once(self, delay_seconds: int, callback: Callable[[], None]) -> object: ...
    def cancel(self, handle: object) -> None: ...
    def dispatch(self, callback: Callable[[], None]) -> None: ...


class RecoveryWorkerPort(Protocol):
    def submit(
        self,
        job: Callable[[], object],
        done: Callable[[object | None, BaseException | None], None],
    ) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class RecoveryControllerSnapshot:
    generation: int
    artifact_uuid: str | None
    timer_pending: bool
    write_in_flight: bool
    newer_state_pending: bool
    change_serial: int


class RecoveryController:
    """One-document recovery owner with one timer, one worker and generation fencing."""

    __slots__ = (
        "session", "capture", "store", "scheduler", "worker", "_warn", "_clock_ns",
        "_publication_lock", "_generation", "_artifact_uuid", "_ownership", "_binding_key",
        "_timer_handle", "_write_in_flight", "_newer_state_pending", "_change_serial",
        "_warned_generation", "_recovery_suppressed", "_installing_recovery", "_closed",
    )

    def __init__(
        self,
        *,
        session: DocumentSession,
        capture: RecoveryCapturePort,
        store: RecoveryArtifactStore,
        scheduler: RecoverySchedulerPort,
        worker: RecoveryWorkerPort,
        warn: Callable[[str, str], None] | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if not isinstance(session, DocumentSession):
            raise TypeError("session must be DocumentSession")
        if capture is None or not callable(getattr(capture, "capture_full", None)):
            raise TypeError("capture must implement capture_full()")
        if not isinstance(store, RecoveryArtifactStore):
            raise TypeError("store must be RecoveryArtifactStore")
        for name in ("schedule_once", "cancel", "dispatch"):
            if not callable(getattr(scheduler, name, None)):
                raise TypeError(f"scheduler must implement {name}()")
        for name in ("submit", "close"):
            if not callable(getattr(worker, name, None)):
                raise TypeError(f"worker must implement {name}()")
        self.session = session
        self.capture = capture
        self.store = store
        self.scheduler = scheduler
        self.worker = worker
        self._warn = warn
        self._clock_ns = clock_ns
        self._publication_lock = threading.Lock()
        self._generation = 0
        self._artifact_uuid: str | None = None
        self._ownership: RecoveryOwnershipLock | None = None
        self._binding_key: tuple[object, ...] | None = None
        self._timer_handle: object | None = None
        self._write_in_flight = False
        self._newer_state_pending = False
        self._change_serial = 0
        self._warned_generation: int | None = None
        self._recovery_suppressed = False
        self._installing_recovery = False
        self._closed = False

    def snapshot(self) -> RecoveryControllerSnapshot:
        return RecoveryControllerSnapshot(
            self._generation,
            self._artifact_uuid,
            self._timer_handle is not None,
            self._write_in_flight,
            self._newer_state_pending,
            self._change_serial,
        )

    @staticmethod
    def _binding_key_for(snapshot: DocumentSessionSnapshot) -> tuple[object, ...]:
        state = snapshot.file_state
        if snapshot.logical_path is None or state is None:
            return ("untitled",)
        object_id = state.binding.object_id
        return (
            "named",
            snapshot.logical_path,
            state.binding.canonical_path,
            None if object_id is None else object_id.device,
            None if object_id is None else object_id.inode,
            state.content_fingerprint.algorithm,
            state.content_fingerprint.hex_digest,
            snapshot.saved_representation_profile,
        )

    @staticmethod
    def _named_baseline(snapshot: DocumentSessionSnapshot) -> RecoveryNamedBaseline | None:
        state = snapshot.file_state
        if snapshot.logical_path is None or state is None:
            return None
        fingerprint: ContentFingerprint = state.content_fingerprint
        if fingerprint.algorithm.lower() != "sha256":
            raise RuntimeError("recovery requires the accepted SHA-256 document fingerprint")
        object_id = state.binding.object_id
        return RecoveryNamedBaseline(
            logical_path=snapshot.logical_path,
            canonical_path=state.binding.canonical_path,
            device=None if object_id is None else object_id.device,
            inode=None if object_id is None else object_id.inode,
            content_sha256=fingerprint.hex_digest,
        )

    def _warn_once(self, message: str) -> None:
        if self._warn is None or self._warned_generation == self._generation:
            return
        self._warned_generation = self._generation
        self._warn("Crash recovery is temporarily unavailable", message)

    def _cancel_timer(self) -> None:
        handle = self._timer_handle
        self._timer_handle = None
        if handle is not None:
            try:
                self.scheduler.cancel(handle)
            except Exception:
                pass

    def _queue_old_generation_cleanup(
        self,
        artifact_uuid: str | None,
        ownership: RecoveryOwnershipLock | None,
    ) -> None:
        if artifact_uuid is None and ownership is None:
            return

        def cleanup() -> object:
            try:
                if artifact_uuid is not None:
                    try:
                        self.store.remove(artifact_uuid)
                    except FileNotFoundError:
                        pass
            finally:
                if ownership is not None:
                    ownership.release()
            return True

        try:
            self.worker.submit(cleanup, lambda _result, _error: None)
        except Exception:
            # If the worker is already unavailable, at least release the process lock.
            if ownership is not None:
                ownership.release()

    def _invalidate_current(self) -> None:
        self._cancel_timer()
        old_uuid = self._artifact_uuid
        old_ownership = self._ownership
        with self._publication_lock:
            self._generation += 1
            self._artifact_uuid = None
            self._ownership = None
            self._binding_key = None
            self._warned_generation = None
        self._newer_state_pending = False
        self._queue_old_generation_cleanup(old_uuid, old_ownership)

    def invalidate(self) -> None:
        if self._closed:
            return
        self._change_serial += 1
        self._recovery_suppressed = True
        self._invalidate_current()

    def _start_generation(self, snapshot: DocumentSessionSnapshot) -> bool:
        artifact_uuid = new_recovery_uuid()
        try:
            ownership = self.store.acquire_ownership(artifact_uuid)
        except Exception as exc:
            self._generation += 1
            self._warn_once(str(exc))
            return False
        self._generation += 1
        self._artifact_uuid = artifact_uuid
        self._ownership = ownership
        self._binding_key = self._binding_key_for(snapshot)
        self._warned_generation = None
        return True

    def _ensure_generation(self, snapshot: DocumentSessionSnapshot) -> bool:
        key = self._binding_key_for(snapshot)
        if self._artifact_uuid is not None and self._binding_key == key:
            return True
        if self._artifact_uuid is not None or self._ownership is not None:
            self._invalidate_current()
        return self._start_generation(snapshot)

    def _schedule_if_needed(self) -> None:
        if self._closed or self._timer_handle is not None or self._write_in_flight:
            return
        try:
            self._timer_handle = self.scheduler.schedule_once(
                RECOVERY_DELAY_SECONDS, self._capture_due
            )
        except Exception as exc:
            self._timer_handle = None
            self._warn_once(str(exc))

    def document_state_changed(self) -> None:
        """Observe a committed editor/representation/lifecycle state transition."""
        if self._closed or self._installing_recovery:
            return
        self._recovery_suppressed = False
        self._change_serial += 1
        snapshot = self.session.snapshot()
        if not snapshot.modified:
            self._invalidate_current()
            return
        if snapshot.current_editor_state_id is None or snapshot.current_editor_state_id <= 0:
            return
        if not self._ensure_generation(snapshot):
            return
        if self._write_in_flight:
            self._newer_state_pending = True
            return
        self._schedule_if_needed()

    def _build_record(
        self,
        snapshot: DocumentSessionSnapshot,
        text: str,
        *,
        generation: int,
        artifact_uuid: str,
        state_token: int,
    ) -> RecoveryRecord:
        baseline = self._named_baseline(snapshot)
        kind = RecoveryDocumentKind.NAMED if baseline is not None else RecoveryDocumentKind.UNTITLED
        captured_at = int(self._clock_ns())
        if captured_at <= 0:
            raise RuntimeError("recovery clock must return a positive nanosecond timestamp")
        return RecoveryRecord(
            artifact_uuid=artifact_uuid,
            captured_at_ns=captured_at,
            generation=generation,
            state_token=state_token,
            text=text,
            current_profile=snapshot.current_representation_profile,
            saved_profile=snapshot.saved_representation_profile,
            document_kind=kind,
            named_baseline=baseline,
        )

    def _capture_due(self) -> None:
        """Main-thread timed boundary: capture newest stable text once, then dispatch I/O."""
        if self._closed:
            return
        self._timer_handle = None
        before = self.session.snapshot()
        if not before.modified:
            self._invalidate_current()
            return
        state_id = before.current_editor_state_id
        if state_id is None or state_id <= 0:
            return
        if not self._ensure_generation(before):
            return
        artifact_uuid = self._artifact_uuid
        generation = self._generation
        if artifact_uuid is None:
            return
        captured = self.capture.capture_full()
        after = self.session.snapshot()
        if (
            not after.modified
            or after.current_editor_state_id != state_id
            or after.current_representation_profile != before.current_representation_profile
            or self._binding_key_for(after) != self._binding_key_for(before)
        ):
            self._newer_state_pending = True
            self._schedule_if_needed()
            return
        state_token = self._change_serial
        try:
            record = self._build_record(
                after,
                captured.text,
                generation=generation,
                artifact_uuid=artifact_uuid,
                state_token=state_token,
            )
        except Exception as exc:
            self._warn_once(str(exc))
            return
        self._write_in_flight = True
        self._newer_state_pending = False

        def persist() -> object:
            staged = self.store.stage(record)
            try:
                with self._publication_lock:
                    if (
                        self._closed
                        or self._generation != generation
                        or self._artifact_uuid != artifact_uuid
                    ):
                        self.store.discard_staged(staged)
                        return False
                    self.store.publish(staged)
                    return True
            finally:
                self.store.discard_staged(staged)

        def completed(_result: object | None, error: BaseException | None) -> None:
            self._write_in_flight = False
            if error is not None:
                self._warn_once(str(error))
            current = self.session.snapshot()
            newer = self._change_serial > state_token or self._newer_state_pending
            self._newer_state_pending = False
            if self._closed:
                return
            if self._recovery_suppressed:
                return
            if not current.modified:
                self._invalidate_current()
                return
            if newer:
                if self._ensure_generation(current):
                    self._schedule_if_needed()

        try:
            self.worker.submit(persist, completed)
        except Exception as exc:
            self._write_in_flight = False
            self._warn_once(str(exc))

    def install_recovered(
        self,
        record: RecoveryRecord,
        ownership: RecoveryOwnershipLock,
        installer: Callable[[], object],
    ) -> None:
        """Adopt one claimed orphan while its recovered document is installed transactionally."""
        if self._closed:
            raise RuntimeError("recovery controller is closed")
        if not isinstance(record, RecoveryRecord):
            raise TypeError("record must be RecoveryRecord")
        if not isinstance(ownership, RecoveryOwnershipLock) or not ownership.held:
            raise TypeError("ownership must be a held RecoveryOwnershipLock")
        if ownership.path != self.store.lock_path(record.artifact_uuid):
            raise ValueError("ownership lock does not match recovery artifact")
        if not callable(installer):
            raise TypeError("installer must be callable")
        if self._artifact_uuid is not None or self._ownership is not None:
            raise RuntimeError("cannot install orphan recovery over an active recovery generation")
        self._cancel_timer()
        self._installing_recovery = True
        try:
            installer()
            snapshot = self.session.snapshot()
            if not snapshot.modified:
                raise RuntimeError("recovered document must be Modified")
            with self._publication_lock:
                self._generation += 1
                self._artifact_uuid = record.artifact_uuid
                self._ownership = ownership
                self._binding_key = self._binding_key_for(snapshot)
                self._warned_generation = None
            self._change_serial += 1
            self._newer_state_pending = False
            self._recovery_suppressed = False
        finally:
            self._installing_recovery = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._recovery_suppressed = True
        self._cancel_timer()
        old_uuid = self._artifact_uuid
        old_ownership = self._ownership
        with self._publication_lock:
            self._generation += 1
            self._artifact_uuid = None
            self._ownership = None
            self._binding_key = None
        self._queue_old_generation_cleanup(old_uuid, old_ownership)
        self.worker.close()
