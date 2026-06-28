"""Security unit tests for the self-update apply ladder (``apply.apply_plan``).

Covers the two CONFIRMED HIGH findings from
``docs/audit/POST_ALPHA4_SECURITY_FINDINGS_2026-06-25.md``:

  * **SU-A** — approval truthiness: an approval token must be honoured ONLY when
    it matches a configured secret under constant-time comparison; a non-empty
    but WRONG token (or any token when the secret is unset) must NOT approve.
  * **SU-B** — ``requires_approval`` unenforced: a plan whose
    ``requires_approval`` flag is set (e.g. a SECURITY-subsystem config edit)
    must be deferred for approval instead of auto-applying when no valid
    approval token is presented.

These tests construct plans directly (no classifier dependency) so the apply
ladder's gating logic is exercised in isolation.
"""

from __future__ import annotations

from general_ludd.self_update.apply import ApplyOutcome, apply_plan
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    SelfUpdateRequest,
    Subsystem,
)


def _config_plan(*, requires_approval: bool = False) -> SelfUpdatePlan:
    """A benign, routable CONFIG-tier plan with a concrete target file."""
    return SelfUpdatePlan(
        subsystem=Subsystem.SPEND,
        change_kind=ChangeKind.VALUE_EDIT,
        target_files=("config/spend.yml",),
        apply_tier=ApplyTier.CONFIG,
        requires_approval=requires_approval,
        rationale="test plan",
        confidence=0.9,
    )


# --- SU-A: approval truthiness ------------------------------------------------


def test_su_a_nonempty_wrong_token_does_not_approve_when_secret_unset() -> None:
    """A non-empty approval token must NOT approve when no secret is configured.

    This is the SU-A fail-closed core: ``bool(token)`` used to approve ANY
    non-empty string. With an empty/unset secret, ``verify_psk`` returns False,
    so a CODE-tier change (which always needs approval) is deferred.
    """
    plan = SelfUpdatePlan(
        subsystem=Subsystem.GATEWAY,
        change_kind=ChangeKind.CODE_CHANGE,
        target_files=("src/general_ludd/gateway.py",),
        apply_tier=ApplyTier.CODE,
        requires_approval=True,
        rationale="code change",
        confidence=0.9,
    )
    request = SelfUpdateRequest(
        raw_text="change the gateway",
        approval_token="i-am-an-attacker-guess",
    )

    # No secret configured (default empty) -> token cannot be valid.
    result = apply_plan(plan, request, approval_secret="")

    assert result.outcome == ApplyOutcome.AWAITING_APPROVAL
    assert result.audit.approved is False


def test_su_a_wrong_token_does_not_approve_when_secret_set() -> None:
    """A non-empty token that mismatches the configured secret must NOT approve."""
    plan = _config_plan(requires_approval=True)
    request = SelfUpdateRequest(
        raw_text="edit config",
        approval_token="wrong-token",
    )

    result = apply_plan(plan, request, approval_secret="the-real-secret")

    assert result.outcome == ApplyOutcome.AWAITING_APPROVAL
    assert result.audit.approved is False


def test_su_a_correct_token_approves_requires_approval_plan() -> None:
    """A token EQUAL to the configured secret is honoured (constant-time match)."""
    plan = _config_plan(requires_approval=True)
    secret = "correct-horse-battery-staple"
    request = SelfUpdateRequest(
        raw_text="edit config",
        approval_token=secret,
    )

    result = apply_plan(plan, request, approval_secret=secret)

    # With a VALID approval token the requires_approval gate passes and the
    # benign CONFIG plan auto-applies.
    assert result.outcome == ApplyOutcome.APPLIED
    assert result.audit.approved is True


def test_su_a_empty_token_never_approves() -> None:
    """An absent/empty approval token never approves even with a secret set."""
    plan = _config_plan(requires_approval=True)
    request = SelfUpdateRequest(raw_text="edit config", approval_token=None)

    result = apply_plan(plan, request, approval_secret="the-real-secret")

    assert result.outcome == ApplyOutcome.AWAITING_APPROVAL
    assert result.audit.approved is False


def test_su_a_reads_env_secret_when_param_omitted(monkeypatch) -> None:
    """When ``approval_secret`` is None, the env var is the source of truth."""
    secret = "env-configured-secret"
    monkeypatch.setenv("GLUDD_SELF_UPDATE_APPROVAL_SECRET", secret)
    plan = _config_plan(requires_approval=True)
    request = SelfUpdateRequest(raw_text="edit config", approval_token=secret)

    result = apply_plan(plan, request)  # approval_secret defaults to env read

    assert result.outcome == ApplyOutcome.APPLIED
    assert result.audit.approved is True


def test_su_a_env_unset_fails_closed(monkeypatch) -> None:
    """With the env secret unset, no token can approve a requires_approval plan."""
    monkeypatch.delenv("GLUDD_SELF_UPDATE_APPROVAL_SECRET", raising=False)
    plan = _config_plan(requires_approval=True)
    request = SelfUpdateRequest(raw_text="edit config", approval_token="anything")

    result = apply_plan(plan, request)

    assert result.outcome == ApplyOutcome.AWAITING_APPROVAL
    assert result.audit.approved is False


# --- SU-B: requires_approval enforced -----------------------------------------


def test_su_b_requires_approval_config_plan_is_deferred_not_applied() -> None:
    """A requires_approval CONFIG plan must NOT auto-apply without approval.

    This is the SU-B core: previously ``apply_plan`` never read
    ``plan.requires_approval``, so a SECURITY-subsystem CONFIG VALUE_EDIT would
    fall through to the auto-apply branch. It must now be deferred.
    """
    plan = SelfUpdatePlan(
        subsystem=Subsystem.SECURITY,
        change_kind=ChangeKind.VALUE_EDIT,
        target_files=("config/security_tuning.yml",),
        apply_tier=ApplyTier.CONFIG,
        requires_approval=True,
        rationale="security config edit",
        confidence=0.9,
    )
    request = SelfUpdateRequest(raw_text="tune security", approval_token=None)

    result = apply_plan(plan, request, approval_secret="some-secret")

    assert result.outcome == ApplyOutcome.AWAITING_APPROVAL
    assert "requires_approval" in result.audit.reason
    # It must NOT have landed any files.
    assert result.landed_files == ()


def test_su_b_requires_approval_plan_applies_with_valid_token() -> None:
    """The same requires_approval plan DOES land with a valid approval token."""
    secret = "approve-me"
    plan = SelfUpdatePlan(
        subsystem=Subsystem.SECURITY,
        change_kind=ChangeKind.VALUE_EDIT,
        target_files=("config/security_tuning.yml",),
        apply_tier=ApplyTier.CONFIG,
        requires_approval=True,
        rationale="security config edit",
        confidence=0.9,
    )
    request = SelfUpdateRequest(raw_text="tune security", approval_token=secret)

    result = apply_plan(plan, request, approval_secret=secret)

    assert result.outcome == ApplyOutcome.APPLIED
    assert result.audit.approved is True


def test_su_b_non_requires_approval_config_still_auto_applies() -> None:
    """Regression: a benign plan that does NOT require approval still auto-applies."""
    plan = _config_plan(requires_approval=False)
    request = SelfUpdateRequest(raw_text="edit config", approval_token=None)

    result = apply_plan(plan, request, approval_secret="")

    assert result.outcome == ApplyOutcome.APPLIED
