"""Build non-runnable, provenance-bound FreeLLMAPI update candidates.

This universal model-infrastructure boundary deliberately stops before runtime
promotion.  It verifies discovery evidence and records exact source identities;
the separate frozen-delta gate remains responsible for admitting an artifact.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import NoReturn

from general_ludd.models.freellmapi_scoring_kernel import (
    FREELLMAPI_SCORING_BUNDLE_DIGEST,
    FREELLMAPI_SCORING_UPSTREAM_COMMIT,
)

__all__ = [
    "FREELLMAPI_ADMISSION_SCHEMA_VERSION",
    "FREELLMAPI_FROZEN_DELTA_GATE",
    "FREELLMAPI_REPOSITORY",
    "FREELLMAPI_SCORING_SOURCE",
    "FreeLLMAPIAdmissionError",
    "FreeLLMAPIAdmissionFault",
    "build_candidate_lock",
    "validate_candidate_lock",
]

FREELLMAPI_ADMISSION_SCHEMA_VERSION = 1
FREELLMAPI_REPOSITORY = "tashfeenahmed/freellmapi"
FREELLMAPI_SCORING_SOURCE = "server/src/services/scoring.ts"
FREELLMAPI_FROZEN_DELTA_GATE = "freellmapi-frozen-delta-v1"

_EXPECTED_EXPORTS = (
    "reliabilityPosterior",
    "expectedReliability",
    "speedScore",
    "headroomFactor",
    "rateWindowHeadroomFactor",
    "rateLimitFactor",
)
_REQUIRED_SOURCE_PATHS = (
    "LICENSE",
    "package-lock.json",
    FREELLMAPI_SCORING_SOURCE,
)
_SOURCE_LIMITS = {
    "LICENSE": 256 * 1024,
    "package-lock.json": 4 * 1024 * 1024,
    FREELLMAPI_SCORING_SOURCE: 2 * 1024 * 1024,
}
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_SIGNATURE_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_BUNDLE_BYTES = 64 * 1024
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")


class FreeLLMAPIAdmissionFault(StrEnum):
    """Content-free failure categories for upstream candidate handling."""

    REPOSITORY_IDENTITY = "repository_identity"
    RELEASE_IDENTITY = "release_identity"
    RELEASE_NOT_STABLE = "release_not_stable"
    TAG_COMMIT_MISMATCH = "tag_commit_mismatch"
    COMMIT_IDENTITY = "commit_identity"
    COMMIT_UNVERIFIED = "commit_unverified"
    ARCHIVE_INVALID = "archive_invalid"
    ARCHIVE_LAYOUT = "archive_layout"
    ARCHIVE_LIMIT = "archive_limit"
    LICENSE_INCOMPATIBLE = "license_incompatible"
    PACKAGE_LOCK_INVALID = "package_lock_invalid"
    ADMITTED_ARTIFACT_INVALID = "admitted_artifact_invalid"
    CANDIDATE_LOCK_INVALID = "candidate_lock_invalid"


class FreeLLMAPIAdmissionError(ValueError):
    """Fail-closed candidate error that never contains upstream content."""

    def __init__(self, fault: FreeLLMAPIAdmissionFault) -> None:
        """Create an error containing only its stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


def _fail(fault: FreeLLMAPIAdmissionFault) -> NoReturn:
    raise FreeLLMAPIAdmissionError(fault)


def _mapping(value: object, fault: FreeLLMAPIAdmissionFault) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(fault)
    return value


def _nonempty_string(value: object, fault: FreeLLMAPIAdmissionFault) -> str:
    if not isinstance(value, str) or not value:
        _fail(fault)
    return value


def _positive_int(value: object, fault: FreeLLMAPIAdmissionFault) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(fault)
    return value


def _sha1(value: object, fault: FreeLLMAPIAdmissionFault) -> str:
    text = _nonempty_string(value, fault)
    if _SHA1_RE.fullmatch(text) is None:
        _fail(fault)
    return text


def _sha256(value: object, fault: FreeLLMAPIAdmissionFault) -> str:
    text = _nonempty_string(value, fault)
    if _SHA256_RE.fullmatch(text) is None:
        _fail(fault)
    return text


def _timestamp(value: object, fault: FreeLLMAPIAdmissionFault) -> str:
    text = _nonempty_string(value, fault)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail(fault)
    if parsed.tzinfo is None:
        _fail(fault)
    return text


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        _fail(FreeLLMAPIAdmissionFault.CANDIDATE_LOCK_INVALID)
    return encoded.encode("ascii")


