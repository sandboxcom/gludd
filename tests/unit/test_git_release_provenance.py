"""Unit tests for provenance + source registry (spec GRC-001 §6 GRC-SEC-005, §10, GRC-P4).

Covers:

- SBOM (CycloneDX) generated from a dependency lock
- Artifact checksums computed (sha256)
- Signature verification: accept valid, reject mismatched/unsigned
- Builder identity recorded and required
- Dependency-lock digest mismatch detected
- Signing key NEVER accepted as a parameter or stored on the record
- Provenance chain completeness (sbom + attestation + subject + materials)
- Source registry seeded with spec §10 categories
- Source freshness: expired entries flagged, fresh entries pass
- Source entry records URL, authority, retrieval time, content digest, license,
  review expiry
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.git_release.provenance import (
    ProvenanceRecord,
    SignatureState,
    VerificationResult,
    build_provenance,
    verify_provenance,
)
from general_ludd.git_release.source_registry import (
    SourceAuthority,
    SourceEntry,
    SourceRegistry,
    default_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ARTIFACT = b"#!/bin/sh\necho hello\n"
_LOCK = json.dumps(
    {
        "generated_at": "2026-01-01T00:00:00Z",
        "packages": {"requests": "2.31.0", "urllib3": "2.0.4"},
    }
).encode("utf-8")
_LOCK_PARSED = json.loads(_LOCK.decode("utf-8"))
_BUILDER = "github-actions:runner-01"


@pytest.fixture()
def lock_bytes() -> bytes:
    return _LOCK


@pytest.fixture()
def artifact_bytes() -> bytes:
    return _ARTIFACT


@pytest.fixture()
def provenance(lock_bytes: bytes, artifact_bytes: bytes) -> ProvenanceRecord:
    return build_provenance(
        artifact_name="gludd-1.0.0.tar.gz",
        artifact_bytes=artifact_bytes,
        dependency_lock_bytes=lock_bytes,
        dependency_lock=_LOCK_PARSED,
        builder_identity=_BUILDER,
        signature_state=SignatureState.VERIFIED,
    )


def _now_rfc3339() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_from_now(days: int) -> str:
    return (datetime.now(tz=UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1. SBOM generated from dependency lock (GRC-SEC-005)
# ---------------------------------------------------------------------------


def test_build_provenance_generates_cyclonedx_sbom_from_lockfile(
    lock_bytes: bytes,
    artifact_bytes: bytes,
) -> None:
    record = build_provenance(
        artifact_name="app-1.0.0.tar.gz",
        artifact_bytes=artifact_bytes,
        dependency_lock_bytes=lock_bytes,
        dependency_lock=_LOCK_PARSED,
        builder_identity=_BUILDER,
    )
    assert record.sbom["bomFormat"] == "CycloneDX"
    assert record.sbom["specVersion"] == "1.5"
    component_names = {c["name"] for c in record.sbom["components"]}
    assert {"requests", "urllib3"} <= component_names, component_names
    # Each component carries its locked version (dependencies SHALL be locked).
    versions = {c["name"]: c["version"] for c in record.sbom["components"]}
    assert versions["requests"] == "2.31.0"
    assert versions["urllib3"] == "2.0.4"


# ---------------------------------------------------------------------------
# 2. Artifact checksum computed (GRC-SEC-005)
# ---------------------------------------------------------------------------


def test_build_provenance_computes_artifact_checksum(
    lock_bytes: bytes,
    artifact_bytes: bytes,
) -> None:
    record = build_provenance(
        artifact_name="app",
        artifact_bytes=artifact_bytes,
        dependency_lock_bytes=lock_bytes,
        dependency_lock=_LOCK_PARSED,
        builder_identity=_BUILDER,
    )
    expected = hashlib.sha256(artifact_bytes).hexdigest()
    assert record.artifact_digest == expected
    # Subject in the in-toto statement carries the same digest.
    assert record.attestation is not None
    subject = record.attestation.statement["subject"][0]
    assert subject["digest"]["sha256"] == expected


# ---------------------------------------------------------------------------
# 3. Signature verification — pass (GRC-SEC-005)
# ---------------------------------------------------------------------------


def test_verify_provenance_accepts_valid_record(
    provenance: ProvenanceRecord,
    lock_bytes: bytes,
    artifact_bytes: bytes,
) -> None:
    result = verify_provenance(
        provenance,
        expected_lock_bytes=lock_bytes,
        expected_artifact_bytes=artifact_bytes,
    )
    assert isinstance(result, VerificationResult)
    assert result.ok is True, result.reasons
    assert result.reasons == []


# ---------------------------------------------------------------------------
# 4. Signature verification — fail (GRC-SEC-005)
# ---------------------------------------------------------------------------


def test_verify_provenance_rejects_unsigned_when_verified_required(
    lock_bytes: bytes,
    artifact_bytes: bytes,
) -> None:
    record = build_provenance(
        artifact_name="app",
        artifact_bytes=artifact_bytes,
        dependency_lock_bytes=lock_bytes,
        dependency_lock=_LOCK_PARSED,
        builder_identity=_BUILDER,
        signature_state=SignatureState.UNSIGNED,
    )
    result = verify_provenance(
        record,
        expected_lock_bytes=lock_bytes,
        expected_artifact_bytes=artifact_bytes,
    )
    assert result.ok is False
    assert any("signature" in r for r in result.reasons), result.reasons


# ---------------------------------------------------------------------------
# 5. Builder identity recorded and required (GRC-SEC-005)
# ---------------------------------------------------------------------------


def test_build_provenance_records_builder_identity(
    lock_bytes: bytes,
    artifact_bytes: bytes,
) -> None:
    record = build_provenance(
        artifact_name="app",
        artifact_bytes=artifact_bytes,
        dependency_lock_bytes=lock_bytes,
        dependency_lock=_LOCK_PARSED,
        builder_identity="tekton:pipeline-abc",
    )
    assert record.builder_identity == "tekton:pipeline-abc"
    assert record.attestation is not None
    assert record.attestation.statement["predicate"]["builder"]["id"] == "tekton:pipeline-abc"


def test_build_provenance_rejects_empty_builder_identity(
    lock_bytes: bytes,
    artifact_bytes: bytes,
) -> None:
    with pytest.raises(ValueError):
        build_provenance(
            artifact_name="app",
            artifact_bytes=artifact_bytes,
            dependency_lock_bytes=lock_bytes,
            dependency_lock=_LOCK_PARSED,
            builder_identity="",
        )


# ---------------------------------------------------------------------------
# 6. Dependency digest mismatch detected (GRC-SEC-005 "digest verified")
# ---------------------------------------------------------------------------


def test_verify_provenance_detects_dependency_digest_mismatch(
    provenance: ProvenanceRecord,
    artifact_bytes: bytes,
) -> None:
    tampered_lock = json.dumps(
        {
            "generated_at": "2026-01-01T00:00:00Z",
            "packages": {"requests": "2.32.0"},  # version drift
        }
    ).encode("utf-8")
    result = verify_provenance(
        provenance,
        expected_lock_bytes=tampered_lock,
        expected_artifact_bytes=artifact_bytes,
    )
    assert result.ok is False
    assert any("dependency" in r or "lock" in r for r in result.reasons), result.reasons


def test_verify_provenance_detects_artifact_digest_mismatch(
    provenance: ProvenanceRecord,
    lock_bytes: bytes,
) -> None:
    result = verify_provenance(
        provenance,
        expected_lock_bytes=lock_bytes,
        expected_artifact_bytes=b"different bytes",
    )
    assert result.ok is False
    assert any("artifact" in r for r in result.reasons), result.reasons


# ---------------------------------------------------------------------------
# 7. Signing key never accepted as a parameter (GRC-SEC-005)
# ---------------------------------------------------------------------------


def test_build_provenance_signature_has_no_signing_key_parameter() -> None:
    """A signing key MUST NOT be a build_provenance parameter (spec GRC-SEC-005).

    Signing keys SHALL remain in an external signer; build_provenance only
    records the resulting signature_state.
    """
    sig = inspect.signature(build_provenance)
    forbidden = {"signing_key", "secret_key", "private_key", "gpg_key", "key"}
    params = set(sig.parameters)
    assert not (params & forbidden), f"build_provenance accepts a forbidden key parameter: {params & forbidden}"


def test_provenance_record_has_no_secret_field(provenance: ProvenanceRecord) -> None:
    """No field on the record may carry key material."""
    record_dict = {
        **{f: getattr(provenance, f) for f in provenance.__dataclass_fields__},
    }
    serialized = json.dumps(record_dict, default=str)
    lower = serialized.lower()
    for needle in ("private_key", "signing_key", "secret_key", "BEGIN PRIVATE KEY"):
        assert needle.lower() not in lower, f"secret material leaked into record: {needle}"


# ---------------------------------------------------------------------------
# 8. Provenance chain completeness (GRC-SEC-005)
# ---------------------------------------------------------------------------


def test_verify_provenance_requires_complete_chain(
    lock_bytes: bytes,
    artifact_bytes: bytes,
) -> None:
    """A provenance record missing sbom / attestation / subject fails verification."""
    good = build_provenance(
        artifact_name="app",
        artifact_bytes=artifact_bytes,
        dependency_lock_bytes=lock_bytes,
        dependency_lock=_LOCK_PARSED,
        builder_identity=_BUILDER,
        signature_state=SignatureState.VERIFIED,
    )

    # Strip the SBOM components — verification must fail.
    broken_sbom = {**good.sbom, "components": []}
    broken = ProvenanceRecord(
        sbom=broken_sbom,
        signature_state=good.signature_state,
        attestation=good.attestation,
        builder_identity=good.builder_identity,
        dependency_lock_digest=good.dependency_lock_digest,
        artifact_digest=good.artifact_digest,
        subject=good.subject,
    )
    result = verify_provenance(
        broken,
        expected_lock_bytes=lock_bytes,
        expected_artifact_bytes=artifact_bytes,
    )
    assert result.ok is False
    assert any("sbom" in r for r in result.reasons), result.reasons

    # Missing attestation is also a chain break.
    no_attest = ProvenanceRecord(
        sbom=good.sbom,
        signature_state=good.signature_state,
        attestation=None,
        builder_identity=good.builder_identity,
        dependency_lock_digest=good.dependency_lock_digest,
        artifact_digest=good.artifact_digest,
        subject=good.subject,
    )
    result2 = verify_provenance(
        no_attest,
        expected_lock_bytes=lock_bytes,
        expected_artifact_bytes=artifact_bytes,
    )
    assert result2.ok is False
    assert any("attestation" in r for r in result2.reasons), result2.reasons


def test_provenance_record_is_json_serializable(provenance: ProvenanceRecord) -> None:
    """Records SHALL be versioned JSON-serializable (spec §5)."""
    blob = json.dumps(provenance, default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o))
    parsed = json.loads(blob)
    assert parsed["builder_identity"] == _BUILDER
    assert parsed["signature_state"] == "verified"


# ---------------------------------------------------------------------------
# 9. Source registry: spec §10 categories present
# ---------------------------------------------------------------------------


def test_default_registry_contains_required_categories() -> None:
    """Spec §10: official Git docs, hosting-provider docs, build specs,
    supply-chain standards, and practitioner reports."""
    reg = default_registry()
    authorities = {e.authority_class for e in reg.entries()}
    assert SourceAuthority.OFFICIAL_DOC in authorities
    assert SourceAuthority.HOSTING_PROVIDER in authorities
    assert SourceAuthority.BUILD_SPEC in authorities
    assert SourceAuthority.SUPPLY_CHAIN_STANDARD in authorities
    assert SourceAuthority.PRACTITIONER_REPORT in authorities


def test_source_entry_records_all_spec_fields() -> None:
    entry = SourceEntry(
        id="git-docs-glossary",
        url="https://git-scm.com/docs/git-glossary",
        authority_class=SourceAuthority.OFFICIAL_DOC,
        retrieval_time=_now_rfc3339(),
        content_digest="sha256:" + "a" * 64,
        license="CC-BY-3.0",
        review_expiry=_days_from_now(180),
        title="Git Glossary",
        affects=("GRC-001",),
    )
    assert entry.url.startswith("https://")
    assert entry.authority_class == SourceAuthority.OFFICIAL_DOC
    assert entry.content_digest.startswith("sha256:")
    assert entry.license
    assert entry.review_expiry


# ---------------------------------------------------------------------------
# 10. Source freshness — expired flagged, fresh passes (spec §10)
# ---------------------------------------------------------------------------


def test_check_freshness_flags_expired_entry() -> None:
    expired = SourceEntry(
        id="stale-forum-report",
        url="https://example.com/forum/1",
        authority_class=SourceAuthority.PRACTITIONER_REPORT,
        retrieval_time=_days_from_now(-200),
        content_digest="sha256:" + "b" * 64,
        license="unknown",
        review_expiry=_days_from_now(-30),  # expired 30 days ago
    )
    reg = SourceRegistry(entries=[expired])
    flags = reg.check_freshness(now=_now_rfc3339())
    assert len(flags) == 1
    assert flags[0].entry_id == "stale-forum-report"
    assert "expired" in flags[0].reason


def test_check_freshness_passes_unexpired_entry() -> None:
    fresh = SourceEntry(
        id="slsa-v1",
        url="https://slsa.dev/spec/v1.0/provenance",
        authority_class=SourceAuthority.SUPPLY_CHAIN_STANDARD,
        retrieval_time=_now_rfc3339(),
        content_digest="sha256:" + "c" * 64,
        license="Apache-2.0",
        review_expiry=_days_from_now(365),
    )
    reg = SourceRegistry(entries=[fresh])
    flags = reg.check_freshness(now=_now_rfc3339())
    assert flags == [], flags


def test_check_freshness_flags_missing_digest_entry() -> None:
    bad = SourceEntry(
        id="no-digest",
        url="https://example.com/x",
        authority_class=SourceAuthority.BUILD_SPEC,
        retrieval_time=_now_rfc3339(),
        content_digest="",  # missing — verification cannot be proven
        license="unknown",
        review_expiry=_days_from_now(90),
    )
    reg = SourceRegistry(entries=[bad])
    flags = reg.check_freshness(now=_now_rfc3339())
    assert any("digest" in f.reason for f in flags), flags
