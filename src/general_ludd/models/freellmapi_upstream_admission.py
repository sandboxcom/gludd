"""Build non-runnable, provenance-bound FreeLLMAPI update candidates.

This universal model-infrastructure boundary deliberately stops before runtime
promotion.  It verifies discovery evidence and records exact source identities;
the separate frozen-delta gate remains responsible for admitting an artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime

from general_ludd.models.freellmapi_upstream_source import (
    FREELLMAPI_EXPECTED_EXPORTS as _EXPECTED_EXPORTS,
)
from general_ludd.models.freellmapi_upstream_source import (
    FREELLMAPI_REPOSITORY,
    FREELLMAPI_SCORING_SOURCE,
    FreeLLMAPIAdmissionError,
    FreeLLMAPIAdmissionFault,
    inspect_upstream_archive,
    validate_admitted_artifact,
)
from general_ludd.models.freellmapi_upstream_source import (
    fail_admission as _fail,
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
FREELLMAPI_FROZEN_DELTA_GATE = "freellmapi-frozen-delta-v1"

_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_SIGNATURE_BYTES = 1024 * 1024
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")


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
    source_evidence = inspect_upstream_archive(
        archive_bytes,
        max_archive_bytes=_MAX_ARCHIVE_BYTES,
    )
    license_bytes = source_evidence.license_bytes
    package_lock_bytes = source_evidence.package_lock_bytes
    scoring_bytes = source_evidence.scoring_bytes
    symbols_present = source_evidence.symbols_present
    symbols_missing = source_evidence.symbols_missing
    admitted = validate_admitted_artifact(admitted_manifest_bytes, admitted_bundle_bytes)

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

    admitted = validate_admitted_artifact(admitted_manifest_bytes, admitted_bundle_bytes)
    if candidate_lock.get("admitted_artifact") != admitted:
        _fail(fault)

    stored_id = candidate_lock.get("candidate_id")
    unsigned = dict(candidate_lock)
    unsigned.pop("candidate_id", None)
    expected_id = f"sha256:{_digest(_canonical_bytes(unsigned))}"
    if stored_id != expected_id:
        _fail(fault)