def _repository_identity(metadata: Mapping[str, object]) -> int:
    fault = FreeLLMAPIAdmissionFault.REPOSITORY_IDENTITY
    if metadata.get("full_name") != FREELLMAPI_REPOSITORY:
        _fail(fault)
    if metadata.get("archived") is not False:
        _fail(fault)
    return _positive_int(metadata.get("id"), fault)


def _release_identity(metadata: Mapping[str, object], expected_tag: str) -> tuple[int, str]:
    identity_fault = FreeLLMAPIAdmissionFault.RELEASE_IDENTITY
    if _STABLE_TAG_RE.fullmatch(expected_tag) is None:
        _fail(identity_fault)
    if metadata.get("tag_name") != expected_tag:
        _fail(identity_fault)
    if metadata.get("draft") is not False or metadata.get("prerelease") is not False:
        _fail(FreeLLMAPIAdmissionFault.RELEASE_NOT_STABLE)
    release_id = _positive_int(metadata.get("id"), identity_fault)
    published_at = _timestamp(metadata.get("published_at"), identity_fault)
    return release_id, published_at


def _tag_identity(metadata: Mapping[str, object], expected_tag: str, expected_commit: str) -> None:
    fault = FreeLLMAPIAdmissionFault.TAG_COMMIT_MISMATCH
    if metadata.get("ref") != f"refs/tags/{expected_tag}":
        _fail(fault)
    target = _mapping(metadata.get("object"), fault)
    if target.get("type") != "commit" or target.get("sha") != expected_commit:
        _fail(fault)


def _commit_identity(metadata: Mapping[str, object], expected_commit: str) -> tuple[str, str, str, str]:
    identity_fault = FreeLLMAPIAdmissionFault.COMMIT_IDENTITY
    if _SHA1_RE.fullmatch(expected_commit) is None or metadata.get("sha") != expected_commit:
        _fail(identity_fault)
    commit = _mapping(metadata.get("commit"), identity_fault)
    tree = _mapping(commit.get("tree"), identity_fault)
    tree_sha = _sha1(tree.get("sha"), identity_fault)

    verification_fault = FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED
    verification = _mapping(commit.get("verification"), verification_fault)
    if verification.get("verified") is not True or verification.get("reason") != "valid":
        _fail(verification_fault)
    signature = _nonempty_string(verification.get("signature"), verification_fault)
    payload = _nonempty_string(verification.get("payload"), verification_fault)
    verified_at = _timestamp(verification.get("verified_at"), verification_fault)
    if len(signature.encode("utf-8")) > _MAX_SIGNATURE_BYTES or len(payload.encode("utf-8")) > _MAX_SIGNATURE_BYTES:
        _fail(verification_fault)
    if f"tree {tree_sha}\n" not in payload:
        _fail(verification_fault)
    return tree_sha, verified_at, _digest(signature.encode()), _digest(payload.encode())


def _safe_member_parts(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name:
        _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)
    return path.parts


