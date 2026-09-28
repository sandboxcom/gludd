"""Verify immutable FreeLLMAPI updates without acquiring upstream content.

The v0.1.2 boundary accepts only already-collected digest manifests.  It does
not open sockets, read repositories, download artifacts, or mutate config.  A
successful receipt admits an update for shadow evaluation only; runtime
promotion remains a separate gate.
"""

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
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EXPORT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,127}$")
_OWNED_PREFIX = "src/general_ludd/"
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
_SHIM_KEYS = {
    "id",
    "path",
    "kind",
    "owner",
    "sha256",
    "upstream_exports",
    "copied_upstream_source",
}

__all__ = [
    "FREELLMAPI_SYNC_PROTOCOL",
    "FREELLMAPI_SYNC_RECEIPT_PROTOCOL",
    "FreeLLMAPISyncError",
    "FreeLLMAPISyncFault",
    "build_owned_shim_diff",
    "verify_sync_admission",
]


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


def _fail(fault: FreeLLMAPISyncFault) -> NoReturn:
    raise FreeLLMAPISyncError(fault)


def _canonical_bytes(value: object, fault: FreeLLMAPISyncFault) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        _fail(fault)
    return encoded.encode("ascii")


def _digest(value: object, fault: FreeLLMAPISyncFault) -> str:
    return hashlib.sha256(_canonical_bytes(value, fault)).hexdigest()


def _safe_request_digest(request: object) -> str:
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


