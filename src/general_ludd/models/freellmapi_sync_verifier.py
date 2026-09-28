"""Verify immutable FreeLLMAPI updates without acquiring upstream content.

The v0.1.2 boundary accepts only already-collected digest manifests. It does
not open sockets, read repositories, download artifacts, or mutate config. A
successful receipt admits an update for shadow evaluation only; runtime
promotion remains a separate gate.
"""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.models.freellmapi_sync_contracts import (
    FREELLMAPI_SYNC_PROTOCOL,
    FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
    FreeLLMAPISyncError,
    FreeLLMAPISyncFault,
    canonical_digest,
    exact_mapping,
    fail_sync,
    normalise_release,
    safe_request_digest,
    semantic_version,
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
    "FREELLMAPI_SYNC_PROTOCOL",
    "FREELLMAPI_SYNC_RECEIPT_PROTOCOL",
    "FreeLLMAPISyncError",
    "FreeLLMAPISyncFault",
    "build_owned_shim_diff",
    "verify_sync_admission",
]


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
    zdd: Mapping[str, object],
    diff: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
        "decision": "admitted_for_shadow",
        "runtime_admitted": False,
        "fault": None,
        "manifest_sha256": canonical_digest(normalised, FreeLLMAPISyncFault.REQUEST_INVALID),
        "baseline_identity_sha256": canonical_digest(
            baseline, FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID
        ),
        "candidate_identity_sha256": canonical_digest(
            candidate, FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID
        ),
        "owned_shim_inventory_sha256": canonical_digest(
            inventory, FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID
        ),
        "plans_sha256": canonical_digest(plans, FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID),
        "rollback_sha256": canonical_digest(zdd, FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID),
        "update_diff": dict(diff),
    }


def _admit(request: Mapping[str, object], *, allow_live: bool) -> dict[str, object]:
    parsed = exact_mapping(
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
        fail_sync(FreeLLMAPISyncFault.REQUEST_INVALID)
    baseline = normalise_release(parsed["baseline"])
    candidate = normalise_release(parsed["candidate"])
    _validate_forward_update(baseline, candidate)
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
        zdd=zdd,
        diff=diff,
    )


def verify_sync_admission(
    request: Mapping[str, object],
    *,
    allow_live: bool = False,
) -> dict[str, object]:
    """Return a deterministic, content-free admission or rejection receipt.

    ``allow_live`` only permits bounded metadata verification declared by the
    caller. This function remains pure and performs no network or file I/O.
    """
    request_digest = safe_request_digest(request)
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
