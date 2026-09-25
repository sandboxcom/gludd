"""Runtime compatibility gate for FreeLLMAPI provenance bundles.

The gate is intentionally small: it decides whether a validated bundle's ABI
version is supported by the current runtime.  It never exposes upstream content.
"""

from __future__ import annotations

import hashlib
import json

from general_ludd.models.freellmapi_compatibility_gate import (
    FREELLMAPI_SUPPORTED_ABI_VERSIONS,
    FreeLLMAPICompatibilityGateResult,
    FreeLLMAPICompatibilityGateStatus,
    check_bundle_compatibility,
)
from general_ludd.models.freellmapi_provenance_bundle import (
    FreeLLMAPIProvenanceBundle,
    validate_provenance_bundle,
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _bundle_for_abi(abi_version: int) -> FreeLLMAPIProvenanceBundle:
    bundle = b"console.log('bundle')"
    manifest = {
        "schema_version": 1,
        "abi_version": abi_version,
        "bundle_sha256": _digest(bundle),
        "upstream_commit": "780a7d8d6dcbc818eb10ec17da210635b569ae22",
        "source": "server/src/services/scoring.ts",
        "upstream": "https://github.com/tashfeenahmed/freellmapi",
        "license": "MIT",
        "exports": ["expectedReliability"],
    }
    source_map = b'{"version":3}'
    license_bundle = b"MIT License\nCopyright (c) 2026"
    sbom = _canonical_bytes({"components": []})
    build_evidence = _canonical_bytes({"gate": "freellmapi-upstream-build-v1", "decision": "upstream_build_verified"})
    provenance = _canonical_bytes({"attestation": "keyless-ci"})
    raw = {
        "bundle": bundle,
        "manifest": _canonical_bytes(manifest),
        "source_map": source_map,
        "license_bundle": license_bundle,
        "sbom": sbom,
        "build_evidence": build_evidence,
        "provenance_attestation": provenance,
    }
    return validate_provenance_bundle(raw)


def test_api_surface_is_minimal_and_public() -> None:
    import general_ludd.models.freellmapi_compatibility_gate as module

    assert sorted(module.__all__) == sorted(
        [
            "FREELLMAPI_SUPPORTED_ABI_VERSIONS",
            "FreeLLMAPICompatibilityGateResult",
            "FreeLLMAPICompatibilityGateStatus",
            "check_bundle_compatibility",
        ]
    )


def test_supported_abi_version_is_nonempty() -> None:
    assert isinstance(FREELLMAPI_SUPPORTED_ABI_VERSIONS, frozenset)
    assert len(FREELLMAPI_SUPPORTED_ABI_VERSIONS) >= 1
    assert all(isinstance(v, int) and v > 0 for v in FREELLMAPI_SUPPORTED_ABI_VERSIONS)


def test_current_abi_is_compatible() -> None:
    current = min(FREELLMAPI_SUPPORTED_ABI_VERSIONS)
    bundle = _bundle_for_abi(current)
    result = check_bundle_compatibility(bundle)

    assert isinstance(result, FreeLLMAPICompatibilityGateResult)
    assert result.status == FreeLLMAPICompatibilityGateStatus.COMPATIBLE
    assert result.compatible is True
    assert result.bundle_evidence_id == bundle.evidence_id
    assert result.reason is not None and "compatible" in result.reason.lower()


def test_unknown_abi_is_incompatible() -> None:
    max_supported = max(FREELLMAPI_SUPPORTED_ABI_VERSIONS)
    bundle = _bundle_for_abi(max_supported + 1000)
    result = check_bundle_compatibility(bundle)

    assert result.status == FreeLLMAPICompatibilityGateStatus.INCOMPATIBLE_ABI
    assert result.compatible is False
    assert result.bundle_evidence_id == bundle.evidence_id
    assert str(max_supported + 1000) in result.reason
    assert "compatible" in result.reason.lower() or "unsupported" in result.reason.lower()


def test_result_str_is_content_free() -> None:
    current = min(FREELLMAPI_SUPPORTED_ABI_VERSIONS)
    bundle = _bundle_for_abi(current)
    result = check_bundle_compatibility(bundle)
    text = str(result)

    assert "console.log" not in text
    assert "MIT License" not in text
    assert "keyless-ci" not in text
    assert result.bundle_evidence_id in text
    assert result.status.value in text


def test_incompatible_statuses_are_distinct() -> None:
    statuses = list(FreeLLMAPICompatibilityGateStatus)
    assert len(set(s.value for s in statuses)) == len(statuses)
    assert (
        FreeLLMAPICompatibilityGateStatus.COMPATIBLE.value != FreeLLMAPICompatibilityGateStatus.INCOMPATIBLE_ABI.value
    )
