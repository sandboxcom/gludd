"""Provenance bundle validation for FreeLLMAPI bridge artifacts.

The bundle is the complete immutable artifact identity: compiled JavaScript,
manifest, source map, license notices, SBOM, upstream build evidence, and a
keyless provenance attestation.  This module proves the bundle can be validated
without exposing upstream content.
"""

from __future__ import annotations

import hashlib
import json
from typing import cast

import pytest

from general_ludd.models.freellmapi_provenance_bundle import (
    FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION,
    FreeLLMAPIProvenanceBundle,
    FreeLLMAPIProvenanceBundleError,
    FreeLLMAPIProvenanceBundleFault,
    validate_provenance_bundle,
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _minimal_bundle() -> dict[str, object]:
    bundle = b"console.log('bundle')"
    manifest = {
        "schema_version": FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION,
        "abi_version": 1,
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
    return {
        "bundle": bundle,
        "manifest": _canonical_bytes(manifest),
        "source_map": source_map,
        "license_bundle": license_bundle,
        "sbom": sbom,
        "build_evidence": build_evidence,
        "provenance_attestation": provenance,
    }


def test_api_surface_is_minimal_and_public() -> None:
    import general_ludd.models.freellmapi_provenance_bundle as module

    assert module.__all__ == [
        "FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION",
        "FreeLLMAPIProvenanceBundle",
        "FreeLLMAPIProvenanceBundleError",
        "FreeLLMAPIProvenanceBundleFault",
        "validate_provenance_bundle",
    ]


def test_minimal_bundle_passes_and_returns_content_free_record() -> None:
    raw = _minimal_bundle()
    result = validate_provenance_bundle(raw)

    assert isinstance(result, FreeLLMAPIProvenanceBundle)
    assert result.schema_version == FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION
    assert result.abi_version == 1
    assert result.bundle_sha256 == _digest(cast(bytes, raw["bundle"]))
    assert result.manifest_sha256 == _digest(cast(bytes, raw["manifest"]))
    assert result.runtime_admitted is False
    assert result.evidence_id.startswith("sha256:")


def test_bundle_size_fault_rejects_empty_and_oversized() -> None:
    raw = _minimal_bundle()
    raw["bundle"] = b""
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.BUNDLE_SIZE


def test_manifest_schema_fault_rejects_missing_required_key() -> None:
    raw = _minimal_bundle()
    manifest = json.loads(raw["manifest"])
    manifest.pop("upstream_commit")
    raw["manifest"] = _canonical_bytes(manifest)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA


def test_bundle_digest_mismatch_is_detected() -> None:
    raw = _minimal_bundle()
    manifest = json.loads(raw["manifest"])
    manifest["bundle_sha256"] = "0" * 64
    raw["manifest"] = _canonical_bytes(manifest)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.BUNDLE_DIGEST


def test_manifest_schema_fault_rejects_wrong_schema_version() -> None:
    raw = _minimal_bundle()
    manifest = json.loads(raw["manifest"])
    manifest["schema_version"] = 99
    raw["manifest"] = _canonical_bytes(manifest)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA


def test_source_map_size_fault_rejects_oversized_map() -> None:
    raw = _minimal_bundle()
    raw["source_map"] = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.SOURCE_MAP_SIZE


def test_license_bundle_size_fault_rejects_oversized_license() -> None:
    raw = _minimal_bundle()
    raw["license_bundle"] = b"x" * (1024 * 1024 + 1)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.LICENSE_SIZE


def test_sbom_size_fault_rejects_oversized_sbom() -> None:
    raw = _minimal_bundle()
    raw["sbom"] = b"x" * (1024 * 1024 + 1)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.SBOM_SIZE


def test_build_evidence_size_fault_rejects_oversized_evidence() -> None:
    raw = _minimal_bundle()
    raw["build_evidence"] = b"x" * (256 * 1024 + 1)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.BUILD_EVIDENCE_SIZE


def test_provenance_size_fault_rejects_oversized_attestation() -> None:
    raw = _minimal_bundle()
    raw["provenance_attestation"] = b"x" * (64 * 1024 + 1)
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.PROVENANCE_SIZE


def test_missing_component_is_a_schema_fault() -> None:
    raw = _minimal_bundle()
    raw.pop("sbom")
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.SCHEMA


def test_non_bytes_component_is_a_schema_fault() -> None:
    raw = _minimal_bundle()
    raw["bundle"] = "not-bytes"
    with pytest.raises(FreeLLMAPIProvenanceBundleError) as exc:
        validate_provenance_bundle(raw)
    assert exc.value.fault == FreeLLMAPIProvenanceBundleFault.SCHEMA


def test_result_str_is_content_free() -> None:
    raw = _minimal_bundle()
    result = validate_provenance_bundle(raw)
    text = str(result)
    assert "console.log" not in text
    assert "MIT License" not in text
    assert "keyless-ci" not in text
    assert result.evidence_id in text
