"""Guarded Save As companion planning for Graphium Plus Scratchpads.

Save As is not Rename: the old document and its saved Scratchpad remain in place.
This module coordinates only the path-local companion copy. Graphium Core's injected writer remains the sole physical write authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Protocol

from graphium.domain.document_identity import DocumentFileState
from graphium.domain.document_save import SaveTargetObservation

from .companion_storage import CheckedTextAuthority, CompanionAuthorityError
from .scratchpad import scratchpad_path
from .workspace.operations import WorkspacePathToken, workspace_path_token


class CompanionWriterPort(Protocol):
    def observe_target(self, path: str) -> SaveTargetObservation: ...
    def commit(self, observation: SaveTargetObservation, data: bytes): ...


def _require_writer_port(writer: object) -> CompanionWriterPort:
    if not callable(getattr(writer, "observe_target", None)) or not callable(
        getattr(writer, "commit", None)
    ):
        raise TypeError("writer must provide observe_target() and commit()")
    return writer  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class ScratchpadSaveAsPlan:
    source_document_path: str | None
    target_document_path: str
    source_path: str | None
    target_path: str | None
    source_token: WorkspacePathToken | None
    source_bytes: bytes | None
    target_observation: SaveTargetObservation | None
    binding_change_expected: bool


@dataclass(frozen=True, slots=True)
class ScratchpadSaveAsResult:
    copied: bool
    source_existed: bool
    target_path: str | None


def _strong_token(path: Path) -> WorkspacePathToken | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(value.st_mode):
        raise CompanionAuthorityError("Managed Scratchpad companion must not be a symbolic link.")
    if not stat.S_ISREG(value.st_mode):
        raise CompanionAuthorityError("Managed Scratchpad companion must be a regular file.")
    return workspace_path_token(value)


def _same_active_object(
    target: SaveTargetObservation,
    active_file_state: DocumentFileState | None,
) -> bool:
    if active_file_state is None or target.existing is None:
        return False
    active_id = active_file_state.binding.object_id
    return active_id is not None and target.existing.object_id == active_id


def prepare_scratchpad_save_as(
    *,
    writer: CompanionWriterPort,
    source_document_path: str | None,
    target_document_path: str,
    active_file_state: DocumentFileState | None,
) -> ScratchpadSaveAsPlan:
    """Freeze source saved bytes and reserve the managed target before primary Save As.

    The target document is observed only to distinguish a chooser alias of the current
    active object. The Core lifecycle will perform its own authoritative observation and
    overwrite confirmation for the document itself.
    """
    writer = _require_writer_port(writer)
    if not isinstance(target_document_path, str) or not target_document_path.strip():
        raise ValueError("target_document_path must be a non-empty string")

    target_document = os.path.abspath(target_document_path)
    target_document_observation = writer.observe_target(target_document)
    if _same_active_object(target_document_observation, active_file_state):
        return ScratchpadSaveAsPlan(
            source_document_path=source_document_path,
            target_document_path=target_document,
            source_path=None,
            target_path=None,
            source_token=None,
            source_bytes=None,
            target_observation=None,
            binding_change_expected=False,
        )

    target_sidecar = scratchpad_path(target_document)
    assert target_sidecar is not None
    target_observation = writer.observe_target(str(target_sidecar))
    if target_observation.existing is not None:
        raise CompanionAuthorityError(
            "The Save As destination already has a managed Scratchpad companion; "
            "Graphium will not overwrite or inherit it."
        )

    if source_document_path is None:
        return ScratchpadSaveAsPlan(
            source_document_path=None,
            target_document_path=target_document,
            source_path=None,
            target_path=str(target_sidecar),
            source_token=None,
            source_bytes=None,
            target_observation=target_observation,
            binding_change_expected=True,
        )

    source_document = os.path.abspath(source_document_path)
    source_sidecar = scratchpad_path(source_document)
    assert source_sidecar is not None
    token_before = _strong_token(source_sidecar)
    if token_before is None:
        return ScratchpadSaveAsPlan(
            source_document_path=source_document,
            target_document_path=target_document,
            source_path=str(source_sidecar),
            target_path=str(target_sidecar),
            source_token=None,
            source_bytes=None,
            target_observation=target_observation,
            binding_change_expected=True,
        )

    snapshot = CheckedTextAuthority(writer, source_sidecar).load()
    token_after = _strong_token(source_sidecar)
    if token_after != token_before or not snapshot.token.exists:
        raise CompanionAuthorityError("Managed Scratchpad companion changed during Save As preflight.")
    payload = snapshot.text.encode("utf-8")
    if len(payload) != token_before.size or hashlib.sha256(payload).hexdigest() != snapshot.token.sha256:
        raise CompanionAuthorityError("Managed Scratchpad companion bytes could not be verified.")

    return ScratchpadSaveAsPlan(
        source_document_path=source_document,
        target_document_path=target_document,
        source_path=str(source_sidecar),
        target_path=str(target_sidecar),
        source_token=token_before,
        source_bytes=payload,
        target_observation=target_observation,
        binding_change_expected=True,
    )



def scratchpad_document_target_safe_to_bind(
    *,
    writer: CompanionWriterPort,
    target_document_path: str,
) -> bool:
    """Return whether the managed target sidecar is still provably absent.

    This is a post-primary-commit safety question, not an ownership claim.  Any
    present or unobservable companion must not be adopted automatically after a
    failed Save As companion step.
    """
    writer = _require_writer_port(writer)
    target_sidecar = scratchpad_path(os.path.abspath(target_document_path))
    if target_sidecar is None:
        return True
    try:
        observed = writer.observe_target(str(target_sidecar))
    except Exception:
        return False
    return observed.existing is None


def scratchpad_save_as_target_safe_to_bind(
    *,
    writer: CompanionWriterPort,
    plan: ScratchpadSaveAsPlan,
) -> bool:
    if not isinstance(plan, ScratchpadSaveAsPlan):
        raise TypeError("plan must be ScratchpadSaveAsPlan")
    if not plan.binding_change_expected or plan.target_path is None:
        return True
    return scratchpad_document_target_safe_to_bind(
        writer=writer, target_document_path=plan.target_document_path
    )

def commit_scratchpad_save_as(
    *,
    writer: CompanionWriterPort,
    plan: ScratchpadSaveAsPlan,
) -> ScratchpadSaveAsResult:
    """Commit the previously frozen sidecar copy after primary Save As succeeded."""
    writer = _require_writer_port(writer)
    if not isinstance(plan, ScratchpadSaveAsPlan):
        raise TypeError("plan must be ScratchpadSaveAsPlan")
    if not plan.binding_change_expected:
        return ScratchpadSaveAsResult(False, False, None)
    if plan.target_path is None or plan.target_observation is None:
        raise CompanionAuthorityError("Scratchpad Save As plan is incomplete.")

    target_path = Path(plan.target_path)
    if plan.source_token is None:
        if plan.source_path is not None and _strong_token(Path(plan.source_path)) is not None:
            raise CompanionAuthorityError(
                "A source Scratchpad appeared during Save As; it was not copied automatically."
            )
        current_target = writer.observe_target(str(target_path))
        if current_target.existing is not None:
            raise CompanionAuthorityError(
                "A managed Scratchpad appeared at the Save As destination during commit."
            )
        return ScratchpadSaveAsResult(False, False, str(target_path))

    assert plan.source_path is not None
    assert plan.source_bytes is not None
    source_path = Path(plan.source_path)
    if _strong_token(source_path) != plan.source_token:
        raise CompanionAuthorityError("Source Scratchpad changed before Save As companion copy.")
    current = CheckedTextAuthority(writer, source_path).load()
    current_bytes = current.text.encode("utf-8")
    if (
        _strong_token(source_path) != plan.source_token
        or current_bytes != plan.source_bytes
        or hashlib.sha256(current_bytes).hexdigest() != current.token.sha256
    ):
        raise CompanionAuthorityError("Source Scratchpad changed before Save As companion copy.")

    try:
        writer.commit(plan.target_observation, plan.source_bytes)
    except Exception as exc:
        raise CompanionAuthorityError(f"Scratchpad companion copy failed: {exc}") from exc

    copied = CheckedTextAuthority(writer, target_path).load()
    if copied.text.encode("utf-8") != plan.source_bytes:
        raise CompanionAuthorityError("Scratchpad companion verification failed after Save As.")
    if _strong_token(source_path) != plan.source_token:
        raise CompanionAuthorityError(
            "Source Scratchpad changed while the Save As companion copy was committing."
        )
    return ScratchpadSaveAsResult(True, True, str(target_path))
