"""GTK/GIO live external-file monitor for Graphium.

GIO events are interrupts only.  Every accepted interrupt is coalesced into one
background call to Graphium's existing strong observer, and the result is
classified against the immutable file state accepted by the document session.
The monitor never mutates document/session truth and never writes files.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import threading
from typing import Callable

import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from graphium.application.document_properties import (
    CheckNowResult,
    CheckNowStatus,
    classify_fresh_observation,
)
from graphium.application.document_session import DocumentSession
from graphium.domain.document_identity import DocumentFileState
from graphium.domain.document_observation import StrongDocumentObservation
from graphium.infrastructure.document_observer import observe_document


Observer = Callable[..., StrongDocumentObservation]
ResultCallback = Callable[[CheckNowResult], None]


@dataclass(frozen=True)
class _ObservationTicket:
    generation: int
    logical_path: str
    accepted: DocumentFileState


class StrongExternalFileMonitor:
    """One-document GIO interrupt source feeding one strong observation lane."""

    __slots__ = (
        "session", "on_result", "observer", "debounce_ms", "initial_delay_ms",
        "_generation", "_monitors", "_debounce_source_id", "_initial_source_id",
        "_inflight_generation", "_pending_generation", "_closed", "_observations_started",
        "_concurrent", "_max_concurrent",
    )

    def __init__(
        self,
        *,
        session: DocumentSession,
        on_result: ResultCallback,
        observer: Observer = observe_document,
        debounce_ms: int = 120,
        initial_delay_ms: int = 250,
    ) -> None:
        if not isinstance(session, DocumentSession):
            raise TypeError("session must be DocumentSession")
        if not callable(on_result) or not callable(observer):
            raise TypeError("callbacks must be callable")
        if debounce_ms < 1 or initial_delay_ms < 1:
            raise ValueError("monitor delays must be positive")
        self.session = session
        self.on_result = on_result
        self.observer = observer
        self.debounce_ms = int(debounce_ms)
        self.initial_delay_ms = int(initial_delay_ms)
        self._generation = 0
        self._monitors: list[Gio.FileMonitor] = []
        self._debounce_source_id = 0
        self._initial_source_id = 0
        self._inflight_generation: int | None = None
        self._pending_generation: int | None = None
        self._closed = False
        self._observations_started = 0
        self._concurrent = 0
        self._max_concurrent = 0

    @property
    def bound(self) -> bool:
        return bool(self._monitors)

    @property
    def observations_started(self) -> int:
        return self._observations_started

    @property
    def max_concurrent_observations(self) -> int:
        return self._max_concurrent

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.suspend()

    def suspend(self) -> None:
        """Invalidate callbacks and cancel monitor/timer ownership immediately."""
        self._generation += 1
        self._pending_generation = None
        self._remove_source("_debounce_source_id")
        self._remove_source("_initial_source_id")
        for monitor in self._monitors:
            try:
                monitor.cancel()
            except Exception:
                pass
        self._monitors.clear()

    def bind_current(self) -> bool:
        """Bind interrupts to the current accepted named document.

        Binding never accepts disk state.  A delayed asynchronous strong observation closes
        the small race between the accepted load and monitor installation.
        """
        if self._closed:
            return False
        self.suspend()
        snapshot = self.session.snapshot()
        accepted = snapshot.file_state
        path = snapshot.logical_path
        if path is None or accepted is None:
            return False
        generation = self._generation
        try:
            self._monitors = self._create_monitors(path, accepted, generation)
        except Exception as exc:
            self._monitors = []
            self.on_result(CheckNowResult(
                CheckNowStatus.UNAVAILABLE_OR_UNSTABLE,
                f"Live file monitoring is unavailable: {exc}",
            ))
            return False
        self._initial_source_id = GLib.timeout_add(
            self.initial_delay_ms, self._initial_check, generation
        )
        return True

    def _create_monitors(
        self,
        logical_path: str,
        accepted: DocumentFileState,
        generation: int,
    ) -> list[Gio.FileMonitor]:
        monitors: list[Gio.FileMonitor] = []
        flags = Gio.FileMonitorFlags.WATCH_MOVES
        logical_path = os.path.abspath(logical_path)
        canonical = accepted.binding.canonical_path
        is_symlink = bool(canonical and os.path.abspath(canonical) != logical_path)

        if is_symlink:
            if canonical:
                target = Gio.File.new_for_path(canonical).monitor_file(flags, None)
                target.connect("changed", self._on_changed, generation, None)
                monitors.append(target)
            parent_path = os.path.dirname(logical_path) or os.curdir
            parent = Gio.File.new_for_path(parent_path).monitor_directory(flags, None)
            parent.connect("changed", self._on_changed, generation, logical_path)
            monitors.append(parent)
        else:
            monitor = Gio.File.new_for_path(logical_path).monitor_file(flags, None)
            monitor.connect("changed", self._on_changed, generation, None)
            monitors.append(monitor)
        return monitors

    @staticmethod
    def _relevant_event(event_type) -> bool:
        names = (
            "CHANGED", "DELETED", "CREATED", "ATTRIBUTE_CHANGED",
            "MOVED", "RENAMED", "MOVED_IN", "MOVED_OUT",
        )
        return any(event_type == getattr(Gio.FileMonitorEvent, name, None) for name in names)

    @staticmethod
    def _directory_event_matches(file_obj, other_file, logical_path: str | None) -> bool:
        if logical_path is None:
            return True
        wanted = os.path.abspath(logical_path)
        for obj in (file_obj, other_file):
            if obj is None:
                continue
            try:
                got = obj.get_path()
            except Exception:
                continue
            if got is not None and os.path.abspath(got) == wanted:
                return True
        return False

    def _on_changed(self, _monitor, file_obj, other_file, event_type, generation, logical_filter) -> None:
        if self._closed or generation != self._generation:
            return
        if not self._relevant_event(event_type):
            return
        if not self._directory_event_matches(file_obj, other_file, logical_filter):
            return
        self._schedule_observation(generation, immediate=False)

    def _initial_check(self, generation: int) -> bool:
        self._initial_source_id = 0
        if not self._closed and generation == self._generation:
            self._schedule_observation(generation, immediate=True)
        return False

    def _schedule_observation(self, generation: int, *, immediate: bool) -> None:
        if self._closed or generation != self._generation:
            return
        if self._inflight_generation is not None:
            self._pending_generation = generation
            return
        if self._debounce_source_id:
            if immediate:
                self._remove_source("_debounce_source_id")
            else:
                return
        delay = 1 if immediate else self.debounce_ms
        self._debounce_source_id = GLib.timeout_add(delay, self._start_observation, generation)

    def _start_observation(self, generation: int) -> bool:
        self._debounce_source_id = 0
        if self._closed or generation != self._generation:
            return False
        snapshot = self.session.snapshot()
        accepted = snapshot.file_state
        path = snapshot.logical_path
        if path is None or accepted is None:
            return False
        ticket = _ObservationTicket(generation, path, accepted)
        self._inflight_generation = generation
        if self._pending_generation == generation:
            self._pending_generation = None
        self._observations_started += 1
        self._concurrent += 1
        self._max_concurrent = max(self._max_concurrent, self._concurrent)
        thread = threading.Thread(
            target=self._observe_worker,
            args=(ticket,),
            name="graphium-strong-observer",
            daemon=True,
        )
        thread.start()
        return False

    def _observe_worker(self, ticket: _ObservationTicket) -> None:
        try:
            try:
                fresh = self.observer(ticket.logical_path, capture_bytes=False, retries=1)
                if not isinstance(fresh, StrongDocumentObservation):
                    raise TypeError("observer returned captured bytes unexpectedly")
                result = CheckNowResult(classify_fresh_observation(ticket.accepted, fresh))
            except FileNotFoundError:
                result = CheckNowResult(
                    CheckNowStatus.MISSING, "The active logical path no longer exists"
                )
            except Exception as exc:
                result = CheckNowResult(CheckNowStatus.UNAVAILABLE_OR_UNSTABLE, str(exc))
        finally:
            # Worker-owned counter only; document/session/UI state remains main-thread only.
            self._concurrent -= 1
        GLib.idle_add(self._deliver_result, ticket, result)

    def _deliver_result(self, ticket: _ObservationTicket, result: CheckNowResult) -> bool:
        # The sole worker slot is now free even when this ticket belongs to an old
        # lifecycle generation.  A stale RESULT is discarded, but a pending request
        # owned by the current generation must still be serviced.  If newer work is
        # already pending for this same generation, the just-finished result is also
        # obsolete for presentation and must not be published before the follow-up.
        self._inflight_generation = None
        pending_generation = self._pending_generation
        self._pending_generation = None
        pending_current = (
            not self._closed
            and pending_generation is not None
            and pending_generation == self._generation
        )

        current_ticket = not self._closed and ticket.generation == self._generation
        if current_ticket and not pending_current:
            snapshot = self.session.snapshot()
            if snapshot.logical_path == ticket.logical_path and snapshot.file_state == ticket.accepted:
                self.on_result(result)

        if pending_current:
            self._schedule_observation(pending_generation, immediate=False)
        return False

    def _remove_source(self, name: str) -> None:
        source_id = int(getattr(self, name))
        setattr(self, name, 0)
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
