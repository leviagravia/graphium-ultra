"""Final stale gates for one derived Pandoc artifact publication.

This module owns no physical writer, GTK, subprocess, thread, document state or
persistent export state.  It only orchestrates one already-injected Core writer
for the final derived-output commit after all frozen semantic inputs are fresh.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from graphium_plus.pandoc import PandocExportPlan, pandoc_plan_matches_current_source
from graphium_plus.pandoc_process import PandocArtifact
from graphium_plus.references import ReferenceFileToken


class PandocWriterPort(Protocol):
    def observe_target(self, path: str): ...
    def commit(self, observation, data: bytes): ...


class PandocPublicationError(RuntimeError):
    """Publication was refused before the final Core writer commit."""


@dataclass(frozen=True, slots=True)
class PandocPublicationResult:
    write_result: object


def publish_pandoc_artifact(
    *,
    plan: PandocExportPlan,
    artifact: PandocArtifact,
    current_text: str,
    current_state_id: int,
    current_reference_token: ReferenceFileToken,
    writer: PandocWriterPort,
    target_observation,
) -> PandocPublicationResult:
    if not isinstance(plan, PandocExportPlan):
        raise TypeError("plan must be PandocExportPlan")
    if not isinstance(artifact, PandocArtifact) or not artifact.succeeded:
        raise PandocPublicationError("Pandoc did not produce a publishable artifact.")
    if writer is None or not callable(getattr(writer, "observe_target", None)) or not callable(getattr(writer, "commit", None)):
        raise TypeError("writer must provide the Core observe_target/commit authority")

    fresh, reason = pandoc_plan_matches_current_source(
        plan,
        current_text=current_text,
        current_state_id=current_state_id,
        current_reference_token=current_reference_token,
    )
    if not fresh:
        raise PandocPublicationError(reason)

    current_target = writer.observe_target(plan.destination)
    if current_target != target_observation:
        raise PandocPublicationError("The output destination changed while Pandoc was running.")

    return PandocPublicationResult(writer.commit(target_observation, artifact.data))
