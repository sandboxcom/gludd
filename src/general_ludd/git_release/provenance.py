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
import json
from collections.abc import Mapping
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
    canonical-JSON serialization of the statement, so a verifier can prove the
    attestation bytes match what was signed without re-serializing.
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
) -> VerificationResult:
    """Check signature, checksums, and SBOM completeness for ``record``.

    Returns a :class:`VerificationResult`; ``ok`` is True only when every
    precondition held. Reasons are lowercase + hyphenated so they can be
    matched by prefix (``"signature-*"``, ``"artifact-*"``, ``"sbom-*"``).
    """
    reasons: list[str] = []

    # 1. Signature state.
    if record.signature_state != expected_signature_state:
        reasons.append(f"signature-state-{record.signature_state.value}-expected-{expected_signature_state.value}")

    # 2. Provenance chain completeness (sbom + attestation + subject).
    sbom = record.sbom
    if not sbom or sbom.get("bomFormat") != "CycloneDX":
        reasons.append("sbom-missing-or-wrong-format")
    elif not sbom.get("components"):
        reasons.append("sbom-empty-components")
    if record.attestation is None:
        reasons.append("attestation-missing")
    else:
        stmt = record.attestation.statement
        if not stmt.get("subject"):
            reasons.append("attestation-subject-missing")
        if "predicate" not in stmt or not stmt["predicate"].get("builder", {}).get("id"):
            reasons.append("attestation-builder-missing")

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
