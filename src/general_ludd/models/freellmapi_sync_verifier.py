"""Verify immutable FreeLLMAPI updates without acquiring upstream content.

The v0.1.2 boundary accepts only already-collected digest manifests. It does
not open sockets, read repositories, download artifacts, or mutate config. A
successful receipt admits an update for shadow evaluation only; runtime
promotion remains a separate gate.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping

from general_ludd.models.freellmapi_sync_contracts import (
    FREELLMAPI_SYNC_OUTCOME_PROTOCOL,
    FREELLMAPI_SYNC_PROTOCOL,
    FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
    FreeLLMAPISyncError,
    FreeLLMAPISyncFault,
    bounded_request_digest,
    canonical_digest,
    exact_mapping,
    fail_sync,
    nonempty,
    normalise_receipt_scope,
    normalise_release,
    safe_request_digest,
    semantic_version,
    sha,
    validate_release_identity,
)
from general_ludd.models.freellmapi_sync_inventory import (
    build_owned_shim_diff,
    diff_normalised,
    normalise_inventory,
)
from general_ludd.models.freellmapi_sync_plans import (
    normalise_live_boundary,
    normalise_plans,
    normalise_zdd,
)

__all__ = [
    "FREELLMAPI_SYNC_OUTCOME_PROTOCOL",
    "FREELLMAPI_SYNC_PROTOCOL",
    "FREELLMAPI_SYNC_RECEIPT_PROTOCOL",
    "FreeLLMAPISyncError",
    "FreeLLMAPISyncFault",
    "build_owned_shim_diff",
    "verify_sync_admission",
    "verify_sync_outcome",
    "verify_sync_receipt_scope",
]

_OUTCOME_KEYS = {
    "state",
    "mutation_started",
    "rollback_attempted",
    "rollback_succeeded",
    "cleanup_attempted",
    "cleanup_succeeded",
}
_SUCCESS_RECEIPT_KEYS = {
    "schema_version",
    "protocol",
    "decision",
    "runtime_admitted",
    "fault",
    "declared_mode",
    "collection_observed",
    "receipt_scope_sha256",
    "manifest_sha256",
    "baseline_identity_sha256",
    "candidate_identity_sha256",
    "owned_shim_inventory_sha256",
    "plans_sha256",
    "rollback_sha256",
    "live_boundary_sha256",
    "update_diff",
}
_SUCCESS_RECEIPT_DIGEST_KEYS = {
    "receipt_scope_sha256",
    "manifest_sha256",
    "baseline_identity_sha256",
    "candidate_identity_sha256",
    "owned_shim_inventory_sha256",
    "plans_sha256",
    "rollback_sha256",
    "live_boundary_sha256",
}
_SHIM_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DIFF_GROUPS = ("added", "changed", "removed", "unchanged")


def _validate_forward_update(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> None:
    baseline_upstream = baseline["upstream"]
    candidate_upstream = candidate["upstream"]
    assert isinstance(baseline_upstream, Mapping)
    assert isinstance(candidate_upstream, Mapping)
    if semantic_version(candidate_upstream["tag"]) <= semantic_version(
        baseline_upstream["tag"]
    ):
        fail_sync(FreeLLMAPISyncFault.UPDATE_NOT_FORWARD)
    for field in ("commit", "tree", "archive_sha256"):
        if candidate_upstream[field] == baseline_upstream[field]:
            fail_sync(FreeLLMAPISyncFault.UPDATE_NOT_FORWARD)


def _success_receipt(
    *,
    normalised: Mapping[str, object],
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    inventory: Mapping[str, object],
    plans: Mapping[str, object],
    boundary: Mapping[str, object],
    zdd: Mapping[str, object],
    diff: Mapping[str, object],
    receipt_scope: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
        "decision": "admitted_for_shadow",
        "runtime_admitted": False,
        "fault": None,
        "declared_mode": normalised["mode"],
        "collection_observed": False,
        "receipt_scope_sha256": canonical_digest(
            receipt_scope, FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID
        ),
        "manifest_sha256": canonical_digest(normalised, FreeLLMAPISyncFault.REQUEST_INVALID),
        "baseline_identity_sha256": baseline["identity_sha256"],
        "candidate_identity_sha256": candidate["identity_sha256"],
        "owned_shim_inventory_sha256": canonical_digest(
            inventory, FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID
        ),
        "plans_sha256": canonical_digest(plans, FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID),
        "rollback_sha256": canonical_digest(zdd, FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID),
        "live_boundary_sha256": canonical_digest(
            boundary, FreeLLMAPISyncFault.LIVE_BOUNDARY_INVALID
        ),
        "update_diff": dict(diff),
    }


def _admit(
    request: Mapping[str, object],
    *,
    allow_live: bool,
    expected_scope: Mapping[str, object],
) -> dict[str, object]:
    parsed = exact_mapping(
        request,
        keys={
            "protocol",
            "receipt_scope",
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
        fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
    receipt_scope = normalise_receipt_scope(parsed["receipt_scope"])
    if receipt_scope != expected_scope:
        fail_sync(FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID)
    baseline = normalise_release(parsed["baseline"])
    candidate = normalise_release(parsed["candidate"], verify_identity=False)
    _validate_forward_update(baseline, candidate)
    validate_release_identity(candidate)
    inventory = normalise_inventory(parsed["owned_shims"])
    diff = diff_normalised(inventory)
    if diff["removed"]:
        fail_sync(FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID)
    plans = normalise_plans(parsed["plans"])
    boundary = normalise_live_boundary(
        parsed["live_boundary"], mode=parsed["mode"], allow_live=allow_live
    )
    zdd = normalise_zdd(parsed["zdd"], baseline)
    normalised = {
        "protocol": FREELLMAPI_SYNC_PROTOCOL,
        "receipt_scope": receipt_scope,
        "mode": parsed["mode"],
        "baseline": baseline,
        "candidate": candidate,
        "owned_shims": inventory,
        "plans": plans,
        "live_boundary": boundary,
        "zdd": zdd,
    }
    return _success_receipt(
        normalised=normalised,
        baseline=baseline,
        candidate=candidate,
        inventory=inventory,
        plans=plans,
        boundary=boundary,
        zdd=zdd,
        diff=diff,
        receipt_scope=receipt_scope,
    )


def _rejection_receipt(
    fault: FreeLLMAPISyncFault,
    *,
    manifest_sha256: str,
    receipt_scope_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
        "decision": "rejected",
        "runtime_admitted": False,
        "fault": fault.value,
        "manifest_sha256": manifest_sha256,
        "receipt_scope_sha256": receipt_scope_sha256,
    }


def _trusted_scope(
    *,
    project_identity_sha256: object,
    operation_id: object,
    prior_receipt_sha256: object,
) -> dict[str, object]:
    return normalise_receipt_scope(
        {
            "project_identity_sha256": project_identity_sha256,
            "operation_id": operation_id,
            "prior_receipt_sha256": prior_receipt_sha256,
        }
    )


def verify_sync_admission(
    request: Mapping[str, object],
    *,
    project_identity_sha256: str,
    operation_id: str,
    prior_receipt_sha256: str | None,
    allow_live: bool = False,
) -> dict[str, object]:
    """Return a deterministic, content-free admission or rejection receipt.

    ``allow_live`` only permits bounded metadata verification declared by the
    caller. This function remains pure and performs no network or file I/O.
    """
    try:
        expected_scope = _trusted_scope(
            project_identity_sha256=project_identity_sha256,
            operation_id=operation_id,
            prior_receipt_sha256=prior_receipt_sha256,
        )
        scope_digest = canonical_digest(
            expected_scope, FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID
        )
    except FreeLLMAPISyncError:
        scope_digest = safe_request_digest(None)
        return _rejection_receipt(
            FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID,
            manifest_sha256=safe_request_digest(request),
            receipt_scope_sha256=scope_digest,
        )
    try:
        request_digest = bounded_request_digest(request)
    except FreeLLMAPISyncError as error:
        return _rejection_receipt(
            error.fault,
            manifest_sha256=safe_request_digest(request),
            receipt_scope_sha256=scope_digest,
        )
    try:
        return _admit(
            request,
            allow_live=allow_live,
            expected_scope=expected_scope,
        )
    except FreeLLMAPISyncError as error:
        return _rejection_receipt(
            error.fault,
            manifest_sha256=request_digest,
            receipt_scope_sha256=scope_digest,
        )


def verify_sync_receipt_scope(
    receipt: object,
    *,
    project_identity_sha256: str,
    operation_id: str,
    prior_receipt_sha256: str | None,
) -> bool:
    """Return whether an admission receipt belongs to the trusted operation."""
    try:
        parsed = exact_mapping(
            receipt,
            keys=_SUCCESS_RECEIPT_KEYS,
            fault=FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID,
        )
        if (
            type(parsed["schema_version"]) is not int
            or parsed["schema_version"] != 1
            or type(parsed["protocol"]) is not str
            or parsed["protocol"] != FREELLMAPI_SYNC_RECEIPT_PROTOCOL
            or type(parsed["decision"]) is not str
            or parsed["decision"] != "admitted_for_shadow"
            or parsed["runtime_admitted"] is not False
            or parsed["fault"] is not None
            or type(parsed["declared_mode"]) is not str
            or parsed["declared_mode"] not in ("offline", "live")
            or parsed["collection_observed"] is not False
        ):
            fail_sync(FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID)
        expected = _trusted_scope(
            project_identity_sha256=project_identity_sha256,
            operation_id=operation_id,
            prior_receipt_sha256=prior_receipt_sha256,
        )
        expected_digest = canonical_digest(
            expected, FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID
        )
        for key in _SUCCESS_RECEIPT_DIGEST_KEYS:
            sha(parsed[key], FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID)
        _normalise_receipt_diff(parsed["update_diff"])
    except FreeLLMAPISyncError:
        return False
    observed = parsed["receipt_scope_sha256"]
    return (
        isinstance(observed, str)
        and hmac.compare_digest(observed, expected_digest)
    )


def _normalise_receipt_diff(value: object) -> dict[str, object]:
    """Validate the deterministic, content-free shim diff carried by a receipt."""
    fault = FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID
    diff = exact_mapping(value, keys={*_DIFF_GROUPS, "counts"}, fault=fault)
    groups: dict[str, list[str]] = {}
    for name in _DIFF_GROUPS:
        raw_group = diff[name]
        if type(raw_group) is not list or len(raw_group) > 256:
            fail_sync(fault)
        normalised = [nonempty(item, fault) for item in raw_group]
        if len(normalised) != len(set(normalised)) or any(
            _SHIM_ID.fullmatch(item) is None for item in normalised
        ):
            fail_sync(fault)
        normalised.sort()
        if diff[name] != normalised:
            fail_sync(fault)
        groups[name] = normalised
    all_ids = [item for name in _DIFF_GROUPS for item in groups[name]]
    if len(all_ids) != len(set(all_ids)):
        fail_sync(fault)
    counts = exact_mapping(diff["counts"], keys=set(_DIFF_GROUPS), fault=fault)
    if any(
        type(counts[name]) is not int or counts[name] != len(groups[name])
        for name in _DIFF_GROUPS
    ):
        fail_sync(fault)
    return {**groups, "counts": {name: len(groups[name]) for name in _DIFF_GROUPS}}


def _normalise_outcome(value: object) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.UPDATE_OUTCOME_INVALID
    outcome = exact_mapping(value, keys=_OUTCOME_KEYS, fault=fault)
    state = outcome["state"]
    if type(state) is not str or state not in (
        "completed",
        "interrupted",
        "cancelled",
        "failed",
    ):
        fail_sync(fault)
    boolean_keys = _OUTCOME_KEYS - {"state"}
    if any(type(outcome[key]) is not bool for key in boolean_keys):
        fail_sync(fault)
    mutation_started = outcome["mutation_started"] is True
    rollback_attempted = outcome["rollback_attempted"] is True
    rollback_succeeded = outcome["rollback_succeeded"] is True
    cleanup_attempted = outcome["cleanup_attempted"] is True
    cleanup_succeeded = outcome["cleanup_succeeded"] is True
    if not cleanup_attempted:
        fail_sync(fault)
    if state == "completed":
        if (
            not mutation_started
            or rollback_attempted
            or rollback_succeeded
            or not cleanup_succeeded
        ):
            fail_sync(fault)
    elif (
        rollback_attempted != mutation_started
        or (not rollback_attempted and rollback_succeeded)
    ):
        fail_sync(fault)
    return {
        "state": state,
        "mutation_started": mutation_started,
        "rollback_attempted": rollback_attempted,
        "rollback_succeeded": rollback_succeeded,
        "cleanup_attempted": True,
        "cleanup_succeeded": cleanup_succeeded,
    }


def _classify_outcome(outcome: Mapping[str, object]) -> tuple[str, str | None]:
    state = outcome["state"]
    if state == "completed":
        return "shadow_updated", None
    if outcome["rollback_attempted"] is True and outcome["rollback_succeeded"] is False:
        return "rollback_failed", FreeLLMAPISyncFault.ROLLBACK_FAILED.value
    if outcome["cleanup_succeeded"] is False:
        return "cleanup_failed", FreeLLMAPISyncFault.CLEANUP_FAILED.value
    primary_fault = {
        "interrupted": FreeLLMAPISyncFault.UPDATE_INTERRUPTED.value,
        "cancelled": FreeLLMAPISyncFault.UPDATE_CANCELLED.value,
        "failed": FreeLLMAPISyncFault.UPDATE_FAILED.value,
    }[str(state)]
    decision = "rolled_back" if outcome["mutation_started"] is True else "aborted"
    return decision, primary_fault


def _outcome_receipt(
    *,
    admission_digest: str,
    outcome_digest: str,
    scope_digest: str,
    decision: str,
    fault: str | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": FREELLMAPI_SYNC_OUTCOME_PROTOCOL,
        "decision": decision,
        "runtime_admitted": False,
        "fault": fault,
        "receipt_scope_sha256": scope_digest,
        "admission_receipt_sha256": admission_digest,
        "outcome_sha256": outcome_digest,
    }


def verify_sync_outcome(
    admission_receipt: Mapping[str, object],
    outcome: Mapping[str, object],
    *,
    project_identity_sha256: str,
    operation_id: str,
    prior_receipt_sha256: str | None,
) -> dict[str, object]:
    """Verify one content-free, non-mutating update outcome and its precedence."""
    scope_digest = safe_request_digest(None)
    admission_digest = safe_request_digest(admission_receipt)
    outcome_digest = safe_request_digest(outcome)
    try:
        expected_scope = _trusted_scope(
            project_identity_sha256=project_identity_sha256,
            operation_id=operation_id,
            prior_receipt_sha256=prior_receipt_sha256,
        )
        scope_digest = canonical_digest(
            expected_scope, FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID
        )
        admission_digest = bounded_request_digest(admission_receipt)
        outcome_digest = bounded_request_digest(outcome)
        if not verify_sync_receipt_scope(
            admission_receipt,
            project_identity_sha256=project_identity_sha256,
            operation_id=operation_id,
            prior_receipt_sha256=prior_receipt_sha256,
        ):
            fail_sync(FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID)
        normalised = _normalise_outcome(outcome)
        decision, fault = _classify_outcome(normalised)
        outcome_digest = canonical_digest(
            normalised, FreeLLMAPISyncFault.UPDATE_OUTCOME_INVALID
        )
    except FreeLLMAPISyncError as error:
        return _outcome_receipt(
            admission_digest=admission_digest,
            outcome_digest=outcome_digest,
            scope_digest=scope_digest,
            decision="rejected",
            fault=error.fault.value,
        )
    return _outcome_receipt(
        admission_digest=admission_digest,
        outcome_digest=outcome_digest,
        scope_digest=scope_digest,
        decision=decision,
        fault=fault,
    )
