"""Provenance tracking for build artifacts (spec GRC-001 §6 GRC-SEC-005, §5.3).

Supply-chain guarantees:

- Build dependencies SHALL be locked (the lockfile bytes are hashed and the
  digest recorded on every record).
- Network-fetched inputs SHALL be digest verified (the artifact checksum is
  computed at build time and re-checked at verify time).
- Build outputs SHALL receive checksums, an SBOM, and provenance.
- Signing keys SHALL remain in an external signer or secret provider and SHALL
  never appear in prompts, logs, generated scripts, or artifacts.

The signing-key guarantee is structural: :func:`build_provenance` has NO
parameter that accepts key material. The only signing-related input is
``signature_state`` — an enum recording the OUTCOME of an external signing
operation. A signing key can therefore never enter this module.

The SBOM is emitted in CycloneDX 1.5 shape (``bomFormat == "CycloneDX"``) and
the attestation in in-toto v1 statement shape, so a downstream verifier or a
hosting provider's release UI can consume them without an adapter.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Attestation",
    "ProvenanceRecord",
    "SignatureState",
    "VerificationResult",
    "build_provenance",
    "verify_provenance",
]

_INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class SignatureState(StrEnum):
    """Outcome of an external signing operation.

    The enum models the *result* of signing, never the act of signing — key
    material lives in the external signer (spec GRC-SEC-005).
    """

    UNSIGNED = "unsigned"
    VERIFIED = "verified"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True)
class Attestation:
    """in-toto v1 statement wrapper (spec §5.3 provenance.attestation).

    ``statement`` is the in-toto Statement dict (``_type``, ``subject``,
    ``predicateType``, ``predicate``). ``digest`` is the sha256 of the
    canonical-JSON serialization of the statement. Verification recomputes it
    and compares it with the payload digest returned by the external signer;
    copying this field from an unverified record is not signature evidence.
    """

    predicate_type: str
    statement: Mapping[str, Any]
    digest: str


@dataclass(frozen=True)
class ProvenanceRecord:
    """Spec §5.3 provenance sub-record, expanded for GRC-SEC-005.

    Fields mirror spec §5.3 (``sbom``, ``signature``/``signature_state``,
    ``attestation``, ``builder_identity``) plus ``dependency_lock_digest`` and
    ``artifact_digest`` which carry the digest-verification guarantees from
    GRC-SEC-005.
    """

    sbom: Mapping[str, Any]
    signature_state: SignatureState
    attestation: Attestation | None
    builder_identity: str
    dependency_lock_digest: str
    artifact_digest: str
    subject: str


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of :func:`verify_provenance`.

    ``ok`` is True only when EVERY precondition held. ``reasons`` carries
    stable reason strings (lowercase, hyphenated) so an operator can diagnose
    a failed record without re-running the build.
    """

    ok: bool
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_cyclonedx_sbom(
    *,
    dependency_lock: Mapping[str, Any],
    artifact_name: str,
    artifact_digest: str,
    builder_identity: str,
) -> dict[str, Any]:
    """Build a minimal CycloneDX 1.5 SBOM from a parsed dependency lock.

    The lockfile format here is the simple ``{"packages": {name: version}}``
    mapping emitted by the planner; a provider adapter may translate another
    format (pip-requirements, package-lock, Cargo.lock) into this shape.
    """
    packages = dependency_lock.get("packages", {}) or {}
    components: list[dict[str, Any]] = []
    for name, version in packages.items():
        components.append(
            {
                "type": "library",
                "name": str(name),
                "version": str(version),
            }
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": str(dependency_lock.get("generated_at", "")),
            "tools": [
                {
                    "vendor": "general-ludd",
                    "name": "git-release-captain",
                    "version": "1.0",
                }
            ],
            "component": {
                "type": "application",
                "name": artifact_name,
                "bom-ref": f"sha256:{artifact_digest}",
            },
            "supplier": {"name": builder_identity},
        },
        "components": components,
    }


def _build_intoto_statement(
    *,
    artifact_name: str,
    artifact_digest: str,
    lock_digest: str,
    builder_identity: str,
    predicate_type: str,
) -> dict[str, Any]:
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": artifact_name,
                "digest": {"sha256": artifact_digest},
            }
        ],
        "predicateType": predicate_type,
        "predicate": {
            "builder": {"id": builder_identity},
            "buildType": "https://general-ludd/gludd/build/v1",
            "materials": [
                {
                    "uri": "dependency-lock",
                    "digest": {"sha256": lock_digest},
                }
            ],
        },
    }


