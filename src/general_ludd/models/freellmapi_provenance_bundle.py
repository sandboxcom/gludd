"""Validate the complete FreeLLMAPI bridge artifact provenance bundle.

A provenance bundle is the immutable set of bytes that travels together for every
admitted bridge artifact: the compiled IIFE, its manifest, source map, license
notices, SBOM, upstream build evidence, and a keyless CI provenance attestation.
This module validates the bundle without exposing upstream content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, cast

FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION = 1

_MAX_BUNDLE_BYTES = 64 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_SOURCE_MAP_BYTES = 2 * 1024 * 1024
_MAX_LICENSE_BYTES = 1024 * 1024
_MAX_SBOM_BYTES = 1024 * 1024
_MAX_BUILD_EVIDENCE_BYTES = 256 * 1024
_MAX_PROVENANCE_BYTES = 64 * 1024

_REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "abi_version",
        "bundle_sha256",
        "upstream_commit",
        "source",
        "upstream",
        "license",
        "exports",
    }
)


class FreeLLMAPIProvenanceBundleFault(StrEnum):
    """Stable failure categories that never expose upstream content."""

    SCHEMA = "schema"
    BUNDLE_SIZE = "bundle_size"
    BUNDLE_DIGEST = "bundle_digest"
    MANIFEST_SIZE = "manifest_size"
    MANIFEST_SCHEMA = "manifest_schema"
    MANIFEST_DIGEST = "manifest_digest"
    SOURCE_MAP_SIZE = "source_map_size"
    LICENSE_SIZE = "license_size"
    SBOM_SIZE = "sbom_size"
    BUILD_EVIDENCE_SIZE = "build_evidence_size"
    PROVENANCE_SIZE = "provenance_size"


class FreeLLMAPIProvenanceBundleError(ValueError):
    """Fail-closed bundle error containing only its stable category."""

    def __init__(self, fault: FreeLLMAPIProvenanceBundleFault) -> None:
        """Create an error containing only its stable category."""
        self.fault = fault
        super().__init__(fault.value)


@dataclass(frozen=True, slots=True)
class FreeLLMAPIProvenanceBundle:
    """Content-free validated identity of one complete artifact bundle."""

    schema_version: int
    abi_version: int
    bundle_sha256: str
    manifest_sha256: str
    source_map_sha256: str
    license_bundle_sha256: str
    sbom_sha256: str
    build_evidence_sha256: str
    provenance_attestation_sha256: str
    upstream_commit: str
    runtime_admitted: bool
    evidence_id: str

    def __str__(self) -> str:
        """Return a content-free string representation of the bundle.

        Only the evidence identifier, ABI version, and bundle digest are
        exposed so that logs and traces remain free of upstream content.
        """
        return (
            f"FreeLLMAPIProvenanceBundle("
            f"evidence_id={self.evidence_id}, "
            f"abi_version={self.abi_version}, "
            f"bundle_sha256={self.bundle_sha256}"
            f")"
        )


def _fail(fault: FreeLLMAPIProvenanceBundleFault) -> NoReturn:
    raise FreeLLMAPIProvenanceBundleError(fault)


def _component(
    bundle: dict[str, object],
    key: str,
    max_bytes: int,
    fault: FreeLLMAPIProvenanceBundleFault,
) -> bytes:
    value = bundle.get(key)
    if type(value) is not bytes:
        _fail(FreeLLMAPIProvenanceBundleFault.SCHEMA)
    if not value or len(value) > max_bytes:
        _fail(fault)
    return value


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail(FreeLLMAPIProvenanceBundleFault.SCHEMA)


def _validate_manifest(manifest_bytes: bytes, bundle_digest: str) -> dict[str, object]:
    if not manifest_bytes or len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SIZE)
    try:
        parsed: object = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    if not isinstance(parsed, dict):
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    if set(parsed) != _REQUIRED_MANIFEST_KEYS:
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    if parsed.get("schema_version") != FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION:
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    abi_version = parsed.get("abi_version")
    if isinstance(abi_version, bool) or not isinstance(abi_version, int) or abi_version < 1:
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    bundle_sha256 = parsed.get("bundle_sha256")
    if not isinstance(bundle_sha256, str) or bundle_sha256 != bundle_digest:
        _fail(FreeLLMAPIProvenanceBundleFault.BUNDLE_DIGEST)
    upstream_commit = parsed.get("upstream_commit")
    if not isinstance(upstream_commit, str) or len(upstream_commit) != 40:
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    for text_key in ("source", "upstream", "license"):
        value = parsed.get(text_key)
        if not isinstance(value, str) or not value:
            _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    exports = parsed.get("exports")
    if not isinstance(exports, list) or not exports or any(not isinstance(item, str) or not item for item in exports):
        _fail(FreeLLMAPIProvenanceBundleFault.MANIFEST_SCHEMA)
    return cast("dict[str, object]", parsed)


def validate_provenance_bundle(bundle: object) -> FreeLLMAPIProvenanceBundle:
    """Validate a complete artifact bundle and return its content-free identity.

    Args:
        bundle: Mapping from component name to bounded bytes. Required keys:
            ``bundle``, ``manifest``, ``source_map``, ``license_bundle``,
            ``sbom``, ``build_evidence``, ``provenance_attestation``.

    Returns:
        A frozen, content-free record of the validated bundle identity.

    Raises:
        FreeLLMAPIProvenanceBundleError: If any component is missing, oversized,
            malformed, or digest-inconsistent.
    """
    if not isinstance(bundle, dict):
        _fail(FreeLLMAPIProvenanceBundleFault.SCHEMA)
    expected_keys = {
        "bundle",
        "manifest",
        "source_map",
        "license_bundle",
        "sbom",
        "build_evidence",
        "provenance_attestation",
    }
    if set(bundle) != expected_keys:
        _fail(FreeLLMAPIProvenanceBundleFault.SCHEMA)

    bundle_bytes = _component(bundle, "bundle", _MAX_BUNDLE_BYTES, FreeLLMAPIProvenanceBundleFault.BUNDLE_SIZE)
    manifest_bytes = _component(bundle, "manifest", _MAX_MANIFEST_BYTES, FreeLLMAPIProvenanceBundleFault.MANIFEST_SIZE)
    source_map_bytes = _component(
        bundle, "source_map", _MAX_SOURCE_MAP_BYTES, FreeLLMAPIProvenanceBundleFault.SOURCE_MAP_SIZE
    )
    license_bytes = _component(
        bundle, "license_bundle", _MAX_LICENSE_BYTES, FreeLLMAPIProvenanceBundleFault.LICENSE_SIZE
    )
    sbom_bytes = _component(bundle, "sbom", _MAX_SBOM_BYTES, FreeLLMAPIProvenanceBundleFault.SBOM_SIZE)
    build_evidence_bytes = _component(
        bundle,
        "build_evidence",
        _MAX_BUILD_EVIDENCE_BYTES,
        FreeLLMAPIProvenanceBundleFault.BUILD_EVIDENCE_SIZE,
    )
    provenance_bytes = _component(
        bundle,
        "provenance_attestation",
        _MAX_PROVENANCE_BYTES,
        FreeLLMAPIProvenanceBundleFault.PROVENANCE_SIZE,
    )

    bundle_digest = _digest(bundle_bytes)
    manifest_digest = _digest(manifest_bytes)
    manifest = _validate_manifest(manifest_bytes, bundle_digest)

    record: dict[str, object] = {
        "schema_version": FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION,
        "abi_version": manifest["abi_version"],
        "bundle_sha256": bundle_digest,
        "manifest_sha256": manifest_digest,
        "source_map_sha256": _digest(source_map_bytes),
        "license_bundle_sha256": _digest(license_bytes),
        "sbom_sha256": _digest(sbom_bytes),
        "build_evidence_sha256": _digest(build_evidence_bytes),
        "provenance_attestation_sha256": _digest(provenance_bytes),
        "upstream_commit": manifest["upstream_commit"],
        "runtime_admitted": False,
    }
    record["evidence_id"] = "sha256:" + _digest(_canonical_bytes(record))

    return FreeLLMAPIProvenanceBundle(
        schema_version=cast(int, record["schema_version"]),
        abi_version=cast(int, record["abi_version"]),
        bundle_sha256=cast(str, record["bundle_sha256"]),
        manifest_sha256=cast(str, record["manifest_sha256"]),
        source_map_sha256=cast(str, record["source_map_sha256"]),
        license_bundle_sha256=cast(str, record["license_bundle_sha256"]),
        sbom_sha256=cast(str, record["sbom_sha256"]),
        build_evidence_sha256=cast(str, record["build_evidence_sha256"]),
        provenance_attestation_sha256=cast(str, record["provenance_attestation_sha256"]),
        upstream_commit=cast(str, record["upstream_commit"]),
        runtime_admitted=cast(bool, record["runtime_admitted"]),
        evidence_id=cast(str, record["evidence_id"]),
    )


__all__ = [
    "FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION",
    "FreeLLMAPIProvenanceBundle",
    "FreeLLMAPIProvenanceBundleError",
    "FreeLLMAPIProvenanceBundleFault",
    "validate_provenance_bundle",
]
