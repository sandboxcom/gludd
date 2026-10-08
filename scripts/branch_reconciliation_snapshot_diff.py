"""Pure bounded comparison of two sealed reconciliation snapshots."""

from __future__ import annotations

import hmac
import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import branch_reconciliation_plan_types as plan_types
    from branch_reconciliation_plan_types import (
        ReconciliationSnapshotBranch,
        ReconciliationSnapshotCounts,
        ReconciliationSnapshotDiffPayload,
        ReconciliationSnapshotHead,
        ReconciliationSnapshotPayload,
        ReconciliationStatus,
        RetirementBasis,
        RetirementReasonCode,
    )
elif not __package__:
    plan_types = importlib.import_module("branch_reconciliation_plan_types")
else:
    from scripts import branch_reconciliation_plan_types as plan_types

RefValidator = Callable[[object], str]
ObjectValidator = Callable[[str], bool]
SNAPSHOT_DIFF_DETAIL_LIMIT = 100
SNAPSHOT_DIFF_INPUT_JSON_CHAR_LIMIT = 33_558_528
SNAPSHOT_DIFF_OUTPUT_JSON_CHAR_LIMIT = 1_048_576
_BRANCH_KEYS = frozenset(
    {
        "approval_digest",
        "approval_expires_at",
        "approval_issued_at",
        "approval_key_id",
        "current_head",
        "expected_tip",
        "fresh_classification",
        "ref",
        "retirement_basis",
        "retirement_reason_code",
        "status",
    }
)
_SNAPSHOT_KEYS = frozenset(
    {
        "approval_keyring_digest",
        "bounds",
        "branches",
        "complete",
        "counts",
        "freshness_digest",
        "heads",
        "mode",
        "ok",
        "plan_digest",
        "schema_version",
        "snapshot_digest",
        "target",
        "verification_time",
    }
)
_STATUS_PRIORITY = {
    "explicitly-retired": 0,
    "merged": 1,
    "pending": 2,
    "stale": 3,
    "blocked": 4,
}


def _optional_digest(value: object) -> str | None:
    """Validate one optional SHA-256 value."""
    return None if value is None else plan_types._snapshot_digest(value)


def _optional_timestamp(value: object) -> str | None:
    """Validate one optional canonical approval timestamp."""
    return None if value is None else plan_types._retirement_timestamp(value)


def _parse_branch(
    value: object,
    *,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
) -> ReconciliationSnapshotBranch:
    """Validate one snapshot branch without retaining free-form evidence."""
    branch = plan_types._snapshot_dict(value, _BRANCH_KEYS)
    try:
        ref = valid_ref(branch["ref"])
    except Exception as exc:
        raise plan_types._snapshot_error() from exc
    expected_tip = plan_types._snapshot_object_id(
        branch["expected_tip"], valid_object_id
    )
    current_value = branch["current_head"]
    current_head = (
        None
        if current_value is None
        else plan_types._snapshot_object_id(current_value, valid_object_id)
    )
    classification = branch["fresh_classification"]
    if classification not in {None, "ancestor", "patch-equivalent", "unique"}:
        raise plan_types._snapshot_error()
    status_value = branch["status"]
    if status_value not in _STATUS_PRIORITY:
        raise plan_types._snapshot_error()
    status = cast("ReconciliationStatus", status_value)
    basis_value = branch["retirement_basis"]
    if basis_value not in {
        None,
        "fresh-ancestor",
        "fresh-patch-equivalent",
        "operator-approval",
    }:
        raise plan_types._snapshot_error()
    basis = cast("RetirementBasis | None", basis_value)
    reason_value = branch["retirement_reason_code"]
    if reason_value is not None and reason_value not in plan_types._RETIREMENT_REASON_CODES:
        raise plan_types._snapshot_error()
    reason = cast("RetirementReasonCode | None", reason_value)
    approval_digest = _optional_digest(branch["approval_digest"])
    approval_key_value = branch["approval_key_id"]
    approval_key = (
        None
        if approval_key_value is None
        else plan_types._approval_key_id(approval_key_value)
    )
    approval_issued = _optional_timestamp(branch["approval_issued_at"])
    approval_expires = _optional_timestamp(branch["approval_expires_at"])
    approval_values = (
        approval_digest,
        approval_expires,
        approval_issued,
        approval_key,
    )
    if status == "explicitly-retired":
        if current_head is not None or classification is not None:
            raise plan_types._snapshot_error()
        if basis == "operator-approval":
            if (
                reason is None
                or any(item is None for item in approval_values)
                or cast(str, approval_issued) >= cast(str, approval_expires)
            ):
                raise plan_types._snapshot_error()
        elif basis in {"fresh-ancestor", "fresh-patch-equivalent"}:
            if reason is not None or any(item is not None for item in approval_values):
                raise plan_types._snapshot_error()
        else:
            raise plan_types._snapshot_error()
    else:
        if (
            current_head is None
            or classification is None
            or basis is not None
            or reason is not None
            or any(item is not None for item in approval_values)
        ):
            raise plan_types._snapshot_error()
        if status == "stale":
            valid_state = current_head != expected_tip
        elif status == "merged":
            valid_state = current_head == expected_tip and classification in {
                "ancestor",
                "patch-equivalent",
            }
        else:
            valid_state = (
                current_head == expected_tip
                and classification == "unique"
                and status in {"blocked", "pending"}
            )
        if not valid_state:
            raise plan_types._snapshot_error()
    return {
        "approval_digest": approval_digest,
        "approval_expires_at": approval_expires,
        "approval_issued_at": approval_issued,
        "approval_key_id": approval_key,
        "current_head": current_head,
        "expected_tip": expected_tip,
        "fresh_classification": classification,
        "ref": ref,
        "retirement_basis": basis,
        "retirement_reason_code": reason,
        "status": status,
    }


