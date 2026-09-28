"""Shared, content-free contracts for FreeLLMAPI sync admission."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import NoReturn

FREELLMAPI_SYNC_PROTOCOL = "gludd-freellmapi-sync-admission-v1"
FREELLMAPI_SYNC_RECEIPT_PROTOCOL = "gludd-freellmapi-sync-receipt-v1"
FREELLMAPI_REPOSITORY = "tashfeenahmed/freellmapi"

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_STABLE_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_DIGEST_KEYS = (
    "source_archive_sha256",
    "package_lock_sha256",
    "license_sha256",
    "build_recipe_sha256",
    "artifact_sha256",
    "artifact_signature_sha256",
    "sbom_sha256",
    "provenance_sha256",
)
_VERIFICATION_KEYS = (
    "tag_verified",
    "commit_verified",
    "tree_verified",
    "artifact_signature_verified",
)


class FreeLLMAPISyncFault(StrEnum):
    """Stable, content-free v0.1.2 sync rejection categories."""

    REQUEST_INVALID = "request_invalid"
    IMMUTABLE_IDENTITY_INVALID = "immutable_identity_invalid"
    UPDATE_NOT_FORWARD = "update_not_forward"
    SUPPLY_CHAIN_INVALID = "supply_chain_invalid"
    OWNED_SHIM_INVENTORY_INVALID = "owned_shim_inventory_invalid"
    OWNED_SHIM_BOUNDARY = "owned_shim_boundary"
    SCHEMA_PLAN_INVALID = "schema_plan_invalid"
    MIGRATION_PLAN_INVALID = "migration_plan_invalid"
    TEST_REUSE_PLAN_INVALID = "test_reuse_plan_invalid"
    LIVE_BOUNDARY_INVALID = "live_boundary_invalid"
    LIVE_MODE_NOT_ADMITTED = "live_mode_not_admitted"
    ZDD_ROLLBACK_INVALID = "zdd_rollback_invalid"


class FreeLLMAPISyncError(ValueError):
    """Internal fail-closed error containing no untrusted input."""

    def __init__(self, fault: FreeLLMAPISyncFault) -> None:
        """Store only a stable fault identifier."""
        self.fault = fault
        super().__init__(fault.value)


def fail_sync(fault: FreeLLMAPISyncFault) -> NoReturn:
    """Raise a content-free sync fault."""
    raise FreeLLMAPISyncError(fault)


def canonical_digest(value: object, fault: FreeLLMAPISyncFault) -> str:
    """Hash one JSON-compatible normalized contract."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError):
        fail_sync(fault)
    return hashlib.sha256(encoded).hexdigest()


def safe_request_digest(request: object) -> str:
    """Hash untrusted input, falling back to a fixed invalid-request digest."""
    try:
        return hashlib.sha256(
            json.dumps(
                request,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
        return hashlib.sha256(b"invalid-freellmapi-sync-request").hexdigest()


def exact_mapping(
    value: object,
    *,
    keys: set[str],
    fault: FreeLLMAPISyncFault,
) -> Mapping[str, object]:
    """Require a mapping with exactly the declared string keys."""
    if not isinstance(value, Mapping) or set(value) != keys:
        fail_sync(fault)
    return value


def nonempty(value: object, fault: FreeLLMAPISyncFault) -> str:
    """Return a bounded non-empty string or fail closed."""
    if not isinstance(value, str) or not value or len(value) > 256:
        fail_sync(fault)
    return value


def sha(value: object, fault: FreeLLMAPISyncFault, *, length: int = 64) -> str:
    """Validate a full non-placeholder hexadecimal digest."""
    text = nonempty(value, fault)
    pattern = _HEX_40 if length == 40 else _HEX_64
    if pattern.fullmatch(text) is None or set(text) == {"0"}:
        fail_sync(fault)
    return text


def positive_int(value: object, fault: FreeLLMAPISyncFault) -> int:
    """Validate a positive integer without treating bool as int."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        fail_sync(fault)
    return value


def string_list(
    value: object,
    fault: FreeLLMAPISyncFault,
    *,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    """Normalize one non-empty, unique list of bounded strings."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        fail_sync(fault)
    result = [nonempty(item, fault) for item in value]
    if len(result) != len(set(result)):
        fail_sync(fault)
    if pattern is not None and any(pattern.fullmatch(item) is None for item in result):
        fail_sync(fault)
    return sorted(result)


def semantic_version(tag: object) -> tuple[int, int, int]:
    """Parse a stable three-part release tag."""
    text = nonempty(tag, FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID)
    match = _STABLE_TAG.fullmatch(text)
    if match is None:
        fail_sync(FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID)
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _normalise_upstream(value: object) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID
    upstream = exact_mapping(
        value,
        keys={"repository", "tag", "commit", "tree", "archive_sha256"},
        fault=fault,
    )
    if upstream["repository"] != FREELLMAPI_REPOSITORY:
        fail_sync(fault)
    tag = nonempty(upstream["tag"], fault)
    semantic_version(tag)
    return {
        "repository": FREELLMAPI_REPOSITORY,
        "tag": tag,
        "commit": sha(upstream["commit"], fault, length=40),
        "tree": sha(upstream["tree"], fault, length=40),
        "archive_sha256": sha(upstream["archive_sha256"], fault),
    }


def _normalise_supply_chain(
    value: object,
    *,
    archive_sha256: object,
) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID
    supply = exact_mapping(
        value,
        keys=set(_DIGEST_KEYS) | set(_VERIFICATION_KEYS),
        fault=fault,
    )
    result: dict[str, object] = {key: sha(supply[key], fault) for key in _DIGEST_KEYS}
    for key in _VERIFICATION_KEYS:
        if supply[key] is not True:
            fail_sync(fault)
        result[key] = True
    if result["source_archive_sha256"] != archive_sha256:
        fail_sync(fault)
    return result


def normalise_release(value: object) -> dict[str, object]:
    """Validate one immutable upstream and supply-chain identity."""
    release = exact_mapping(
        value,
        keys={"upstream", "supply_chain"},
        fault=FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
    )
    upstream = _normalise_upstream(release["upstream"])
    supply_chain = _normalise_supply_chain(
        release["supply_chain"],
        archive_sha256=upstream["archive_sha256"],
    )
    return {"upstream": upstream, "supply_chain": supply_chain}
