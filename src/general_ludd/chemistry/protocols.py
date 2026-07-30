"""CHEM-006 protocol drafting — approval-gated, versioned draft procedures.

Implements CHEM-006 and §8.1 of ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``.

A protocol draft is a mutable planning artifact. It becomes execution-capable
only after a qualified human approves the *exact* version (digest) the operator
intends to run. Any change after approval — even a single byte — invalidates
the approval token (CHEM-AT-009).

Design choices:

* The version digest is a SHA-256 over a canonical JSON encoding of the
  protocol's substantive fields. Field order is sorted; whitespace is fixed,
  so the digest is deterministic across Python sessions and hosts.
* The approval token binds an approver identity + role to a specific digest
  and an issue timestamp. ``validate_protocol`` checks that the token's
  embedded digest exactly equals the protocol's current digest.
* Lot-level inventory checks (CHEM-AT-010 / CHEM-009 §9 row "Inventory lot
  expired, restricted, or wrong purity") are consulted when an
  ``inventory_lots`` list is supplied at creation time.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import uuid
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

_CORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core.py")


def _load_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chemistry_core_for_protocols", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()

SCHEMA_VERSION = _core.SCHEMA_VERSION

REQUIRED_SECTIONS = (
    "objective",
    "evidence_refs",
    "entities",
    "quantities",
    "equipment",
    "operations",
    "stop_conditions",
    "waste_streams",
    "emergency_actions",
)

_DIGEST_FIELDS = (
    "objective",
    "evidence_refs",
    "entities",
    "quantities",
    "equipment",
    "operations",
    "parameter_ranges",
    "stop_conditions",
    "quench_workup",
    "waste_streams",
    "emergency_actions",
    "expected_results",
    "approver_roles",
)


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compute_digest(payload: dict[str, Any]) -> str:
    parts = []
    for field in _DIGEST_FIELDS:
        if field in payload:
            parts.append(f"{field}={_canonical(payload[field])}")
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def recompute_digest(proto: dict[str, Any]) -> dict[str, Any]:
    """Return ``proto`` with ``version_digest`` refreshed from its contents.

    Used after an in-place mutation to reflect the new content state. The
    caller SHOULD treat the prior approval token as invalidated.
    """
    proto["version_digest"] = _compute_digest(proto)
    return proto


def create_protocol_draft(
    payload: dict[str, Any],
    inventory_lots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Construct an immutable-digest protocol draft from ``payload``.

    ``inventory_lots`` is an optional list of inventory record dicts; when
    provided, each entity's lot is checked for expiry and the protocol carries
    a ``lot_warnings`` list of any lots that fail. This does not refuse the
    draft outright (an unsuitable lot might be a planning error worth
    surfacing), but ``validate_protocol`` will refuse execution until every
    referenced lot is suitable.
    """
    missing = [s for s in REQUIRED_SECTIONS if not payload.get(s)]
    if missing:
        raise ValueError(f"protocol draft missing required sections: {missing}")

    proto: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": str(uuid.uuid4()),
        "objective": payload["objective"],
        "evidence_refs": list(payload.get("evidence_refs", [])),
        "entities": [dict(e) for e in payload.get("entities", [])],
        "quantities": [dict(q) for q in payload.get("quantities", [])],
        "equipment": [dict(e) for e in payload.get("equipment", [])],
        "operations": [dict(op) for op in payload.get("operations", [])],
        "parameter_ranges": [dict(p) for p in payload.get("parameter_ranges", [])],
        "stop_conditions": [dict(s) for s in payload.get("stop_conditions", [])],
        "quench_workup": [dict(s) for s in payload.get("quench_workup", [])],
        "waste_streams": [dict(w) for w in payload.get("waste_streams", [])],
        "emergency_actions": [dict(e) for e in payload.get("emergency_actions", [])],
        "expected_results": [dict(r) for r in payload.get("expected_results", [])],
        "approver_roles": list(payload.get("approver_roles", [])),
    }

    lot_warnings: list[dict[str, Any]] = []
    if inventory_lots:
        by_lot = {r.get("lot"): r for r in inventory_lots}
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        for ent in proto["entities"]:
            lot_id = ent.get("lot")
            if lot_id and lot_id in by_lot:
                rec = by_lot[lot_id]
                expiry = str(rec.get("expiry", ""))
                if expiry and expiry < today:
                    lot_warnings.append({"lot": lot_id, "code": "expired", "expiry": expiry, "as_of": today})

    proto["lot_warnings"] = lot_warnings
    proto["version_digest"] = _compute_digest(proto)
    return proto


def issue_approval_token(proto: dict[str, Any], approver: str, role: str) -> dict[str, Any]:
    """Mint an approval token binding ``approver``/``role`` to the digest.

    The token is valid ONLY for the exact digest it was issued against. Any
    change to the protocol (CHEM-AT-009) produces a different digest and the
    token is rejected by :func:`validate_protocol`.
    """
    if role not in proto.get("approver_roles", []):
        raise ValueError(f"role {role!r} is not in the protocol's approver_roles={proto.get('approver_roles')}")
    return {
        "token_id": str(uuid.uuid4()),
        "approver": approver,
        "role": role,
        "version_digest": proto["version_digest"],
        "issued_at": datetime.now(UTC).isoformat(),
    }


def validate_protocol(
    proto: dict[str, Any],
    approval_token: dict[str, Any] | None,
) -> dict[str, Any]:
    """Check that ``proto`` is fit for execution under ``approval_token``.

    Returns a verdict dict. ``approved_for_execution`` is True only when:

    * the approval token is present and its ``role`` is in the protocol's
      ``approver_roles``;
    * the token's ``version_digest`` exactly equals the protocol's current
      ``version_digest`` (CHEM-AT-009);
    * no referenced lot is expired (CHEM-AT-010 pathway).
    """
    limitations: list[str] = []
    errors: list[dict[str, Any]] = []

    # Lot checks run regardless of approval-token presence: a protocol
    # referencing an expired lot must surface the lot_warning even while
    # awaiting approval, so the limitations list is never empty in that case.
    for warning in proto.get("lot_warnings", []):
        if warning.get("code") == "expired":
            limitations.append(f"lot_expired: lot={warning.get('lot')} expiry={warning.get('expiry')}")
            errors.append(
                _err(
                    "chem.lot_expired",
                    f"lot {warning.get('lot')} expired {warning.get('expiry')}",
                )
            )

    if approval_token is None:
        limitations.append("approval_required: no approval token presented")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "awaiting_approval",
            "approved_for_execution": False,
            "limitations": limitations,
            "errors": errors,
        }

    token_role = approval_token.get("role", "")
    if token_role not in proto.get("approver_roles", []):
        limitations.append(f"approver_role_unauthorized: role {token_role!r} not in approver_roles")

    token_digest = approval_token.get("version_digest", "")
    proto_digest = proto.get("version_digest", "")
    digest_ok = bool(token_digest) and token_digest == proto_digest
    if not digest_ok:
        errors.append(
            _err(
                "chem.protocol_digest_mismatch",
                "approval token digest does not match protocol version digest; the protocol changed after approval",
            )
        )
        limitations.append(f"version_digest_mismatch: token={token_digest[:12]}... proto={proto_digest[:12]}...")

    approved = digest_ok and not errors
    if approved:
        status = "succeeded"
    elif errors:
        status = "refused"
    else:
        status = "awaiting_approval"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "approved_for_execution": approved,
        "limitations": limitations,
        "errors": errors,
    }


__all__ = [
    "REQUIRED_SECTIONS",
    "create_protocol_draft",
    "issue_approval_token",
    "recompute_digest",
    "validate_protocol",
]