def _archive_sources(archive_bytes: bytes) -> dict[str, bytes]:
    if not archive_bytes.startswith(b"\x1f\x8b"):
        _fail(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)
    if not archive_bytes or len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
                _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
            expanded = sum(member.size for member in members if member.isfile())
            if expanded > _MAX_ARCHIVE_EXPANDED_BYTES:
                _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)

            roots: set[str] = set()
            targets: dict[str, list[tarfile.TarInfo]] = {
                path: [] for path in _REQUIRED_SOURCE_PATHS
            }
            for member in members:
                parts = _safe_member_parts(member.name)
                roots.add(parts[0])
                if len(parts) < 2:
                    continue
                relative = "/".join(parts[1:])
                if relative in targets:
                    targets[relative].append(member)
            if len(roots) != 1:
                _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)

            result: dict[str, bytes] = {}
            for relative, matches in targets.items():
                if len(matches) != 1 or not matches[0].isfile():
                    _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)
                member = matches[0]
                if member.size < 0 or member.size > _SOURCE_LIMITS[relative]:
                    _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
                extracted = archive.extractfile(member)
                if extracted is None:
                    _fail(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)
                body = extracted.read(_SOURCE_LIMITS[relative] + 1)
                if len(body) != member.size or len(body) > _SOURCE_LIMITS[relative]:
                    _fail(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
                result[relative] = body
            return result
    except FreeLLMAPIAdmissionError:
        raise
    except (OSError, tarfile.TarError, EOFError):
        _fail(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)


def _validate_license(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        _fail(FreeLLMAPIAdmissionFault.LICENSE_INCOMPATIBLE)
    required = (
        "MIT License",
        "Permission is hereby granted, free of charge",
        'THE SOFTWARE IS PROVIDED "AS IS"',
    )
    if any(fragment not in text for fragment in required):
        _fail(FreeLLMAPIAdmissionFault.LICENSE_INCOMPATIBLE)


def _validate_package_lock(content: bytes) -> None:
    try:
        parsed: object = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)
    if not isinstance(parsed, dict):
        _fail(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)
    if parsed.get("name") != "@freellmapi/monorepo" or parsed.get("lockfileVersion") != 3:
        _fail(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)
    if not isinstance(parsed.get("packages"), dict):
        _fail(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)


def _source_symbols(content: bytes) -> tuple[list[str], list[str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        _fail(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)
    present: list[str] = []
    missing: list[str] = []
    for name in _EXPECTED_EXPORTS:
        function_pattern = rf"\bexport\s+(?:async\s+)?function\s+{re.escape(name)}\s*\("
        value_pattern = rf"\bexport\s+(?:const|let|var)\s+{re.escape(name)}\b"
        target = present if re.search(function_pattern, text) or re.search(value_pattern, text) else missing
        target.append(name)
    return present, missing


def _admitted_artifact(manifest_bytes: bytes, bundle_bytes: bytes) -> dict[str, object]:
    fault = FreeLLMAPIAdmissionFault.ADMITTED_ARTIFACT_INVALID
    if not manifest_bytes or len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        _fail(fault)
    if not bundle_bytes or len(bundle_bytes) > _MAX_BUNDLE_BYTES:
        _fail(fault)
    try:
        parsed: object = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(fault)
    manifest = _mapping(parsed, fault)
    exports = manifest.get("exports")
    if exports != list(_EXPECTED_EXPORTS):
        _fail(fault)
    bundle_digest = _digest(bundle_bytes)
    if manifest.get("bundle_sha256") != bundle_digest or bundle_digest != FREELLMAPI_SCORING_BUNDLE_DIGEST:
        _fail(fault)
    if manifest.get("upstream_commit") != FREELLMAPI_SCORING_UPSTREAM_COMMIT:
        _fail(fault)
    if (
        manifest.get("license") != "MIT"
        or manifest.get("source") != FREELLMAPI_SCORING_SOURCE
        or manifest.get("upstream") != f"https://github.com/{FREELLMAPI_REPOSITORY}"
    ):
        _fail(fault)
    return {
        "abi_version": 1,
        "upstream_commit": FREELLMAPI_SCORING_UPSTREAM_COMMIT,
        "bundle_sha256": bundle_digest,
        "manifest_sha256": _digest(manifest_bytes),
        "exports": list(_EXPECTED_EXPORTS),
    }


def build_candidate_lock(
    *,
    expected_tag: str,
    expected_commit: str,
    repository_metadata: Mapping[str, object],
    release_metadata: Mapping[str, object],
    tag_ref_metadata: Mapping[str, object],
    commit_metadata: Mapping[str, object],
    archive_bytes: bytes,
    admitted_manifest_bytes: bytes,
    admitted_bundle_bytes: bytes,
) -> dict[str, object]:
    """Return a deterministic candidate lock that cannot admit runtime code."""
    repository_id = _repository_identity(repository_metadata)
    release_id, published_at = _release_identity(release_metadata, expected_tag)
    _tag_identity(tag_ref_metadata, expected_tag, expected_commit)
    tree, verified_at, signature_digest, payload_digest = _commit_identity(
        commit_metadata, expected_commit
    )
    sources = _archive_sources(archive_bytes)
    license_bytes = sources["LICENSE"]
    package_lock_bytes = sources["package-lock.json"]
    scoring_bytes = sources[FREELLMAPI_SCORING_SOURCE]
    _validate_license(license_bytes)
    _validate_package_lock(package_lock_bytes)
    symbols_present, symbols_missing = _source_symbols(scoring_bytes)
    admitted = _admitted_artifact(admitted_manifest_bytes, admitted_bundle_bytes)

    if symbols_missing:
        decision = {
            "runtime_admitted": False,
            "state": "rejected_source_symbols",
            "required_gate": "freellmapi-source-symbols-v1",
        }
    else:
        decision = {
            "runtime_admitted": False,
            "state": "pending_frozen_delta",
            "required_gate": FREELLMAPI_FROZEN_DELTA_GATE,
        }

    candidate: dict[str, object] = {
        "schema_version": FREELLMAPI_ADMISSION_SCHEMA_VERSION,
        "decision": decision,
        "upstream": {
            "repository": FREELLMAPI_REPOSITORY,
            "repository_id": repository_id,
            "release_id": release_id,
            "tag": expected_tag,
            "commit": expected_commit,
            "tree": tree,
            "published_at": published_at,
            "verified_at": verified_at,
            "signature_sha256": signature_digest,
            "signed_payload_sha256": payload_digest,
        },
        "archive": {
            "sha256": _digest(archive_bytes),
            "size_bytes": len(archive_bytes),
        },
        "sources": {
            "license": {
                "path": "LICENSE",
                "sha256": _digest(license_bytes),
                "size_bytes": len(license_bytes),
                "spdx": "MIT",
            },
            "package_lock": {
                "path": "package-lock.json",
                "sha256": _digest(package_lock_bytes),
                "size_bytes": len(package_lock_bytes),
            },
            "scoring": {
                "path": FREELLMAPI_SCORING_SOURCE,
                "sha256": _digest(scoring_bytes),
                "size_bytes": len(scoring_bytes),
                "symbols_present": symbols_present,
                "symbols_missing": symbols_missing,
            },
        },
        "admitted_artifact": admitted,
    }
    candidate["candidate_id"] = f"sha256:{_digest(_canonical_bytes(candidate))}"
    return candidate


def validate_candidate_lock(
    candidate_lock: Mapping[str, object],
    *,
    expected_tag: str,
    expected_commit: str,
    admitted_manifest_bytes: bytes,
    admitted_bundle_bytes: bytes,
) -> None:
    """Validate an immutable candidate lock against the still-admitted artifact."""
    fault = FreeLLMAPIAdmissionFault.CANDIDATE_LOCK_INVALID
    if candidate_lock.get("schema_version") != FREELLMAPI_ADMISSION_SCHEMA_VERSION:
        _fail(fault)
    upstream = _mapping(candidate_lock.get("upstream"), fault)
    if (
        upstream.get("repository") != FREELLMAPI_REPOSITORY
        or upstream.get("tag") != expected_tag
        or upstream.get("commit") != expected_commit
    ):
        _fail(fault)
    _positive_int(upstream.get("repository_id"), fault)
    _positive_int(upstream.get("release_id"), fault)
    _sha1(upstream.get("tree"), fault)
    _timestamp(upstream.get("published_at"), fault)
    _timestamp(upstream.get("verified_at"), fault)
    _sha256(upstream.get("signature_sha256"), fault)
    _sha256(upstream.get("signed_payload_sha256"), fault)

    archive = _mapping(candidate_lock.get("archive"), fault)
    _sha256(archive.get("sha256"), fault)
    _positive_int(archive.get("size_bytes"), fault)

    sources = _mapping(candidate_lock.get("sources"), fault)
    license_record = _mapping(sources.get("license"), fault)
    package_record = _mapping(sources.get("package_lock"), fault)
    scoring_record = _mapping(sources.get("scoring"), fault)
    for record, expected_path in (
        (license_record, "LICENSE"),
        (package_record, "package-lock.json"),
        (scoring_record, FREELLMAPI_SCORING_SOURCE),
    ):
        if record.get("path") != expected_path:
            _fail(fault)
        _sha256(record.get("sha256"), fault)
        _positive_int(record.get("size_bytes"), fault)
    if license_record.get("spdx") != "MIT":
        _fail(fault)

    present = scoring_record.get("symbols_present")
    missing = scoring_record.get("symbols_missing")
    if not isinstance(present, list) or not isinstance(missing, list):
        _fail(fault)
    all_symbols = present + missing
    if (
        any(not isinstance(symbol, str) for symbol in all_symbols)
        or len(all_symbols) != len(_EXPECTED_EXPORTS)
        or set(all_symbols) != set(_EXPECTED_EXPORTS)
        or set(present) & set(missing)
    ):
        _fail(fault)

    decision = _mapping(candidate_lock.get("decision"), fault)
    expected_decision = (
        {
            "runtime_admitted": False,
            "state": "rejected_source_symbols",
            "required_gate": "freellmapi-source-symbols-v1",
        }
        if missing
        else {
            "runtime_admitted": False,
            "state": "pending_frozen_delta",
            "required_gate": FREELLMAPI_FROZEN_DELTA_GATE,
        }
    )
    if dict(decision) != expected_decision:
        _fail(fault)

    admitted = _admitted_artifact(admitted_manifest_bytes, admitted_bundle_bytes)
    if candidate_lock.get("admitted_artifact") != admitted:
        _fail(fault)

    stored_id = candidate_lock.get("candidate_id")
    unsigned = dict(candidate_lock)
    unsigned.pop("candidate_id", None)
    expected_id = f"sha256:{_digest(_canonical_bytes(unsigned))}"
    if stored_id != expected_id:
        _fail(fault)