def _mapping(
    value: object,
    *,
    keys: set[str],
    fault: FreeLLMAPISyncFault,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(fault)
    if any(not isinstance(key, str) for key in value):
        _fail(fault)
    return value


def _nonempty(value: object, fault: FreeLLMAPISyncFault) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        _fail(fault)
    return value


def _sha(value: object, fault: FreeLLMAPISyncFault, *, length: int = 64) -> str:
    text = _nonempty(value, fault)
    pattern = _HEX_40 if length == 40 else _HEX_64
    if pattern.fullmatch(text) is None or set(text) == {"0"}:
        _fail(fault)
    return text


def _positive_int(value: object, fault: FreeLLMAPISyncFault) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(fault)
    return value


def _string_list(
    value: object,
    fault: FreeLLMAPISyncFault,
    *,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        _fail(fault)
    result = [_nonempty(item, fault) for item in value]
    if len(result) != len(set(result)):
        _fail(fault)
    if pattern is not None and any(pattern.fullmatch(item) is None for item in result):
        _fail(fault)
    return sorted(result)


def _version(tag: object) -> tuple[int, int, int]:
    text = _nonempty(tag, FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID)
    match = _STABLE_TAG.fullmatch(text)
    if match is None:
        _fail(FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID)
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _normalise_release(value: object) -> dict[str, object]:
    release = _mapping(
        value,
        keys={"upstream", "supply_chain"},
        fault=FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
    )
    upstream = _mapping(
        release["upstream"],
        keys={"repository", "tag", "commit", "tree", "archive_sha256"},
        fault=FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
    )
    if upstream["repository"] != FREELLMAPI_REPOSITORY:
        _fail(FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID)
    tag = _nonempty(upstream["tag"], FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID)
    _version(tag)
    normalised_upstream = {
        "repository": FREELLMAPI_REPOSITORY,
        "tag": tag,
        "commit": _sha(
            upstream["commit"], FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID, length=40
        ),
        "tree": _sha(
            upstream["tree"], FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID, length=40
        ),
        "archive_sha256": _sha(
            upstream["archive_sha256"], FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID
        ),
    }

    supply_keys = set(_DIGEST_KEYS) | set(_VERIFICATION_KEYS)
    supply = _mapping(
        release["supply_chain"],
        keys=supply_keys,
        fault=FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID,
    )
    normalised_supply: dict[str, object] = {
        key: _sha(supply[key], FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID)
        for key in _DIGEST_KEYS
    }
    for key in _VERIFICATION_KEYS:
        if supply[key] is not True:
            _fail(FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID)
        normalised_supply[key] = True
    if normalised_supply["source_archive_sha256"] != normalised_upstream["archive_sha256"]:
        _fail(FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID)
    return {"upstream": normalised_upstream, "supply_chain": normalised_supply}


def _normalise_shim(value: object) -> dict[str, object]:
    shim = _mapping(
        value,
        keys=_SHIM_KEYS,
        fault=FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID,
    )
    shim_id = _nonempty(shim["id"], FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID)
    if _IDENTIFIER.fullmatch(shim_id) is None:
        _fail(FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID)
    path = _nonempty(shim["path"], FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY)
    path_parts = path.split("/")
    if (
        not path.startswith(_OWNED_PREFIX)
        or path.startswith("/")
        or ".." in path_parts
        or "vendor" in path_parts
        or not path.endswith((".py", ".json", ".md"))
    ):
        _fail(FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY)
    if (
        shim["owner"] != "gludd"
        or shim["kind"] not in {"adapter", "schema", "migration", "test_bridge"}
        or shim["copied_upstream_source"] is not False
    ):
        _fail(FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY)
    return {
        "id": shim_id,
        "path": path,
        "kind": shim["kind"],
        "owner": "gludd",
        "sha256": _sha(shim["sha256"], FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID),
        "upstream_exports": _string_list(
            shim["upstream_exports"],
            FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY,
            pattern=_EXPORT,
        ),
        "copied_upstream_source": False,
    }


def _normalise_shim_list(value: object) -> list[dict[str, object]]:
    fault = FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        _fail(fault)
    result = [_normalise_shim(item) for item in value]
    ids = [str(item["id"]) for item in result]
    paths = [str(item["path"]) for item in result]
    if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
        _fail(fault)
    return sorted(result, key=lambda item: str(item["id"]))


def _normalise_inventory(value: object) -> dict[str, list[dict[str, object]]]:
    inventory = _mapping(
        value,
        keys={"baseline", "candidate"},
        fault=FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID,
    )
    return {
        "baseline": _normalise_shim_list(inventory["baseline"]),
        "candidate": _normalise_shim_list(inventory["candidate"]),
    }


def _diff_normalised(inventory: Mapping[str, Sequence[Mapping[str, object]]]) -> dict[str, object]:
    baseline = {str(item["id"]): item for item in inventory["baseline"]}
    candidate = {str(item["id"]): item for item in inventory["candidate"]}
    old_ids = set(baseline)
    new_ids = set(candidate)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    changed = sorted(key for key in old_ids & new_ids if baseline[key] != candidate[key])
    unchanged = sorted((old_ids & new_ids) - set(changed))
    return {
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged": unchanged,
        "counts": {
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
            "unchanged": len(unchanged),
        },
    }


def build_owned_shim_diff(inventory: Mapping[str, object]) -> dict[str, object]:
    """Return a stable ID-only diff after validating the owned-shim boundary."""
    return _diff_normalised(_normalise_inventory(inventory))


def _normalise_plans(value: object) -> dict[str, object]:
    plans = _mapping(
        value,
        keys={"schema", "migration", "test_reuse"},
        fault=FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID,
    )
    schema = _mapping(
        plans["schema"],
        keys={"from_version", "to_version", "compatibility"},
        fault=FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID,
    )
    from_version = _positive_int(schema["from_version"], FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID)
    to_version = _positive_int(schema["to_version"], FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID)
    if to_version < from_version or schema["compatibility"] != "backward":
        _fail(FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID)

    migration = _mapping(
        plans["migration"],
        keys={"strategy", "steps", "destructive", "contract_deferred"},
        fault=FreeLLMAPISyncFault.MIGRATION_PLAN_INVALID,
    )
    if (
        migration["strategy"] != "expand-migrate-contract"
        or migration["steps"] != ["expand", "dual_read", "backfill", "cutover"]
        or migration["destructive"] is not False
        or migration["contract_deferred"] is not True
    ):
        _fail(FreeLLMAPISyncFault.MIGRATION_PLAN_INVALID)

    test_reuse = _mapping(
        plans["test_reuse"],
        keys={
            "gludd_suites",
            "upstream_suites",
            "upstream_source",
            "copied_upstream_tests",
        },
        fault=FreeLLMAPISyncFault.TEST_REUSE_PLAN_INVALID,
    )
    if (
        test_reuse["upstream_source"] != "ephemeral_verified_archive"
        or test_reuse["copied_upstream_tests"] is not False
    ):
        _fail(FreeLLMAPISyncFault.TEST_REUSE_PLAN_INVALID)
    return {
        "schema": {
            "from_version": from_version,
            "to_version": to_version,
            "compatibility": "backward",
        },
        "migration": {
            "strategy": "expand-migrate-contract",
            "steps": ["expand", "dual_read", "backfill", "cutover"],
            "destructive": False,
            "contract_deferred": True,
        },
        "test_reuse": {
            "gludd_suites": _string_list(
                test_reuse["gludd_suites"], FreeLLMAPISyncFault.TEST_REUSE_PLAN_INVALID
            ),
            "upstream_suites": _string_list(
                test_reuse["upstream_suites"], FreeLLMAPISyncFault.TEST_REUSE_PLAN_INVALID
            ),
            "upstream_source": "ephemeral_verified_archive",
            "copied_upstream_tests": False,
        },
    }


def _normalise_live_boundary(value: object, *, mode: object, allow_live: bool) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.LIVE_BOUNDARY_INVALID
    boundary = _mapping(
        value,
        keys={
            "network_enabled",
            "max_requests",
            "timeout_seconds",
            "max_response_bytes",
            "download_artifacts",
            "mutate_config",
        },
        fault=fault,
    )
    if mode == "offline":
        expected: dict[str, object] = {
            "network_enabled": False,
            "max_requests": 0,
            "timeout_seconds": 0,
            "max_response_bytes": 0,
            "download_artifacts": False,
            "mutate_config": False,
        }
        if dict(boundary) != expected:
            _fail(fault)
        return expected
    if mode != "live":
        _fail(fault)
    if not allow_live:
        _fail(FreeLLMAPISyncFault.LIVE_MODE_NOT_ADMITTED)
    request_count = _positive_int(boundary["max_requests"], fault)
    timeout = _positive_int(boundary["timeout_seconds"], fault)
    response_bytes = _positive_int(boundary["max_response_bytes"], fault)
    if (
        boundary["network_enabled"] is not True
        or request_count > 8
        or timeout > 30
        or response_bytes > 1_048_576
        or boundary["download_artifacts"] is not False
        or boundary["mutate_config"] is not False
    ):
        _fail(fault)
    return {
        "network_enabled": True,
        "max_requests": request_count,
        "timeout_seconds": timeout,
        "max_response_bytes": response_bytes,
        "download_artifacts": False,
        "mutate_config": False,
    }


def _normalise_zdd(value: object, baseline: Mapping[str, object]) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID
    zdd = _mapping(
        value,
        keys={
            "strategy",
            "baseline_commit",
            "baseline_tree",
            "baseline_artifact_sha256",
            "rollback_test_id",
            "rollback_ready",
            "no_downtime",
            "state_write_before_admission",
        },
        fault=fault,
    )
    upstream = baseline["upstream"]
    supply = baseline["supply_chain"]
    assert isinstance(upstream, Mapping)
    assert isinstance(supply, Mapping)
    if (
        zdd["strategy"] != "dual-read-shadow"
        or zdd["baseline_commit"] != upstream["commit"]
        or zdd["baseline_tree"] != upstream["tree"]
        or zdd["baseline_artifact_sha256"] != supply["artifact_sha256"]
        or zdd["rollback_ready"] is not True
        or zdd["no_downtime"] is not True
        or zdd["state_write_before_admission"] is not False
    ):
        _fail(fault)
    return {
        "strategy": "dual-read-shadow",
        "baseline_commit": upstream["commit"],
        "baseline_tree": upstream["tree"],
        "baseline_artifact_sha256": supply["artifact_sha256"],
        "rollback_test_id": _nonempty(zdd["rollback_test_id"], fault),
        "rollback_ready": True,
        "no_downtime": True,
        "state_write_before_admission": False,
    }


def _admit(request: Mapping[str, object], *, allow_live: bool) -> dict[str, object]:
    parsed = _mapping(
        request,
        keys={
            "protocol",
            "mode",
            "baseline",
            "candidate",
            "owned_shims",
            "plans",
            "live_boundary",
            "zdd",
        },
        fault=FreeLLMAPISyncFault.REQUEST_INVALID,
    )
    if parsed["protocol"] != FREELLMAPI_SYNC_PROTOCOL:
        _fail(FreeLLMAPISyncFault.REQUEST_INVALID)
    baseline = _normalise_release(parsed["baseline"])
    candidate = _normalise_release(parsed["candidate"])
    baseline_upstream = baseline["upstream"]
    candidate_upstream = candidate["upstream"]
    assert isinstance(baseline_upstream, Mapping)
    assert isinstance(candidate_upstream, Mapping)
    if _version(candidate_upstream["tag"]) <= _version(baseline_upstream["tag"]):
        _fail(FreeLLMAPISyncFault.UPDATE_NOT_FORWARD)
    for field in ("commit", "tree", "archive_sha256"):
        if candidate_upstream[field] == baseline_upstream[field]:
            _fail(FreeLLMAPISyncFault.UPDATE_NOT_FORWARD)

    inventory = _normalise_inventory(parsed["owned_shims"])
    diff = _diff_normalised(inventory)
    removed = diff["removed"]
    assert isinstance(removed, list)
    if removed:
        _fail(FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID)
    plans = _normalise_plans(parsed["plans"])
    boundary = _normalise_live_boundary(
        parsed["live_boundary"], mode=parsed["mode"], allow_live=allow_live
    )
    zdd = _normalise_zdd(parsed["zdd"], baseline)
    normalised = {
        "protocol": FREELLMAPI_SYNC_PROTOCOL,
        "mode": parsed["mode"],
        "baseline": baseline,
        "candidate": candidate,
        "owned_shims": inventory,
        "plans": plans,
        "live_boundary": boundary,
        "zdd": zdd,
    }
    return {
        "schema_version": 1,
        "protocol": FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
        "decision": "admitted_for_shadow",
        "runtime_admitted": False,
        "fault": None,
        "manifest_sha256": _digest(normalised, FreeLLMAPISyncFault.REQUEST_INVALID),
        "baseline_identity_sha256": _digest(
            baseline, FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID
        ),
        "candidate_identity_sha256": _digest(
            candidate, FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID
        ),
        "owned_shim_inventory_sha256": _digest(
            inventory, FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID
        ),
        "plans_sha256": _digest(plans, FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID),
        "rollback_sha256": _digest(zdd, FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID),
        "update_diff": diff,
    }


def verify_sync_admission(
    request: Mapping[str, object],
    *,
    allow_live: bool = False,
) -> dict[str, object]:
    """Return a deterministic, content-free admission or rejection receipt.

    ``allow_live`` only permits bounded metadata verification declared by the
    caller.  This function remains pure and performs no network or file I/O.
    """
    request_digest = _safe_request_digest(request)
    try:
        return _admit(request, allow_live=allow_live)
    except FreeLLMAPISyncError as error:
        return {
            "schema_version": 1,
            "protocol": FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
            "decision": "rejected",
            "runtime_admitted": False,
            "fault": error.fault.value,
            "manifest_sha256": request_digest,
        }