def _parse_bounds(value: object, entry_limit: int) -> tuple[int, int]:
    """Validate the snapshot's recorded input and serialization ceilings."""
    bounds = plan_types._snapshot_dict(
        value,
        frozenset(
            {
                "approval_key_limit",
                "approval_keyring_json_char_limit",
                "approval_signature_limit",
                "entry_limit",
                "json_char_limit",
                "retirement_limit",
            }
        ),
    )
    snapshot_entry_limit = plan_types._snapshot_integer(
        bounds["entry_limit"], minimum=1, maximum=entry_limit
    )
    snapshot_json_limit = plan_types._snapshot_integer(
        bounds["json_char_limit"],
        minimum=1,
        maximum=plan_types.SNAPSHOT_JSON_CHAR_LIMIT,
    )
    exact_bounds = {
        "approval_key_limit": plan_types.APPROVAL_KEY_LIMIT,
        "approval_keyring_json_char_limit": plan_types.APPROVAL_KEYRING_JSON_CHAR_LIMIT,
        "approval_signature_limit": plan_types.APPROVAL_SIGNATURE_LIMIT,
        "retirement_limit": snapshot_entry_limit,
    }
    for key, expected in exact_bounds.items():
        if (
            plan_types._snapshot_integer(
                bounds[key], minimum=0, maximum=max(expected, entry_limit)
            )
            != expected
        ):
            raise plan_types._snapshot_error()
    return snapshot_entry_limit, snapshot_json_limit


def _expected_heads(
    branches: list[ReconciliationSnapshotBranch],
) -> list[ReconciliationSnapshotHead]:
    """Rebuild the canonical deduplicated-head accounting."""
    aliases_by_head: dict[str, list[ReconciliationSnapshotBranch]] = {}
    for branch in branches:
        aliases_by_head.setdefault(branch["expected_tip"], []).append(branch)
    return [
        {
            "expected_tip": head,
            "refs": sorted(alias["ref"] for alias in aliases),
            "status": max(
                (alias["status"] for alias in aliases),
                key=_STATUS_PRIORITY.__getitem__,
            ),
        }
        for head, aliases in sorted(aliases_by_head.items())
    ]


def _parse_heads(
    value: object,
    *,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    entry_limit: int,
) -> list[ReconciliationSnapshotHead]:
    """Validate sorted, unique, bounded head aliases."""
    if not isinstance(value, list) or len(value) > entry_limit:
        raise plan_types._snapshot_error()
    result: list[ReconciliationSnapshotHead] = []
    for raw_head in value:
        head = plan_types._snapshot_dict(
            raw_head, frozenset({"expected_tip", "refs", "status"})
        )
        status = head["status"]
        refs_value = head["refs"]
        if status not in _STATUS_PRIORITY or not isinstance(refs_value, list) or not refs_value:
            raise plan_types._snapshot_error()
        try:
            refs = [valid_ref(ref) for ref in refs_value]
        except Exception as exc:
            raise plan_types._snapshot_error() from exc
        if refs != sorted(set(refs)):
            raise plan_types._snapshot_error()
        result.append(
            {
                "expected_tip": plan_types._snapshot_object_id(
                    head["expected_tip"], valid_object_id
                ),
                "refs": refs,
                "status": cast("ReconciliationStatus", status),
            }
        )
    return result


