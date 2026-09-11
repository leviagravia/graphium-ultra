"""Pure, self-validating crash-recovery artifact format for Graphium.

Recovery storage is deliberately distinct from document serialization. The body is always
strict UTF-8 normalized editor text; user-selected encoding/BOM/EOL are descriptive
metadata only. This module performs no filesystem I/O and has no GTK dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import struct
import uuid

from .document_identity import BomKind, LineEnding
from .document_serialization import DocumentSerializationProfile


RECOVERY_FORMAT_VERSION = 1
RECOVERY_MAGIC = b"GRAPHIUM-RECOVERY\x00"
_HEADER_LENGTH = struct.Struct(">I")
_MAX_HEADER_BYTES = 64 * 1024
_SHA256_HEX_LEN = 64


class RecoveryArtifactError(ValueError):
    """Base class for malformed or unsupported recovery artifact data."""


class CorruptRecoveryArtifactError(RecoveryArtifactError):
    """Recovery bytes fail structural, UTF-8, length or digest validation."""


class RecoveryDocumentKind(str, Enum):
    NAMED = "named"
    UNTITLED = "untitled"


def canonical_recovery_uuid(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("artifact UUID must be a non-empty canonical UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("artifact UUID is invalid") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ValueError("artifact UUID must use canonical lowercase UUID spelling")
    return canonical


def new_recovery_uuid() -> str:
    return str(uuid.uuid4())


def _sha256_hex(value: str, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_HEX_LEN:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True)
class RecoveryNamedBaseline:
    """Descriptive copy of the accepted named-document baseline, never authority."""

    logical_path: str
    canonical_path: str | None
    device: int | None
    inode: int | None
    content_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.logical_path, str) or not self.logical_path:
            raise ValueError("logical_path must be a non-empty string")
        if self.canonical_path is not None and (
            not isinstance(self.canonical_path, str) or not self.canonical_path
        ):
            raise ValueError("canonical_path must be a non-empty string or None")
        if (self.device is None) != (self.inode is None):
            raise ValueError("device and inode must either both be known or both be None")
        if self.device is not None:
            if not isinstance(self.device, int) or isinstance(self.device, bool) or self.device < 0:
                raise ValueError("device must be a non-negative integer")
            if not isinstance(self.inode, int) or isinstance(self.inode, bool) or self.inode <= 0:
                raise ValueError("inode must be a positive integer")
        _sha256_hex(self.content_sha256, field="content_sha256")


@dataclass(frozen=True)
class RecoveryRecord:
    artifact_uuid: str
    captured_at_ns: int
    generation: int
    state_token: int
    text: str
    current_profile: DocumentSerializationProfile
    saved_profile: DocumentSerializationProfile
    document_kind: RecoveryDocumentKind
    named_baseline: RecoveryNamedBaseline | None = None

    def __post_init__(self) -> None:
        canonical_recovery_uuid(self.artifact_uuid)
        if not isinstance(self.captured_at_ns, int) or isinstance(self.captured_at_ns, bool) or self.captured_at_ns <= 0:
            raise ValueError("captured_at_ns must be a positive integer")
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation <= 0:
            raise ValueError("generation must be a positive integer")
        if not isinstance(self.state_token, int) or isinstance(self.state_token, bool) or self.state_token <= 0:
            raise ValueError("state_token must be a positive integer")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not isinstance(self.current_profile, DocumentSerializationProfile):
            raise TypeError("current_profile must be DocumentSerializationProfile")
        if not isinstance(self.saved_profile, DocumentSerializationProfile):
            raise TypeError("saved_profile must be DocumentSerializationProfile")
        if not isinstance(self.document_kind, RecoveryDocumentKind):
            raise TypeError("document_kind must be RecoveryDocumentKind")
        if self.document_kind is RecoveryDocumentKind.NAMED:
            if not isinstance(self.named_baseline, RecoveryNamedBaseline):
                raise ValueError("named recovery records require named_baseline")
        elif self.named_baseline is not None:
            raise ValueError("Untitled recovery records must not carry named_baseline")


def _profile_to_json(profile: DocumentSerializationProfile) -> dict[str, object]:
    return {
        "encoding": profile.encoding,
        "bom": profile.bom.value,
        "line_ending": profile.line_ending.value,
        "mixed_source": profile.mixed_source,
    }


def _profile_from_json(value: object) -> DocumentSerializationProfile:
    if not isinstance(value, dict) or set(value) != {
        "encoding", "bom", "line_ending", "mixed_source"
    }:
        raise CorruptRecoveryArtifactError("invalid recovery representation profile")
    encoding = value["encoding"]
    mixed = value["mixed_source"]
    if not isinstance(encoding, str) or not encoding or not isinstance(mixed, bool):
        raise CorruptRecoveryArtifactError("invalid recovery representation profile")
    try:
        bom = BomKind(value["bom"])
        line_ending = LineEnding(value["line_ending"])
        return DocumentSerializationProfile(encoding, bom, line_ending, mixed)
    except (TypeError, ValueError) as exc:
        raise CorruptRecoveryArtifactError("invalid recovery representation profile") from exc


def _baseline_to_json(value: RecoveryNamedBaseline | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "logical_path": value.logical_path,
        "canonical_path": value.canonical_path,
        "device": value.device,
        "inode": value.inode,
        "content_sha256": value.content_sha256,
    }


def _baseline_from_json(value: object) -> RecoveryNamedBaseline | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "logical_path", "canonical_path", "device", "inode", "content_sha256"
    }:
        raise CorruptRecoveryArtifactError("invalid recovery named baseline")
    try:
        return RecoveryNamedBaseline(
            logical_path=value["logical_path"],
            canonical_path=value["canonical_path"],
            device=value["device"],
            inode=value["inode"],
            content_sha256=value["content_sha256"],
        )
    except (TypeError, ValueError) as exc:
        raise CorruptRecoveryArtifactError("invalid recovery named baseline") from exc


def encode_recovery_record(record: RecoveryRecord) -> bytes:
    if not isinstance(record, RecoveryRecord):
        raise TypeError("record must be RecoveryRecord")
    body = record.text.encode("utf-8", errors="strict")
    header = {
        "format_version": RECOVERY_FORMAT_VERSION,
        "artifact_uuid": record.artifact_uuid,
        "captured_at_ns": record.captured_at_ns,
        "generation": record.generation,
        "state_token": record.state_token,
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "current_profile": _profile_to_json(record.current_profile),
        "saved_profile": _profile_to_json(record.saved_profile),
        "document_kind": record.document_kind.value,
        "named_baseline": _baseline_to_json(record.named_baseline),
    }
    header_bytes = json.dumps(
        header, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8", errors="strict")
    if len(header_bytes) > _MAX_HEADER_BYTES:
        raise RecoveryArtifactError("recovery header is unexpectedly large")
    return RECOVERY_MAGIC + _HEADER_LENGTH.pack(len(header_bytes)) + header_bytes + body


def decode_recovery_record(payload: bytes) -> RecoveryRecord:
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    prefix = len(RECOVERY_MAGIC) + _HEADER_LENGTH.size
    if len(payload) < prefix or not payload.startswith(RECOVERY_MAGIC):
        raise CorruptRecoveryArtifactError("recovery magic/header is missing")
    header_length = _HEADER_LENGTH.unpack(
        payload[len(RECOVERY_MAGIC):prefix]
    )[0]
    if header_length <= 0 or header_length > _MAX_HEADER_BYTES:
        raise CorruptRecoveryArtifactError("recovery header length is invalid")
    body_offset = prefix + header_length
    if body_offset > len(payload):
        raise CorruptRecoveryArtifactError("recovery header is truncated")
    header_bytes = payload[prefix:body_offset]
    body = payload[body_offset:]
    try:
        header = json.loads(header_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptRecoveryArtifactError("recovery header is not valid UTF-8 JSON") from exc
    expected_keys = {
        "format_version", "artifact_uuid", "captured_at_ns", "generation", "state_token",
        "body_length", "body_sha256", "current_profile", "saved_profile",
        "document_kind", "named_baseline",
    }
    if not isinstance(header, dict) or set(header) != expected_keys:
        raise CorruptRecoveryArtifactError("recovery header schema is invalid")
    if type(header["format_version"]) is not int or header["format_version"] != RECOVERY_FORMAT_VERSION:
        raise CorruptRecoveryArtifactError("recovery format version is unsupported")
    body_length = header["body_length"]
    if not isinstance(body_length, int) or isinstance(body_length, bool) or body_length < 0:
        raise CorruptRecoveryArtifactError("recovery body length is invalid")
    if body_length != len(body):
        raise CorruptRecoveryArtifactError("recovery body length does not match payload")
    body_sha = header["body_sha256"]
    try:
        _sha256_hex(body_sha, field="body_sha256")
    except ValueError as exc:
        raise CorruptRecoveryArtifactError("recovery body digest is invalid") from exc
    if hashlib.sha256(body).hexdigest() != body_sha:
        raise CorruptRecoveryArtifactError("recovery body digest mismatch")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CorruptRecoveryArtifactError("recovery body is not strict UTF-8") from exc
    try:
        kind = RecoveryDocumentKind(header["document_kind"])
        baseline = _baseline_from_json(header["named_baseline"])
        return RecoveryRecord(
            artifact_uuid=header["artifact_uuid"],
            captured_at_ns=header["captured_at_ns"],
            generation=header["generation"],
            state_token=header["state_token"],
            text=text,
            current_profile=_profile_from_json(header["current_profile"]),
            saved_profile=_profile_from_json(header["saved_profile"]),
            document_kind=kind,
            named_baseline=baseline,
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CorruptRecoveryArtifactError):
            raise
        raise CorruptRecoveryArtifactError("recovery metadata is invalid") from exc
