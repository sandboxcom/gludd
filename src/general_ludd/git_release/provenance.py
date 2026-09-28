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
    "ArtifactVerificationReceipt",
    "Attestation",
    "ProvenanceRecord",
    "ReceiptPurpose",
    "SignatureState",
    "VerificationResult",
    "build_provenance",
    "verify_artifact_receipt",
    "verify_provenance",
]

_INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
_BUILD_TYPE = "https://general-ludd/gludd/build/v1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class SignatureState(StrEnum):
    """Outcome of an external signing operation.

    The enum models the *result* of signing, never the act of signing — key
    material lives in the external signer (spec GRC-SEC-005).
    """

    UNSIGNED = "unsigned"
    VERIFIED = "verified"
    FAILED = "failed"
    MISSING = "missing"


class ReceiptPurpose(StrEnum):
    """One mutation authorized by an exact provenance verification."""

    DEPLOY = "deploy"
    ROLLBACK = "rollback"


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
    release_id: str
    source_sha: str


@dataclass(frozen=True)
class ArtifactVerificationReceipt:
    """Immutable, digest-bound authorization emitted by the verifier only.

    Provenance identity describes the signed build. Authorization identity
    describes the release operation consuming it; the two differ for rollback,
    where a current release authorizes restoring a prior release artifact.
    """

    purpose: ReceiptPurpose
    authorization_id: str
    authorization_source_sha: str
    deployment_target: str
    provenance_release_id: str
    provenance_source_sha: str
    subject: str
    artifact_digest: str
    dependency_lock_digest: str
    predicate_type: str
    builder_identity: str
    attestation_digest: str
    signer_payload_digest: str
    sbom_digest: str
    receipt_digest: str


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of :func:`verify_provenance`.

    ``ok`` is True only when EVERY precondition held. ``reasons`` carries
    stable reason strings (lowercase, hyphenated) so an operator can diagnose
    a failed record without re-running the build.
    """

    ok: bool
    reasons: list[str] = field(default_factory=list)
    receipt: ArtifactVerificationReceipt | None = None


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
    release_id: str,
    source_sha: str,
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
            "buildDefinition": {
                "buildType": _BUILD_TYPE,
                "externalParameters": {
                    "releaseId": release_id,
                    "sourceSha": source_sha,
                },
                "internalParameters": {},
                "resolvedDependencies": [
                    {
                        "uri": "dependency-lock",
                        "digest": {"sha256": lock_digest},
                    }
                ],
            },
            "runDetails": {
                "builder": {"id": builder_identity},
                "metadata": {"invocationId": release_id},
            },
        },
    }


def _canonical_json_bytes(obj: Mapping[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _bounded_text(value: object, field_name: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(char in value for char in "\r\n\x00")
    ):
        raise ValueError(f"{field_name} must be bounded non-empty text")
    return value


def _lock_packages(lock: Mapping[str, Any]) -> dict[str, str] | None:
    packages = lock.get("packages")
    if not isinstance(packages, Mapping):
        return None
    normalized: dict[str, str] = {}
    for name, version in packages.items():
        if not isinstance(name, str) or not name or name in normalized:
            return None
        if not isinstance(version, str) or not version:
            return None
        normalized[name] = version
    return normalized


def _validated_dependency_lock(
    dependency_lock_bytes: bytes,
    dependency_lock: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    """Decode a lockfile and reject any mismatched parsed representation."""
    try:
        decoded_lock = json.loads(dependency_lock_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("dependency_lock_bytes must contain JSON") from exc
    if not isinstance(decoded_lock, Mapping) or _lock_packages(decoded_lock) is None:
        raise ValueError("dependency_lock_bytes must contain a packages mapping")
    parsed_lock: Mapping[str, Any] = decoded_lock
    if dependency_lock is not None:
        try:
            parsed_bytes = _canonical_json_bytes(parsed_lock)
            supplied_bytes = _canonical_json_bytes(dependency_lock)
        except (TypeError, ValueError) as exc:
            raise ValueError("dependency_lock must be JSON serializable") from exc
        if not hmac.compare_digest(parsed_bytes, supplied_bytes):
            raise ValueError("dependency_lock does not match dependency_lock_bytes")
    return parsed_lock


def _receipt_payload(receipt: ArtifactVerificationReceipt) -> dict[str, str]:
    return {
        "attestation_digest": receipt.attestation_digest,
        "artifact_digest": receipt.artifact_digest,
        "authorization_id": receipt.authorization_id,
        "authorization_source_sha": receipt.authorization_source_sha,
        "builder_identity": receipt.builder_identity,
        "dependency_lock_digest": receipt.dependency_lock_digest,
        "deployment_target": receipt.deployment_target,
        "predicate_type": receipt.predicate_type,
        "provenance_release_id": receipt.provenance_release_id,
        "provenance_source_sha": receipt.provenance_source_sha,
        "purpose": receipt.purpose.value,
        "sbom_digest": receipt.sbom_digest,
        "signer_payload_digest": receipt.signer_payload_digest,
        "subject": receipt.subject,
    }


def _receipt_digest(receipt: ArtifactVerificationReceipt) -> str:
    return _sha256_hex(_canonical_json_bytes(_receipt_payload(receipt)))


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
                "attestation-release-context-missing",
            ]
        )
        return reasons

    build_definition = _as_mapping(predicate.get("buildDefinition"))
    run_details = _as_mapping(predicate.get("runDetails"))
    builder = (
        _as_mapping(run_details.get("builder"))
        if run_details is not None
        else None
    )
    if builder is None or not builder.get("id"):
        reasons.append("attestation-builder-missing")
    elif builder.get("id") != record.builder_identity:
        reasons.append("attestation-builder-mismatch")

    if build_definition is None or build_definition.get("buildType") != _BUILD_TYPE:
        reasons.append("attestation-build-type-mismatch")

    external_parameters = (
        _as_mapping(build_definition.get("externalParameters"))
        if build_definition is not None
        else None
    )
    expected_external_parameters = {
        "releaseId": record.release_id,
        "sourceSha": record.source_sha,
    }
    if external_parameters != expected_external_parameters:
        reasons.append("attestation-release-context-mismatch")
    metadata = (
        _as_mapping(run_details.get("metadata"))
        if run_details is not None
        else None
    )
    if metadata is None or metadata.get("invocationId") != record.release_id:
        reasons.append("attestation-invocation-mismatch")

    materials = (
        _as_sequence(build_definition.get("resolvedDependencies"))
        if build_definition is not None
        else None
    )
    if materials is None or len(materials) != 1:
        reasons.append("attestation-lock-digest-mismatch")
    else:
        material = _as_mapping(materials[0])
        digest = _as_mapping(material.get("digest")) if material is not None else None
        if (
            material is None
            or material.get("uri") != "dependency-lock"
            or digest is None
            or digest != {"sha256": record.dependency_lock_digest}
        ):
            reasons.append("attestation-lock-digest-mismatch")
    return reasons


def _verify_sbom_binding(
    record: ProvenanceRecord,
    *,
    expected_lock: Mapping[str, Any] | None,
) -> list[str]:
    """Cross-bind the CycloneDX metadata to the provenance subject and builder."""
    sbom = _as_mapping(record.sbom)
    if sbom is None or sbom.get("bomFormat") != "CycloneDX":
        return ["sbom-missing-or-wrong-format"]
    reasons: list[str] = []
    if sbom.get("specVersion") != "1.5":
        reasons.append("sbom-version-mismatch")
    components = _as_sequence(sbom.get("components"))
    if not components:
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
    if expected_lock is not None:
        expected_packages = _lock_packages(expected_lock)
        actual_packages_map: dict[str, str] = {}
        valid_components = components is not None
        if components is None:
            valid_components = False
        else:
            for value in components:
                component_value = _as_mapping(value)
                if component_value is None or component_value.get("type") != "library":
                    valid_components = False
                    break
                name = component_value.get("name")
                version = component_value.get("version")
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(version, str)
                    or not version
                    or name in actual_packages_map
                ):
                    valid_components = False
                    break
                actual_packages_map[name] = version
        actual_packages = actual_packages_map if valid_components else None
        if expected_packages is None or actual_packages != expected_packages:
            reasons.append("sbom-components-mismatch")
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
    release_id: str,
    source_sha: str,
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
        release_id: Stable identifier of the release that requested the build.
        source_sha: Full lowercase source commit SHA pinned by the release plan.
        predicate_type: SLSA / in-toto predicate type URI.
        signature_state: outcome of the external signing step.

    Raises:
        ValueError: if ``builder_identity`` is empty (spec §5.3 requires a
            non-null builder_identity).
    """
    _bounded_text(artifact_name, "artifact_name")
    if not builder_identity:
        raise ValueError(
            "builder_identity is required (spec §5.3 forbids anonymous artifacts)",
        )
    _bounded_text(builder_identity, "builder_identity")
    _bounded_text(release_id, "release_id")
    if _SOURCE_SHA_RE.fullmatch(source_sha) is None:
        raise ValueError("source_sha must be a full lowercase commit SHA")
    if predicate_type != _SLSA_PROVENANCE_TYPE:
        raise ValueError("predicate_type must be the policy-selected SLSA v1 URI")
    if not artifact_bytes:
        raise ValueError("artifact_bytes is required")
    if not dependency_lock_bytes:
        raise ValueError("dependency_lock_bytes is required (GRC-SEC-005: deps SHALL be locked)")

    artifact_digest = _sha256_hex(artifact_bytes)
    lock_digest = _sha256_hex(dependency_lock_bytes)
    parsed_lock = _validated_dependency_lock(dependency_lock_bytes, dependency_lock)

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
        release_id=release_id,
        source_sha=source_sha,
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
        release_id=release_id,
        source_sha=source_sha,
    )