def _expected_counts(
    branches: list[ReconciliationSnapshotBranch], head_count: int
) -> ReconciliationSnapshotCounts:
    """Return complete redundant accounting for validated rows."""
    return {
        "blocked": sum(branch["status"] == "blocked" for branch in branches),
        "explicitly_retired": sum(
            branch["status"] == "explicitly-retired" for branch in branches
        ),
        "merged": sum(branch["status"] == "merged" for branch in branches),
        "pending": sum(branch["status"] == "pending" for branch in branches),
        "stale": sum(branch["status"] == "stale" for branch in branches),
        "total_branches": len(branches),
        "total_heads": head_count,
    }


def _parse_counts(
    value: object, entry_limit: int
) -> ReconciliationSnapshotCounts:
    """Validate exact non-boolean status and total counts."""
    keys = (
        "blocked",
        "explicitly_retired",
        "merged",
        "pending",
        "stale",
        "total_branches",
        "total_heads",
    )
    raw = plan_types._snapshot_dict(value, frozenset(keys))
    return cast(
        "ReconciliationSnapshotCounts",
        {
            key: plan_types._snapshot_integer(
                raw[key], minimum=0, maximum=entry_limit
            )
            for key in keys
        },
    )


def _parse_snapshot(
    value: object,
    *,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    entry_limit: int,
) -> tuple[ReconciliationSnapshotPayload, dict[str, str]]:
    """Verify one complete snapshot envelope, seal, and redundant accounting."""
    snapshot = plan_types._snapshot_dict(value, _SNAPSHOT_KEYS)
    snapshot_entry_limit, snapshot_json_limit = _parse_bounds(
        snapshot["bounds"], entry_limit
    )
    if (
        snapshot["mode"] != "reconciliation-snapshot"
        or snapshot["ok"] is not True
        or plan_types._snapshot_integer(
            snapshot["schema_version"], minimum=2, maximum=2
        )
        != 2
    ):
        raise plan_types._snapshot_error()
    target = plan_types._snapshot_target(
        snapshot["target"], valid_ref=valid_ref, valid_object_id=valid_object_id
    )
    raw_branches = snapshot["branches"]
    if not isinstance(raw_branches, list) or len(raw_branches) > snapshot_entry_limit:
        raise plan_types._snapshot_error()
    branches = [
        _parse_branch(
            branch, valid_ref=valid_ref, valid_object_id=valid_object_id
        )
        for branch in raw_branches
    ]
    refs = [branch["ref"] for branch in branches]
    if refs != sorted(set(refs)):
        raise plan_types._snapshot_error()
    expected_heads = _expected_heads(branches)
    heads = _parse_heads(
        snapshot["heads"],
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        entry_limit=snapshot_entry_limit,
    )
    if heads != expected_heads:
        raise plan_types._snapshot_error()
    expected_counts = _expected_counts(branches, len(heads))
    counts = _parse_counts(snapshot["counts"], snapshot_entry_limit)
    if counts != expected_counts:
        raise plan_types._snapshot_error()
    complete = not any(
        branch["status"] in {"blocked", "pending", "stale"}
        for branch in branches
    )
    if not isinstance(snapshot["complete"], bool) or snapshot["complete"] is not complete:
        raise plan_types._snapshot_error()
    keyring_digest = _optional_digest(snapshot["approval_keyring_digest"])
    verification_time = _optional_timestamp(snapshot["verification_time"])
    signed = [
        branch
        for branch in branches
        if branch["retirement_basis"] == "operator-approval"
    ]
    if bool(signed) != bool(keyring_digest) or bool(signed) != bool(verification_time):
        raise plan_types._snapshot_error()
    if verification_time is not None and any(
        cast(str, branch["approval_issued_at"]) > verification_time
        or verification_time >= cast(str, branch["approval_expires_at"])
        for branch in signed
    ):
        raise plan_types._snapshot_error()
    freshness_digest = plan_types._snapshot_digest(snapshot["freshness_digest"])
    plan_digest = plan_types._snapshot_digest(snapshot["plan_digest"])
    snapshot_digest = plan_types._snapshot_digest(snapshot["snapshot_digest"])
    body = {
        "approval_keyring_digest": keyring_digest,
        "branches": branches,
        "counts": counts,
        "freshness_digest": freshness_digest,
        "heads": heads,
        "plan_digest": plan_digest,
        "target": target,
        "verification_time": verification_time,
    }
    expected_digest = plan_types.canonical_document_digest(
        body, char_limit=snapshot_json_limit
    )
    if not hmac.compare_digest(snapshot_digest, expected_digest):
        raise plan_types._snapshot_error()
    payload = cast("ReconciliationSnapshotPayload", snapshot)
    plan_types._canonical_document(payload, snapshot_json_limit)
    return payload, {
        branch["ref"]: branch["current_head"] or branch["expected_tip"]
        for branch in branches
    }


