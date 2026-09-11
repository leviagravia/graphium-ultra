"""Pure strong read-only filesystem observation values for Graphium."""
from __future__ import annotations

from dataclasses import dataclass

from .document_identity import ContentFingerprint, DiskObservation, DocumentFileBinding


@dataclass(frozen=True)
class StrongDocumentObservation:
    binding: DocumentFileBinding
    disk: DiskObservation
    content_fingerprint: ContentFingerprint


@dataclass(frozen=True)
class ObservedDocumentBytes:
    observation: StrongDocumentObservation
    raw: bytes