def verify_provenance(
    record: ProvenanceRecord,
    *,
    expected_artifact_bytes: bytes | None = None,
    expected_lock_bytes: bytes | None = None,
    expected_subject: str | None = None,
    expected_builder_identity: str | None = None,
    expected_release_id: str | None = None,
    expected_source_sha: str | None = None,
    expected_signature_state: SignatureState = SignatureState.VERIFIED,
    verified_attestation_digest: str | None = None,
    expected_predicate_type: str = _SLSA_PROVENANCE_TYPE,
    authorization_id: str | None = None,
    authorization_source_sha: str | None = None,
    receipt_purpose: ReceiptPurpose | None = None,
    deployment_target: str | None = None,
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
        expected_subject: Artifact name independently selected by the plan.
        expected_builder_identity: Builder identity allowed by local policy.
        expected_release_id: Signed provenance release identifier.
        expected_source_sha: Signed provenance source commit.
        expected_signature_state: Required outcome from the external signer.
        verified_attestation_digest: Canonical payload SHA-256 returned by the
            external signature verifier. A verified release fails closed when
            this value is absent; it must not be copied from ``record``.
        expected_predicate_type: Exact signed predicate type allowed by policy.
        authorization_id: Current release operation receiving the receipt.
        authorization_source_sha: Current release plan's pinned source commit.
        receipt_purpose: Exact deploy or rollback operation being authorized.
        deployment_target: Environment or service receiving the artifact.
    """
    reasons: list[str] = []
    release_authorization = expected_signature_state is SignatureState.VERIFIED

    if _SHA256_RE.fullmatch(record.artifact_digest) is None:
        reasons.append("artifact-digest-invalid")
    if _SHA256_RE.fullmatch(record.dependency_lock_digest) is None:
        reasons.append("dependency-lock-digest-invalid")
    if _SOURCE_SHA_RE.fullmatch(record.source_sha) is None:
        reasons.append("source-sha-invalid")

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
    expected_lock: Mapping[str, Any] | None = None
    if expected_lock_bytes is None:
        if release_authorization:
            reasons.append("dependency-lock-bytes-missing")
    else:
        try:
            decoded_lock = json.loads(expected_lock_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            reasons.append("dependency-lock-invalid-json")
        else:
            if not isinstance(decoded_lock, Mapping) or _lock_packages(decoded_lock) is None:
                reasons.append("dependency-lock-invalid-shape")
            else:
                expected_lock = decoded_lock
        actual_lock_digest = _sha256_hex(expected_lock_bytes)
        if not hmac.compare_digest(actual_lock_digest, record.dependency_lock_digest):
            reasons.append(
                f"dependency-lock-digest-mismatch-expected-{actual_lock_digest[:12]}"
            )

    reasons.extend(_verify_sbom_binding(record, expected_lock=expected_lock))
    reasons.extend(
        _verify_attestation_binding(
            record,
            expected_predicate_type=expected_predicate_type,
        )
    )

    # 3. Artifact bytes are an independent verifier input, never optional for
    # a deploy/rollback authorization.
    if expected_artifact_bytes is None:
        if release_authorization:
            reasons.append("artifact-bytes-missing")
    else:
        actual_artifact_digest = _sha256_hex(expected_artifact_bytes)
        if not hmac.compare_digest(actual_artifact_digest, record.artifact_digest):
            reasons.append(
                f"artifact-digest-mismatch-expected-{actual_artifact_digest[:12]}"
            )

    # 4. Policy expectations are independent of the signed record. Without
    # them, a self-consistent statement can be replayed or swapped.
    if release_authorization:
        if expected_subject is None:
            reasons.append("subject-expectation-missing")
        elif record.subject != expected_subject:
            reasons.append("subject-mismatch")
        if expected_builder_identity is None:
            reasons.append("builder-expectation-missing")
        elif record.builder_identity != expected_builder_identity:
            reasons.append("builder-identity-mismatch")
        if expected_release_id is None:
            reasons.append("release-id-expectation-missing")
        elif record.release_id != expected_release_id:
            reasons.append("release-id-mismatch")
        if expected_source_sha is None:
            reasons.append("source-sha-expectation-missing")
        elif record.source_sha != expected_source_sha:
            reasons.append("source-sha-mismatch")
        if authorization_id is None:
            reasons.append("authorization-id-missing")
        if authorization_source_sha is None:
            reasons.append("authorization-source-sha-missing")
        elif _SOURCE_SHA_RE.fullmatch(authorization_source_sha) is None:
            reasons.append("authorization-source-sha-invalid")
        if not isinstance(receipt_purpose, ReceiptPurpose):
            reasons.append("receipt-purpose-missing-or-invalid")
        if deployment_target is None:
            reasons.append("deployment-target-missing")

    try:
        sbom_digest = _sha256_hex(_canonical_json_bytes(record.sbom))
    except (TypeError, ValueError):
        sbom_digest = ""
        reasons.append("sbom-not-canonical-json")

    reasons = list(dict.fromkeys(reasons))
    receipt: ArtifactVerificationReceipt | None = None
    if (
        not reasons
        and release_authorization
        and verified_attestation_digest is not None
        and authorization_id is not None
        and authorization_source_sha is not None
        and isinstance(receipt_purpose, ReceiptPurpose)
        and deployment_target is not None
        and record.attestation is not None
    ):
        partial = ArtifactVerificationReceipt(
            purpose=receipt_purpose,
            authorization_id=authorization_id,
            authorization_source_sha=authorization_source_sha,
            deployment_target=deployment_target,
            provenance_release_id=record.release_id,
            provenance_source_sha=record.source_sha,
            subject=record.subject,
            artifact_digest=record.artifact_digest,
            dependency_lock_digest=record.dependency_lock_digest,
            predicate_type=expected_predicate_type,
            builder_identity=record.builder_identity,
            attestation_digest=record.attestation.digest,
            signer_payload_digest=verified_attestation_digest,
            sbom_digest=sbom_digest,
            receipt_digest="",
        )
        receipt = ArtifactVerificationReceipt(
            **{
                **partial.__dict__,
                "receipt_digest": _receipt_digest(partial),
            }
        )

    return VerificationResult(ok=not reasons, reasons=reasons, receipt=receipt)


def verify_artifact_receipt(
    receipt: ArtifactVerificationReceipt | None,
    *,
    expected_purpose: ReceiptPurpose,
    expected_authorization_id: str,
    expected_authorization_source_sha: str,
    expected_deployment_target: str,
    expected_artifact_digest: str,
    expected_predicate_type: str = _SLSA_PROVENANCE_TYPE,
) -> VerificationResult:
    """Validate an exact deploy/rollback authorization receipt fail closed."""
    if receipt is None:
        return VerificationResult(ok=False, reasons=["artifact-receipt-missing"])

    reasons: list[str] = []
    text_fields = (
        receipt.authorization_id,
        receipt.authorization_source_sha,
        receipt.deployment_target,
        receipt.provenance_release_id,
        receipt.provenance_source_sha,
        receipt.subject,
        receipt.predicate_type,
        receipt.builder_identity,
    )
    receipt_shape_valid = isinstance(receipt.purpose, ReceiptPurpose) and all(
        isinstance(value, str) for value in text_fields
    )
    if not receipt_shape_valid:
        reasons.append("receipt-shape-invalid")
    if receipt.purpose is not expected_purpose:
        reasons.append("receipt-purpose-mismatch")
    if receipt.authorization_id != expected_authorization_id:
        reasons.append("receipt-authorization-id-mismatch")
    if receipt.authorization_source_sha != expected_authorization_source_sha:
        reasons.append("receipt-authorization-source-sha-mismatch")
    if receipt.deployment_target != expected_deployment_target:
        reasons.append("receipt-deployment-target-mismatch")
    if receipt.artifact_digest != expected_artifact_digest:
        reasons.append("receipt-artifact-digest-mismatch")
    if receipt.predicate_type != expected_predicate_type:
        reasons.append("receipt-predicate-type-mismatch")
    if (
        not isinstance(receipt.signer_payload_digest, str)
        or not isinstance(receipt.attestation_digest, str)
        or not hmac.compare_digest(
            receipt.signer_payload_digest,
            receipt.attestation_digest,
        )
    ):
        reasons.append("receipt-signer-payload-mismatch")
    for field_name, digest in (
        ("artifact", receipt.artifact_digest),
        ("dependency-lock", receipt.dependency_lock_digest),
        ("attestation", receipt.attestation_digest),
        ("signer-payload", receipt.signer_payload_digest),
        ("sbom", receipt.sbom_digest),
        ("receipt", receipt.receipt_digest),
    ):
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            reasons.append(f"receipt-{field_name}-digest-invalid")
    if (
        not isinstance(receipt.authorization_source_sha, str)
        or _SOURCE_SHA_RE.fullmatch(receipt.authorization_source_sha) is None
    ):
        reasons.append("receipt-authorization-source-sha-invalid")
    if (
        not isinstance(receipt.provenance_source_sha, str)
        or _SOURCE_SHA_RE.fullmatch(receipt.provenance_source_sha) is None
    ):
        reasons.append("receipt-provenance-source-sha-invalid")
    try:
        computed_receipt_digest = (
            _receipt_digest(receipt) if receipt_shape_valid else ""
        )
    except (AttributeError, TypeError, ValueError):
        computed_receipt_digest = ""
        reasons.append("receipt-shape-invalid")
    if (
        not isinstance(receipt.receipt_digest, str)
        or not hmac.compare_digest(computed_receipt_digest, receipt.receipt_digest)
    ):
        reasons.append("receipt-digest-mismatch")

    reasons = list(dict.fromkeys(reasons))
    return VerificationResult(ok=not reasons, reasons=reasons)