def _canonical_json_bytes(obj: Mapping[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return value


def _as_sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return None


def _verify_signature_binding(
    attestation: Attestation | None,
    *,
    expected_signature_state: SignatureState,
    verified_attestation_digest: str | None,
) -> list[str]:
    """Bind the record to the payload digest returned by an external verifier."""
    if expected_signature_state is not SignatureState.VERIFIED:
        return []
    if verified_attestation_digest is None:
        return ["signature-payload-digest-missing"]
    if _SHA256_RE.fullmatch(verified_attestation_digest) is None:
        return ["signature-payload-digest-invalid"]
    if attestation is None or not hmac.compare_digest(
        verified_attestation_digest,
        attestation.digest,
    ):
        return ["signature-payload-digest-mismatch"]
    return []


def _verify_attestation_binding(
    record: ProvenanceRecord,
    *,
    expected_predicate_type: str,
) -> list[str]:
    """Cross-bind the in-toto statement to every immutable record identity."""
    attestation = record.attestation
    if attestation is None:
        return ["attestation-missing"]

    reasons: list[str] = []
    try:
        canonical_digest = _sha256_hex(_canonical_json_bytes(attestation.statement))
    except (TypeError, ValueError):
        canonical_digest = ""
        reasons.append("attestation-not-canonical-json")
    if canonical_digest != attestation.digest:
        reasons.append("attestation-digest-mismatch")

    statement = _as_mapping(attestation.statement)
    if statement is None:
        reasons.append("attestation-statement-invalid")
        return reasons
    if statement.get("_type") != _INTOTO_STATEMENT_TYPE:
        reasons.append("attestation-statement-type-mismatch")
    if (
        attestation.predicate_type != expected_predicate_type
        or statement.get("predicateType") != expected_predicate_type
    ):
        reasons.append("attestation-predicate-type-mismatch")

    subjects = _as_sequence(statement.get("subject"))
    if not subjects:
        reasons.append("attestation-subject-missing")
    elif len(subjects) != 1:
        reasons.append("attestation-subject-cardinality-invalid")
    else:
        subject = _as_mapping(subjects[0])
        if subject is None:
            reasons.append("attestation-subject-invalid")
        else:
            if subject.get("name") != record.subject:
                reasons.append("attestation-subject-mismatch")
            digest = _as_mapping(subject.get("digest"))
            if digest is None or digest.get("sha256") != record.artifact_digest:
                reasons.append("attestation-artifact-digest-mismatch")

    predicate = _as_mapping(statement.get("predicate"))
    if predicate is None:
        reasons.extend(
            [
                "attestation-builder-missing",
                "attestation-lock-digest-mismatch",
            ]
        )
        return reasons

    builder = _as_mapping(predicate.get("builder"))
    if builder is None or not builder.get("id"):
        reasons.append("attestation-builder-missing")
    elif builder.get("id") != record.builder_identity:
        reasons.append("attestation-builder-mismatch")

    materials = _as_sequence(predicate.get("materials"))
    lock_digest_bound = False
    if materials is not None:
        for value in materials:
            material = _as_mapping(value)
            if material is None or material.get("uri") != "dependency-lock":
                continue
            digest = _as_mapping(material.get("digest"))
            if digest is not None and digest.get("sha256") == record.dependency_lock_digest:
                lock_digest_bound = True
                break
    if not lock_digest_bound:
        reasons.append("attestation-lock-digest-mismatch")
    return reasons


def _verify_sbom_binding(record: ProvenanceRecord) -> list[str]:
    """Cross-bind the CycloneDX metadata to the provenance subject and builder."""
    sbom = _as_mapping(record.sbom)
    if sbom is None or sbom.get("bomFormat") != "CycloneDX":
        return ["sbom-missing-or-wrong-format"]
    reasons: list[str] = []
    if not sbom.get("components"):
        reasons.append("sbom-empty-components")
    metadata = _as_mapping(sbom.get("metadata"))
    component = _as_mapping(metadata.get("component")) if metadata is not None else None
    supplier = _as_mapping(metadata.get("supplier")) if metadata is not None else None
    if component is None or component.get("name") != record.subject:
        reasons.append("sbom-subject-mismatch")
    if component is None or component.get("bom-ref") != f"sha256:{record.artifact_digest}":
        reasons.append("sbom-artifact-digest-mismatch")
    if supplier is None or supplier.get("name") != record.builder_identity:
        reasons.append("sbom-builder-mismatch")
    return reasons


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_provenance(
    *,
    artifact_name: str,
    artifact_bytes: bytes,
    dependency_lock_bytes: bytes,
    dependency_lock: Mapping[str, Any] | None = None,
    builder_identity: str,
    predicate_type: str = "https://slsa.dev/provenance/v1",
    signature_state: SignatureState = SignatureState.UNSIGNED,
) -> ProvenanceRecord:
    """Generate a :class:`ProvenanceRecord` from a lockfile + artifact.

    Per spec GRC-SEC-005, this function NEVER accepts key material. Only the
    outcome of an external signing operation (``signature_state``) is recorded.
    A caller that needs to sign must invoke its external signer, capture the
    resulting state, and pass it in here.

    Args:
        artifact_name: logical name of the build output.
        artifact_bytes: raw bytes of the build output; hashed for the checksum.
        dependency_lock_bytes: raw bytes of the dependency lock; hashed for the
            lock digest.
        dependency_lock: parsed lockfile (``{"packages": {name: version}}``).
            If omitted, the lockfile is parsed from ``dependency_lock_bytes``.
        builder_identity: stable identifier of the builder (e.g.
            ``github-actions:runner-01``). Anonymous builds are forbidden.
        predicate_type: SLSA / in-toto predicate type URI.
        signature_state: outcome of the external signing step.

    Raises:
        ValueError: if ``builder_identity`` is empty (spec §5.3 requires a
            non-null builder_identity).
    """
    if not builder_identity:
        raise ValueError("builder_identity is required (spec §5.3 forbids anonymous artifacts)")
    if not artifact_bytes:
        raise ValueError("artifact_bytes is required")
    if not dependency_lock_bytes:
        raise ValueError("dependency_lock_bytes is required (GRC-SEC-005: deps SHALL be locked)")

    artifact_digest = _sha256_hex(artifact_bytes)
    lock_digest = _sha256_hex(dependency_lock_bytes)
    parsed_lock: Mapping[str, Any] = (
        dependency_lock if dependency_lock is not None else json.loads(dependency_lock_bytes.decode("utf-8"))
    )

    sbom = _build_cyclonedx_sbom(
        dependency_lock=parsed_lock,
        artifact_name=artifact_name,
        artifact_digest=artifact_digest,
        builder_identity=builder_identity,
    )
    statement = _build_intoto_statement(
        artifact_name=artifact_name,
        artifact_digest=artifact_digest,
        lock_digest=lock_digest,
        builder_identity=builder_identity,
        predicate_type=predicate_type,
    )
    attestation = Attestation(
        predicate_type=predicate_type,
        statement=statement,
        digest=_sha256_hex(_canonical_json_bytes(statement)),
    )

    return ProvenanceRecord(
        sbom=sbom,
        signature_state=signature_state,
        attestation=attestation,
        builder_identity=builder_identity,
        dependency_lock_digest=lock_digest,
        artifact_digest=artifact_digest,
        subject=artifact_name,
    )


def verify_provenance(
    record: ProvenanceRecord,
    *,
    expected_artifact_bytes: bytes | None = None,
    expected_lock_bytes: bytes | None = None,
    expected_signature_state: SignatureState = SignatureState.VERIFIED,
    verified_attestation_digest: str | None = None,
    expected_predicate_type: str = _SLSA_PROVENANCE_TYPE,
) -> VerificationResult:
    """Check signature, checksums, and SBOM completeness for ``record``.

    Returns a :class:`VerificationResult`; ``ok`` is True only when every
    precondition held. Reasons are lowercase + hyphenated so they can be
    matched by prefix (``"signature-*"``, ``"artifact-*"``, ``"sbom-*"``).

    Args:
        record: Provenance record to verify.
        expected_artifact_bytes: Artifact bytes independently obtained by the
            deploy or release-page verifier.
        expected_lock_bytes: Dependency-lock bytes independently obtained from
            the pinned source tree.
        expected_signature_state: Required outcome from the external signer.
        verified_attestation_digest: Canonical payload SHA-256 returned by the
            external signature verifier. A verified release fails closed when
            this value is absent; it must not be copied from ``record``.
        expected_predicate_type: Exact signed predicate type allowed by policy.
    """
    reasons: list[str] = []

    # 1. Signature state and the exact payload digest reported by the external
    # signer. A VERIFIED enum alone is a claim, not cryptographic evidence.
    if record.signature_state != expected_signature_state:
        reasons.append(f"signature-state-{record.signature_state.value}-expected-{expected_signature_state.value}")
    reasons.extend(
        _verify_signature_binding(
            record.attestation,
            expected_signature_state=expected_signature_state,
            verified_attestation_digest=verified_attestation_digest,
        )
    )

    # 2. Provenance chain completeness and immutable cross-binding. A valid
    # signature over the wrong subject or predicate must still fail policy.
    reasons.extend(_verify_sbom_binding(record))
    reasons.extend(
        _verify_attestation_binding(
            record,
            expected_predicate_type=expected_predicate_type,
        )
    )

    # 3. Dependency-lock digest.
    if expected_lock_bytes is not None:
        actual_lock_digest = _sha256_hex(expected_lock_bytes)
        if actual_lock_digest != record.dependency_lock_digest:
            reasons.append(f"dependency-lock-digest-mismatch-expected-{actual_lock_digest[:12]}")

    # 4. Artifact digest.
    if expected_artifact_bytes is not None:
        actual_artifact_digest = _sha256_hex(expected_artifact_bytes)
        if actual_artifact_digest != record.artifact_digest:
            reasons.append(f"artifact-digest-mismatch-expected-{actual_artifact_digest[:12]}")

    return VerificationResult(ok=not reasons, reasons=reasons)
