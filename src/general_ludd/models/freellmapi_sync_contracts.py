"""Shared, content-free contracts for FreeLLMAPI sync admission."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import NoReturn

FREELLMAPI_SYNC_PROTOCOL = "gludd-freellmapi-sync-admission-v1"
FREELLMAPI_SYNC_RECEIPT_PROTOCOL = "gludd-freellmapi-sync-receipt-v1"
FREELLMAPI_SYNC_OUTCOME_PROTOCOL = "gludd-freellmapi-sync-outcome-v1"
FREELLMAPI_RELEASE_IDENTITY_PROTOCOL = "gludd-freellmapi-release-identity-v1"
FREELLMAPI_REPOSITORY = "tashfeenahmed/freellmapi"

MAX_MANIFEST_BYTES = 64 * 1024
_MAX_MANIFEST_DEPTH = 24
_MAX_MANIFEST_NODES = 4_096
_MAX_CONTAINER_ITEMS = 512
_MAX_MANIFEST_STRING_BYTES = 8 * 1024
_MAX_MANIFEST_INTEGER = 2**63 - 1

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
    MANIFEST_LIMIT = "manifest_limit"
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
    RECEIPT_SCOPE_INVALID = "receipt_scope_invalid"
    UPDATE_OUTCOME_INVALID = "update_outcome_invalid"
    UPDATE_INTERRUPTED = "update_interrupted"
    UPDATE_CANCELLED = "update_cancelled"
    UPDATE_FAILED = "update_failed"
    ROLLBACK_FAILED = "rollback_failed"
    CLEANUP_FAILED = "cleanup_failed"


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
            allow_nan=False,
        ).encode("ascii")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError):
        fail_sync(fault)
    return hashlib.sha256(encoded).hexdigest()


def _manifest_bytes(value: object) -> bytes:
    """Encode a JSON-compatible request within fixed structural bounds."""
    stack: list[tuple[object, int]] = [(value, 0)]
    containers: set[int] = set()
    nodes = 0
    estimated_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_MANIFEST_NODES or depth > _MAX_MANIFEST_DEPTH:
            fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
        if type(item) is str:
            if len(item) > _MAX_MANIFEST_STRING_BYTES:
                fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
            try:
                encoded_length = len(item.encode("utf-8"))
            except UnicodeEncodeError:
                fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
            if encoded_length > _MAX_MANIFEST_STRING_BYTES:
                fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
            estimated_bytes += len(json.dumps(item, ensure_ascii=True))
        elif item is None or type(item) is bool:
            estimated_bytes += 5
        elif type(item) is int:
            if abs(item) > _MAX_MANIFEST_INTEGER:
                fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
            estimated_bytes += len(str(item))
        elif type(item) is dict:
            if len(item) > _MAX_CONTAINER_ITEMS:
                fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
            identity = id(item)
            if identity in containers:
                fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
            containers.add(identity)
            for key, member in item.items():
                if type(key) is not str:
                    fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
                stack.append((key, depth + 1))
                stack.append((member, depth + 1))
        elif type(item) is list:
            if len(item) > _MAX_CONTAINER_ITEMS:
                fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
            identity = id(item)
            if identity in containers:
                fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
            containers.add(identity)
            stack.extend((member, depth + 1) for member in item)
        else:
            fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
        if estimated_bytes > MAX_MANIFEST_BYTES:
            fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("ascii")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError):
        fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
    if len(encoded) > MAX_MANIFEST_BYTES:
        fail_sync(FreeLLMAPISyncFault.MANIFEST_LIMIT)
    return encoded


def bounded_request_digest(request: object) -> str:
    """Hash one structurally and byte-bounded untrusted request."""
    return hashlib.sha256(_manifest_bytes(request)).hexdigest()


def safe_request_digest(request: object) -> str:
    """Hash untrusted input, falling back to a fixed invalid-request digest."""
    try:
        return bounded_request_digest(request)
    except (AttributeError, FreeLLMAPISyncError, RecursionError, TypeError, ValueError):
        return hashlib.sha256(b"invalid-freellmapi-sync-request").hexdigest()


def exact_mapping(
    value: object,
    *,
    keys: set[str],
    fault: FreeLLMAPISyncFault,
) -> dict[str, object]:
    """Require a mapping with exactly the declared string keys."""
    if (
        type(value) is not dict
        or len(value) != len(keys)
        or any(type(key) is not str or key not in keys for key in value)
    ):
        fail_sync(fault)
    return value


def nonempty(value: object, fault: FreeLLMAPISyncFault) -> str:
    """Return a bounded non-empty string or fail closed."""
    if type(value) is not str or not value or len(value) > 256:
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
    if type(value) is not list or not value or len(value) > 128:
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


def normalise_release(
    value: object,
    *,
    verify_identity: bool = True,
) -> dict[str, object]:
    """Validate one immutable upstream and supply-chain identity."""
    release = exact_mapping(
        value,
        keys={"upstream", "supply_chain", "identity_sha256"},
        fault=FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
    )
    upstream = _normalise_upstream(release["upstream"])
    supply_chain = _normalise_supply_chain(
        release["supply_chain"],
        archive_sha256=upstream["archive_sha256"],
    )
    identity = sha(
        release["identity_sha256"],
        FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
    )
    result: dict[str, object] = {
        "upstream": upstream,
        "supply_chain": supply_chain,
        "identity_sha256": identity,
    }
    if verify_identity:
        validate_release_identity(result)
    return result


def validate_release_identity(release: Mapping[str, object]) -> None:
    """Verify the domain-separated binding after update-order checks."""
    expected_identity = canonical_digest(
        {
            "protocol": FREELLMAPI_RELEASE_IDENTITY_PROTOCOL,
            "upstream": release["upstream"],
            "supply_chain": release["supply_chain"],
        },
        FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
    )
    identity = release["identity_sha256"]
    if not isinstance(identity, str) or not hmac.compare_digest(
        identity, expected_identity
    ):
        fail_sync(FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID)


def normalise_receipt_scope(value: object) -> dict[str, object]:
    """Validate the trusted project/operation scope used for anti-replay binding."""
    fault = FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID
    scope = exact_mapping(
        value,
        keys={
            "project_identity_sha256",
            "operation_id",
            "prior_receipt_sha256",
        },
        fault=fault,
    )
    prior = scope["prior_receipt_sha256"]
    if prior is not None:
        prior = sha(prior, fault)
    return {
        "project_identity_sha256": sha(scope["project_identity_sha256"], fault),
        "operation_id": sha(scope["operation_id"], fault),
        "prior_receipt_sha256": prior,
    }
