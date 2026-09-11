"""Single-document savepoint-aware session authority for Graphium.

State-ID based Saved/Modified semantics are authoritative. The native editor
integration allows the live GTK buffer to advance state identity without copying the
entire document into this session on every keystroke. Before a physical Save, the current
buffer text must be explicitly synchronized to the exact current editor state ID.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Iterator
from graphium.domain.document_identity import BomKind, DocumentFileState, DocumentLoadResult, LineEnding
from graphium.domain.history import HistoryState
from graphium.domain.document_serialization import (
    DocumentSerializationProfile,
    profile_for_document,
    representation_with_encoding,
    representation_with_line_ending,
)


class DocumentSessionPhase(str, Enum):
    IDLE = "idle"
    REPLACING = "replacing"
    OPENING = "opening"


@dataclass(frozen=True)
class DocumentSessionSnapshot:
    text: str
    logical_path: str | None
    file_state: DocumentFileState | None
    phase: DocumentSessionPhase
    revision: int
    current_editor_state_id: int | None
    saved_editor_state_id: int | None
    text_editor_state_id: int | None
    current_representation_profile: DocumentSerializationProfile
    saved_representation_profile: DocumentSerializationProfile

    @property
    def file_path(self) -> str | None:
        return self.logical_path

    @property
    def modified(self) -> bool:
        return not (
            self.current_editor_state_id is not None
            and self.saved_editor_state_id is not None
            and self.current_editor_state_id == self.saved_editor_state_id
            and self.current_representation_profile == self.saved_representation_profile
        )

    @property
    def text_is_current(self) -> bool:
        return (
            self.current_editor_state_id is not None
            and self.text_editor_state_id == self.current_editor_state_id
        )


class DocumentSession:
    __slots__ = (
        "_text", "_logical_path", "_file_state", "_phase", "_phase_depth", "_revision",
        "_current_editor_state_id", "_saved_editor_state_id", "_text_editor_state_id",
        "_current_representation_profile", "_saved_representation_profile",
    )

    def __init__(self) -> None:
        self._text = ""
        self._logical_path = None
        self._file_state = None
        self._phase = DocumentSessionPhase.IDLE
        self._phase_depth = 0
        self._revision = 0
        self._current_editor_state_id = None
        self._saved_editor_state_id = None
        self._text_editor_state_id = None
        default_profile = profile_for_document(None)
        self._current_representation_profile = default_profile
        self._saved_representation_profile = default_profile

    @staticmethod
    def _state_id(value: int) -> int:
        result = int(value)
        if result <= 0:
            raise ValueError("editor state id must be positive")
        return result

    @property
    def text(self): return self._text
    @property
    def logical_path(self): return self._logical_path
    @property
    def file_state(self): return self._file_state
    @property
    def file_path(self): return self._logical_path
    @property
    def phase(self): return self._phase
    @property
    def loading(self): return self._phase_depth > 0
    @property
    def revision(self): return self._revision
    @property
    def current_editor_state_id(self): return self._current_editor_state_id
    @property
    def saved_editor_state_id(self): return self._saved_editor_state_id
    @property
    def text_editor_state_id(self): return self._text_editor_state_id
    @property
    def current_representation_profile(self): return self._current_representation_profile
    @property
    def saved_representation_profile(self): return self._saved_representation_profile
    @property
    def text_is_current(self): return self.snapshot().text_is_current
    @property
    def modified(self): return self.snapshot().modified

    def snapshot(self) -> DocumentSessionSnapshot:
        return DocumentSessionSnapshot(
            self._text,
            self._logical_path,
            self._file_state,
            self._phase,
            self._revision,
            self._current_editor_state_id,
            self._saved_editor_state_id,
            self._text_editor_state_id,
            self._current_representation_profile,
            self._saved_representation_profile,
        )

    @contextmanager
    def replacement(self, phase: DocumentSessionPhase = DocumentSessionPhase.REPLACING) -> Iterator[None]:
        if not isinstance(phase, DocumentSessionPhase):
            raise TypeError("phase must be DocumentSessionPhase")
        previous = self._phase
        self._phase_depth += 1
        self._phase = phase
        try:
            yield
        finally:
            self._phase_depth -= 1
            if self._phase_depth < 0:
                self._phase_depth = 0
                self._phase = DocumentSessionPhase.IDLE
                raise RuntimeError("document session replacement depth underflow")
            self._phase = previous if self._phase_depth else DocumentSessionPhase.IDLE

    def establish_new(self, state: HistoryState, *, clean: bool = True) -> None:
        if not isinstance(state, HistoryState) or state.state_id <= 0:
            raise TypeError("state must be an assigned HistoryState")
        before = self.snapshot()
        self._text, self._logical_path, self._file_state = state.text, None, None
        self._current_editor_state_id = state.state_id
        self._saved_editor_state_id = state.state_id if clean else None
        self._text_editor_state_id = state.state_id
        profile = profile_for_document(None)
        self._current_representation_profile = profile
        self._saved_representation_profile = profile
        if self.snapshot() != before:
            self._revision += 1

    def establish_open(self, result: DocumentLoadResult, state: HistoryState) -> None:
        if not isinstance(result, DocumentLoadResult):
            raise TypeError("result must be DocumentLoadResult")
        if not isinstance(state, HistoryState) or state.state_id <= 0:
            raise TypeError("state must be an assigned HistoryState")
        if state.text != result.text:
            raise ValueError("history state text must equal loaded text")
        before = self.snapshot()
        self._text = result.text
        self._logical_path = result.file_state.binding.logical_path
        self._file_state = result.file_state
        self._current_editor_state_id = state.state_id
        self._saved_editor_state_id = state.state_id
        self._text_editor_state_id = state.state_id
        profile = profile_for_document(result.file_state)
        self._current_representation_profile = profile
        self._saved_representation_profile = profile
        if self.snapshot() != before:
            self._revision += 1

    def establish_recovered_named(
        self,
        result: DocumentLoadResult,
        state: HistoryState,
        *,
        saved_state_id: int,
        current_profile: DocumentSerializationProfile,
    ) -> None:
        """Install recovered text over a freshly revalidated named Saved baseline."""
        if not isinstance(result, DocumentLoadResult):
            raise TypeError("result must be DocumentLoadResult")
        if not isinstance(state, HistoryState) or state.state_id <= 0:
            raise TypeError("state must be an assigned HistoryState")
        saved_state_id = self._state_id(saved_state_id)
        if saved_state_id == state.state_id:
            raise ValueError("recovered current state must differ from the Saved baseline")
        if not isinstance(current_profile, DocumentSerializationProfile):
            raise TypeError("current_profile must be DocumentSerializationProfile")
        before = self.snapshot()
        self._text = state.text
        self._logical_path = result.file_state.binding.logical_path
        self._file_state = result.file_state
        self._current_editor_state_id = state.state_id
        self._saved_editor_state_id = saved_state_id
        self._text_editor_state_id = state.state_id
        self._current_representation_profile = current_profile
        self._saved_representation_profile = profile_for_document(result.file_state)
        if self.snapshot() != before:
            self._revision += 1

    def establish_recovered_unbound(
        self,
        state: HistoryState,
        *,
        current_profile: DocumentSerializationProfile,
    ) -> None:
        """Install recovered content as an unbound Modified document."""
        if not isinstance(state, HistoryState) or state.state_id <= 0:
            raise TypeError("state must be an assigned HistoryState")
        if not isinstance(current_profile, DocumentSerializationProfile):
            raise TypeError("current_profile must be DocumentSerializationProfile")
        before = self.snapshot()
        self._text, self._logical_path, self._file_state = state.text, None, None
        self._current_editor_state_id = state.state_id
        self._saved_editor_state_id = None
        self._text_editor_state_id = state.state_id
        self._current_representation_profile = current_profile
        self._saved_representation_profile = current_profile
        if self.snapshot() != before:
            self._revision += 1

    def observe_uncommitted_text(self, text: str) -> bool:
        """Compatibility path for the headless session protocol."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if self.loading:
            return False
        before = self.snapshot()
        self._text = text
        self._current_editor_state_id = None
        self._text_editor_state_id = None
        if self.snapshot() != before:
            self._revision += 1
        return True

    def reconcile_with_history(self, state: HistoryState) -> bool:
        if not isinstance(state, HistoryState) or state.state_id <= 0:
            raise TypeError("state must be an assigned HistoryState")
        if self._text != state.text:
            return False
        before = self.snapshot()
        self._current_editor_state_id = state.state_id
        self._text_editor_state_id = state.state_id
        if self.snapshot() != before:
            self._revision += 1
        return True

    def commit_history_state(self, state: HistoryState) -> None:
        if not isinstance(state, HistoryState) or state.state_id <= 0:
            raise TypeError("state must be an assigned HistoryState")
        before = self.snapshot()
        self._text = state.text
        self._current_editor_state_id = state.state_id
        self._text_editor_state_id = state.state_id
        if self.snapshot() != before:
            self._revision += 1

    def advance_editor_state(self, state_id: int) -> None:
        """Advance native editor identity without copying the live buffer text.

        The native editor uses this on every committed edit group. The session text remains a
        previously synchronized representation until ``synchronize_current_text`` is
        called before Save. Modified/Saved remains exact because it depends on IDs only.
        """
        state_id = self._state_id(state_id)
        before = self.snapshot()
        self._current_editor_state_id = state_id
        if self.snapshot() != before:
            self._revision += 1

    def synchronize_current_text(self, text: str, *, state_id: int | None = None) -> int:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        current = self._current_editor_state_id
        if current is None or current <= 0:
            raise RuntimeError("cannot synchronize text without a stable editor state")
        if state_id is not None and self._state_id(state_id) != current:
            raise RuntimeError("text synchronization state ID differs from current editor state")
        before = self.snapshot()
        self._text = text
        self._text_editor_state_id = current
        if self.snapshot() != before:
            self._revision += 1
        return current

    def select_representation_encoding(self, encoding: str, bom: BomKind) -> bool:
        """Select an explicit supported output encoding without writing or editing text."""
        profile = representation_with_encoding(
            self._current_representation_profile, encoding=encoding, bom=bom
        )
        before = self.snapshot()
        self._current_representation_profile = profile
        if self.snapshot() != before:
            self._revision += 1
            return True
        return False

    def select_representation_line_ending(self, line_ending: LineEnding) -> bool:
        """Select a concrete output EOL; on mixed input this is explicit normalization consent."""
        profile = representation_with_line_ending(
            self._current_representation_profile, line_ending=line_ending
        )
        before = self.snapshot()
        self._current_representation_profile = profile
        if self.snapshot() != before:
            self._revision += 1
            return True
        return False

    def retarget_file_binding(self, file_state: DocumentFileState) -> None:
        """Accept a path-only move of the same already-accepted disk document.

        This changes filesystem binding evidence only. Editor text, editor-state IDs and
        representation choices remain untouched, so dirty and Undo relations cannot be
        accidentally accepted or rebuilt by a namespace move.
        """
        if not isinstance(file_state, DocumentFileState):
            raise TypeError("file_state must be DocumentFileState")
        current = self._file_state
        if current is None:
            raise RuntimeError("cannot retarget an unbound document")
        old_object = current.binding.object_id
        new_object = file_state.binding.object_id
        if old_object is None or new_object is None or old_object != new_object:
            raise ValueError("retargeted document does not preserve physical object identity")
        if current.content_fingerprint != file_state.content_fingerprint:
            raise ValueError("retargeted document content differs from the accepted document")
        if current.load != file_state.load:
            raise ValueError("retargeted document load representation differs from the accepted document")
        if profile_for_document(current) != profile_for_document(file_state):
            raise ValueError("retargeted document serialization profile is incompatible")
        if self._saved_representation_profile != profile_for_document(current):
            raise RuntimeError("accepted file representation no longer matches the saved baseline")
        before = self.snapshot()
        self._logical_path = file_state.binding.logical_path
        self._file_state = file_state
        if self.snapshot() != before:
            self._revision += 1

    def accept_saved_state(self, state_id: int, *, file_state: DocumentFileState | None = None) -> None:
        state_id = self._state_id(state_id)
        before = self.snapshot()
        self._saved_editor_state_id = state_id
        self._saved_representation_profile = self._current_representation_profile
        if file_state is not None:
            if not isinstance(file_state, DocumentFileState):
                raise TypeError("file_state must be DocumentFileState or None")
            self._logical_path = file_state.binding.logical_path
            self._file_state = file_state
            accepted_profile = profile_for_document(file_state)
            self._current_representation_profile = accepted_profile
            self._saved_representation_profile = accepted_profile
        if self.snapshot() != before:
            self._revision += 1

    def accept_committed_save(
        self,
        state_id: int,
        *,
        logical_path: str,
        file_state: DocumentFileState | None,
        representation_profile: DocumentSerializationProfile,
    ) -> None:
        state_id = self._state_id(state_id)
        if not isinstance(logical_path, str) or not logical_path:
            raise ValueError("logical_path must be a non-empty string")
        if not isinstance(representation_profile, DocumentSerializationProfile):
            raise TypeError("representation_profile must be DocumentSerializationProfile")
        if file_state is not None:
            if not isinstance(file_state, DocumentFileState):
                raise TypeError("file_state must be DocumentFileState or None")
            if file_state.binding.logical_path != logical_path:
                raise ValueError("file_state logical binding must equal committed logical_path")
        before = self.snapshot()
        self._logical_path = logical_path
        self._file_state = file_state
        self._saved_editor_state_id = state_id
        accepted_profile = (
            profile_for_document(file_state) if file_state is not None else representation_profile
        )
        if self._current_representation_profile == representation_profile:
            self._current_representation_profile = accepted_profile
        self._saved_representation_profile = accepted_profile
        if self.snapshot() != before:
            self._revision += 1

    def invalidate_saved_relation(self) -> None:
        before = self.snapshot()
        self._saved_editor_state_id = None
        if self.snapshot() != before:
            self._revision += 1

    def restore_checkpoint(self, snapshot: DocumentSessionSnapshot) -> None:
        if not isinstance(snapshot, DocumentSessionSnapshot):
            raise TypeError("snapshot must be DocumentSessionSnapshot")
        self._text = snapshot.text
        self._logical_path = snapshot.logical_path
        self._file_state = snapshot.file_state
        self._phase = snapshot.phase
        self._phase_depth = 0
        self._revision = snapshot.revision
        self._current_editor_state_id = snapshot.current_editor_state_id
        self._saved_editor_state_id = snapshot.saved_editor_state_id
        self._text_editor_state_id = snapshot.text_editor_state_id
        self._current_representation_profile = snapshot.current_representation_profile
        self._saved_representation_profile = snapshot.saved_representation_profile