def build_reconciliation_snapshot_diff(
    request_value: object,
    *,
    valid_ref: RefValidator,
    valid_object_id: ObjectValidator,
    expected_target: str | None = None,
    detail_limit: int = SNAPSHOT_DIFF_DETAIL_LIMIT,
    entry_limit: int = plan_types.SNAPSHOT_ENTRY_LIMIT,
    input_json_char_limit: int = SNAPSHOT_DIFF_INPUT_JSON_CHAR_LIMIT,
    output_json_char_limit: int = SNAPSHOT_DIFF_OUTPUT_JSON_CHAR_LIMIT,
) -> ReconciliationSnapshotDiffPayload:
    """Diff two sealed snapshots without consulting or mutating repository state."""
    detail_limit = plan_types._snapshot_integer(
        detail_limit, minimum=0, maximum=SNAPSHOT_DIFF_DETAIL_LIMIT
    )
    entry_limit = plan_types._snapshot_integer(
        entry_limit, minimum=1, maximum=plan_types.SNAPSHOT_ENTRY_LIMIT
    )
    input_json_char_limit = plan_types._snapshot_integer(
        input_json_char_limit,
        minimum=1,
        maximum=SNAPSHOT_DIFF_INPUT_JSON_CHAR_LIMIT,
    )
    output_json_char_limit = plan_types._snapshot_integer(
        output_json_char_limit,
        minimum=1,
        maximum=SNAPSHOT_DIFF_OUTPUT_JSON_CHAR_LIMIT,
    )
    plan_types._canonical_document(request_value, input_json_char_limit)
    request = plan_types._snapshot_dict(
        request_value, frozenset({"after", "before"})
    )
    before, before_heads = _parse_snapshot(
        request["before"],
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        entry_limit=entry_limit,
    )
    after, after_heads = _parse_snapshot(
        request["after"],
        valid_ref=valid_ref,
        valid_object_id=valid_object_id,
        entry_limit=entry_limit,
    )
    identity = {
        "input": before["target"]["input"],
        "ref": before["target"]["ref"],
    }
    if identity != {
        "input": after["target"]["input"],
        "ref": after["target"]["ref"],
    } or (expected_target is not None and identity["input"] != expected_target):
        raise plan_types._snapshot_error()
    before_refs = set(before_heads)
    after_refs = set(after_heads)
    added = sorted(after_refs - before_refs)
    removed = sorted(before_refs - after_refs)
    common = sorted(before_refs & after_refs)
    changed = [ref for ref in common if before_heads[ref] != after_heads[ref]]
    unchanged = [ref for ref in common if before_heads[ref] == after_heads[ref]]
    categories = {
        "added": added,
        "changed_head": changed,
        "removed": removed,
        "unchanged": unchanged,
    }
    details: dict[str, list[dict[str, str]]] = {
        "added": [
            {"head": after_heads[ref], "ref": ref} for ref in added[:detail_limit]
        ],
        "changed_head": [
            {
                "after_head": after_heads[ref],
                "before_head": before_heads[ref],
                "ref": ref,
            }
            for ref in changed[:detail_limit]
        ],
        "removed": [
            {"head": before_heads[ref], "ref": ref}
            for ref in removed[:detail_limit]
        ],
        "unchanged": [
            {"head": before_heads[ref], "ref": ref}
            for ref in unchanged[:detail_limit]
        ],
    }
    payload: ReconciliationSnapshotDiffPayload = {
        "after_snapshot_digest": after["snapshot_digest"],
        "before_snapshot_digest": before["snapshot_digest"],
        "bounds": {
            "detail_limit": detail_limit,
            "entry_limit": entry_limit,
            "input_json_char_limit": input_json_char_limit,
            "output_json_char_limit": output_json_char_limit,
        },
        "counts": {key: len(refs) for key, refs in categories.items()},
        "details": details,
        "details_truncated": {
            key: len(refs) > detail_limit for key, refs in categories.items()
        },
        "mode": "reconciliation-snapshot-diff",
        "ok": True,
        "schema_version": 2,
        "target_identity_digest": plan_types.canonical_document_digest(identity),
    }
    plan_types._canonical_document(payload, output_json_char_limit)
    return payload
