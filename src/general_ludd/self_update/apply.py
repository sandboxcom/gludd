"""The self-update apply ladder (issue #81).

Given a :class:`~general_ludd.self_update.model.SelfUpdatePlan`, decide *whether*
and *how* to land it, fail-closed at every rung. The ladder, in preference
order:

  (a) **config/var/template edit** — the preferred path. Hot-reloadable, no
      Python touched, may auto-apply (no human approval) when nothing protected
      is involved and validation passes.
  (b) **scaffold a new role/connector/profile** from a template — new files
      only, never an in-place edit of guard surface.
  (c) **guarded real-code change** — always requires explicit approval AND a
      green validate (lint/type) before landing.

Every rung is gated by the SAME guards the live daemon uses:

  * :func:`general_ludd.security.capability_lattice.is_protected_path` /
    :func:`~...capability_lattice.check_self_modification` — a request that
    targets a guardrail / policy / permission / security / settings file is
    REFUSED outright (the critical guarantee), regardless of tier or approval,
    unless an explicit ``approval_token`` is present (and even then a hard
    deny-listed guard stem is still refused — see :func:`_is_hard_denied`).
  * a validate gate (lint/type) that must pass before any code-tier change
    lands.

Every decision — applied, deferred-for-approval, or refused — writes an audit
record so a self-improvement loop can never mutate the system invisibly.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from general_ludd.security.auth import verify_psk
from general_ludd.security.capability_lattice import (
    CapabilityError,
    ProtectedPathError,
    check_self_modification,
    is_protected_path,
)
from general_ludd.security.path_canonicalizer import (
    _HARD_DENY_SEGMENTS as _CANONICAL_HARD_DENY_SEGMENTS,
)
from general_ludd.security.path_canonicalizer import (
    is_denied_path,
)
from general_ludd.self_update.model import ApplyTier, ChangeKind, SelfUpdatePlan, SelfUpdateRequest

_HARD_DENY_SEGMENTS = _CANONICAL_HARD_DENY_SEGMENTS


class ApplyOutcome:
    """Terminal outcomes of an apply attempt."""

    APPLIED = "applied"
    AWAITING_APPROVAL = "awaiting_approval"
    REFUSED = "refused"
    VALIDATION_FAILED = "validation_failed"


@dataclass(frozen=True)
class AuditRecord:
    """The immutable record written for every apply decision.

    Attributes:
        outcome: One of :class:`ApplyOutcome`.
        subsystem: The plan's target subsystem (string value).
        change_kind: The plan's change kind (string value).
        apply_tier: The *effective* tier after guard evaluation (may be
            ``refused`` even if the plan proposed config/code).
        target_files: The files the decision concerned.
        requested_by: Who filed the originating request.
        reason: Human-readable explanation (why applied / deferred / refused).
        approved: Whether an approval token was honoured for this decision.
        timestamp: Unix epoch seconds the decision was made.
    """

    outcome: str
    subsystem: str
    change_kind: str
    apply_tier: str
    target_files: tuple[str, ...]
    requested_by: str
    reason: str
    approved: bool = False
    timestamp: float = field(default_factory=lambda: time.time())

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping (for AuditEventRepository.details)."""
        return {
            "outcome": self.outcome,
            "subsystem": self.subsystem,
            "change_kind": self.change_kind,
            "apply_tier": self.apply_tier,
            "target_files": list(self.target_files),
            "requested_by": self.requested_by,
            "reason": self.reason,
            "approved": self.approved,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class ApplyResult:
    """The result of :func:`apply_plan`: the outcome + its audit record."""

    outcome: str
    audit: AuditRecord
    landed_files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def applied(self) -> bool:
        """Return whether the plan was applied successfully."""
        return self.outcome == ApplyOutcome.APPLIED


def _is_hard_denied(path: str) -> bool:
    """True if ``path`` matches the canonical deny-list (C9: unified canonicalizer).

    Delegates to :func:`~general_ludd.security.path_canonicalizer.is_denied_path`
    so the hard-deny check in apply.py and the protected-path check in
    capability_lattice.py share the same canonical deny set — no cross-module
    drift.
    """
    return is_denied_path(path)


def _any_protected(target_files: tuple[str, ...], role: str | None) -> str | None:
    """Return the first target path that the guards refuse, else ``None``.

    Consults BOTH :func:`is_protected_path` (the guard deny-list) and
    :func:`check_self_modification` (which additionally raises for a
    collections/ self-modify the role may not perform). Fail-closed: any guard
    objection short-circuits to that path.
    """
    for path in target_files:
        if is_protected_path(path) or _is_hard_denied(path):
            return path
        try:
            check_self_modification(path, role)
        except (ProtectedPathError, CapabilityError):
            return path
    return None


#: A validate callable returns (ok, detail). Default is a no-op that PASSES only
#: when explicitly provided — see apply_plan's contract for code-tier.
ValidateFn = Callable[[SelfUpdatePlan], tuple[bool, str]]
AuditSink = Callable[[AuditRecord], None]


def _audit_record(
    plan: SelfUpdatePlan,
    request: SelfUpdateRequest,
    outcome: str,
    tier: ApplyTier,
    reason: str,
    *,
    approved: bool = False,
) -> AuditRecord:
    """Build one immutable decision record from the effective plan."""
    return AuditRecord(
        outcome=outcome,
        subsystem=plan.subsystem.value,
        change_kind=plan.change_kind.value,
        apply_tier=tier.value,
        target_files=plan.target_files,
        requested_by=request.requested_by,
        reason=reason,
        approved=approved,
    )


def _emit_apply_result(
    record: AuditRecord,
    audit_sink: AuditSink | None,
) -> ApplyResult:
    """Publish one decision before returning its public result."""
    if audit_sink is not None:
        audit_sink(record)
    landed = record.target_files if record.outcome == ApplyOutcome.APPLIED else ()
    return ApplyResult(
        outcome=record.outcome,
        audit=record,
        landed_files=landed,
    )


def _protected_path_decision(
    plan: SelfUpdatePlan,
    request: SelfUpdateRequest,
    *,
    role: str,
    has_approval: bool,
    audit_sink: AuditSink | None,
) -> tuple[SelfUpdatePlan, ApplyResult | None]:
    """Refuse protected targets or escalate an approved target to code tier."""
    offending = _any_protected(plan.target_files, role)
    if offending is None:
        return plan, None
    if _is_hard_denied(offending) or not has_approval:
        result = _emit_apply_result(
            _audit_record(
                plan,
                request,
                ApplyOutcome.REFUSED,
                ApplyTier.REFUSED,
                f"refused: target {offending!r} is a protected "
                f"guardrail/policy/security/settings path "
                f"({'hard-deny' if _is_hard_denied(offending) else 'no approval token'})",
            ),
            audit_sink,
        )
        return plan, result
    escalated = SelfUpdatePlan(
        subsystem=plan.subsystem,
        change_kind=ChangeKind.CODE_CHANGE,
        target_files=plan.target_files,
        apply_tier=ApplyTier.CODE,
        requires_approval=True,
        rationale=plan.rationale + " | escalated: approved protected-path edit",
        confidence=plan.confidence,
    )
    return escalated, None


def _routing_decision(
    plan: SelfUpdatePlan,
    request: SelfUpdateRequest,
    *,
    has_approval: bool,
    audit_sink: AuditSink | None,
) -> ApplyResult | None:
    """Defer unknown or explicitly approval-bound plans before tier routing."""
    if plan.subsystem.value == "unknown" or plan.change_kind is ChangeKind.UNKNOWN:
        return _emit_apply_result(
            _audit_record(
                plan,
                request,
                ApplyOutcome.AWAITING_APPROVAL,
                plan.apply_tier,
                "deferred: unroutable request (unknown subsystem/change-kind) "
                "— requires operator approval + manual targeting",
            ),
            audit_sink,
        )
    if plan.requires_approval and not has_approval:
        return _emit_apply_result(
            _audit_record(
                plan,
                request,
                ApplyOutcome.AWAITING_APPROVAL,
                plan.apply_tier,
                "deferred: requires_approval and no valid approval token "
                "(fail-closed)",
            ),
            audit_sink,
        )
    return None


def _apply_config_tier(
    plan: SelfUpdatePlan,
    request: SelfUpdateRequest,
    *,
    validate: ValidateFn | None,
    audit_sink: AuditSink | None,
    auto_apply_config: bool,
    has_approval: bool,
) -> ApplyResult:
    """Apply or defer one validated config/scaffold-tier plan."""
    if not plan.target_files:
        record = _audit_record(
            plan,
            request,
            ApplyOutcome.AWAITING_APPROVAL,
            plan.apply_tier,
            "deferred: no concrete target file resolved — refusing to "
            "auto-apply without a target",
        )
        return _emit_apply_result(record, audit_sink)

    if validate is not None:
        ok, detail = validate(plan)
        if not ok:
            record = _audit_record(
                plan,
                request,
                ApplyOutcome.VALIDATION_FAILED,
                plan.apply_tier,
                f"validation failed before landing: {detail}",
            )
            return _emit_apply_result(record, audit_sink)

    if not auto_apply_config:
        record = _audit_record(
            plan,
            request,
            ApplyOutcome.AWAITING_APPROVAL,
            plan.apply_tier,
            "deferred: auto-apply disabled by caller",
        )
    else:
        record = _audit_record(
            plan,
            request,
            ApplyOutcome.APPLIED,
            plan.apply_tier,
            f"auto-applied {plan.apply_tier.value}-tier change "
            f"(hot-reloadable, no protected path involved)",
            approved=has_approval,
        )
    return _emit_apply_result(record, audit_sink)


def _apply_code_tier(
    plan: SelfUpdatePlan,
    request: SelfUpdateRequest,
    *,
    validate: ValidateFn | None,
    audit_sink: AuditSink | None,
    has_approval: bool,
) -> ApplyResult:
    """Apply approved validated code, otherwise return the fail-closed result."""
    if not has_approval:
        record = _audit_record(
            plan,
            request,
            ApplyOutcome.AWAITING_APPROVAL,
            ApplyTier.CODE,
            "deferred: code-tier change requires explicit operator approval",
        )
    elif validate is None:
        record = _audit_record(
            plan,
            request,
            ApplyOutcome.VALIDATION_FAILED,
            ApplyTier.CODE,
            "refused: approved code change has no validate gate "
            "(fail-closed — unverified code is never landed)",
            approved=True,
        )
    else:
        ok, detail = validate(plan)
        record = _audit_record(
            plan,
            request,
            ApplyOutcome.APPLIED if ok else ApplyOutcome.VALIDATION_FAILED,
            ApplyTier.CODE,
            (
                "applied approved + validated code-tier change"
                if ok
                else f"validation failed before landing: {detail}"
            ),
            approved=True,
        )
    return _emit_apply_result(record, audit_sink)


def apply_plan(
    plan: SelfUpdatePlan,
    request: SelfUpdateRequest,
    *,
    role: str | None = None,
    validate: ValidateFn | None = None,
    audit_sink: AuditSink | None = None,
    auto_apply_config: bool = True,
    approval_secret: str | None = None,
) -> ApplyResult:
    """Run the apply ladder for ``plan`` and emit an audit record.

    Decision order (fail-closed):

    1. **Protected-path / guard refusal (the critical gate).** If ANY target
       file is a protected guardrail/policy/permission/security/settings path
       — or a collections/ self-modify the role may not perform — the request
       is REFUSED. A hard-deny path (``.claude``/``.opencode``/settings) is
       refused even with an approval token; any other protected path is refused
       UNLESS a VALID ``approval_token`` is presented (one matching
       ``approval_secret`` under constant-time comparison — see SU-A).
    2. **Unknown / unroutable.** An UNKNOWN subsystem or change-kind, or a tier
       with no concrete target files where files are required, is deferred for
       approval (never auto-applied).
    2b. **requires_approval.** If the plan's ``requires_approval`` flag is set
       (e.g. a SECURITY-subsystem config edit) and no VALID approval token was
       presented, the request is deferred for approval before any auto-apply
       rung — fail-closed.
    3. **Tier ladder.**
         * CONFIG  -> auto-apply (when ``auto_apply_config`` and not protected),
           after validation if a validator is supplied.
         * SCAFFOLD -> auto-apply new files (validation if supplied).
         * CODE    -> ALWAYS requires approval; with approval, must pass
           ``validate`` (a missing validator fails closed -> VALIDATION_FAILED).

    The ``role`` is threaded into the capability lattice so a role lacking
    ``collections_self_modify`` cannot drive a collections write through here.
    """
    effective_role = role or request.requested_by
    expected_secret = (
        approval_secret
        if approval_secret is not None
        else os.environ.get("GLUDD_SELF_UPDATE_APPROVAL_SECRET", "")
    )
    has_approval = verify_psk(request.approval_token or "", expected_secret)
    effective_plan, protected_result = _protected_path_decision(
        plan,
        request,
        role=effective_role,
        has_approval=has_approval,
        audit_sink=audit_sink,
    )
    if protected_result is not None:
        return protected_result
    routing_result = _routing_decision(
        effective_plan,
        request,
        has_approval=has_approval,
        audit_sink=audit_sink,
    )
    if routing_result is not None:
        return routing_result
    if effective_plan.apply_tier in (ApplyTier.CONFIG, ApplyTier.SCAFFOLD):
        return _apply_config_tier(
            effective_plan,
            request,
            validate=validate,
            audit_sink=audit_sink,
            auto_apply_config=auto_apply_config,
            has_approval=has_approval,
        )
    return _apply_code_tier(
        effective_plan,
        request,
        validate=validate,
        audit_sink=audit_sink,
        has_approval=has_approval,
    )
