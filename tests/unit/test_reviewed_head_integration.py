"""Executable contract for integrating independently reviewed heads."""

from __future__ import annotations

from dataclasses import replace

import pytest

from general_ludd.git_release.reviewed_head_integration import (
    AppliedHeadEvidence,
    ExactGateEvidence,
    FocusedValidationEvidence,
    HeadApplicationMode,
    IntegrationStep,
    IntegrationStepKind,
    ReviewedHead,
    ReviewedHeadIntegrationPlan,
    ReviewedHeadIntegrationReceipt,
    build_reviewed_head_integration_plan,
)

_BASE_SHA = "a" * 40
_HEAD_ONE_SHA = "b" * 40
_HEAD_TWO_SHA = "c" * 40
_AFTER_ONE_SHA = "d" * 40
_FINAL_SHA = "e" * 40


def _heads() -> tuple[ReviewedHead, ...]:
    return (
        ReviewedHead(
            source_ref="feature/one",
            source_sha=_HEAD_ONE_SHA,
            reviewed_base_sha=_BASE_SHA,
            review_receipt_sha256="1" * 64,
            focused_validation_ids=("test-one", "lint-shared"),
            application_mode=HeadApplicationMode.CHERRY_PICK,
        ),
        ReviewedHead(
            source_ref="feature/two",
            source_sha=_HEAD_TWO_SHA,
            reviewed_base_sha=_BASE_SHA,
            review_receipt_sha256="2" * 64,
            focused_validation_ids=("test-two", "lint-shared"),
            application_mode=HeadApplicationMode.MERGE_TWO_PARENT,
        ),
    )


def _plan() -> ReviewedHeadIntegrationPlan:
    return build_reviewed_head_integration_plan(
        base_sha=_BASE_SHA,
        heads=_heads(),
        exact_gate_id="ci-gate-exact:3.11",
    )


def _receipt() -> ReviewedHeadIntegrationReceipt:
    plan = _plan()
    return ReviewedHeadIntegrationReceipt(
        plan=plan,
        applied_heads=(
            AppliedHeadEvidence(
                ordinal=1,
                source_ref="feature/one",
                source_sha=_HEAD_ONE_SHA,
                review_receipt_sha256="1" * 64,
                application_mode=HeadApplicationMode.CHERRY_PICK,
                before_sha=_BASE_SHA,
                after_sha=_AFTER_ONE_SHA,
                parent_shas=(_BASE_SHA,),
            ),
            AppliedHeadEvidence(
                ordinal=2,
                source_ref="feature/two",
                source_sha=_HEAD_TWO_SHA,
                review_receipt_sha256="2" * 64,
                application_mode=HeadApplicationMode.MERGE_TWO_PARENT,
                before_sha=_AFTER_ONE_SHA,
                after_sha=_FINAL_SHA,
                parent_shas=(_AFTER_ONE_SHA, _HEAD_TWO_SHA),
            ),
        ),
        focused_validation=FocusedValidationEvidence(
            tip_sha=_FINAL_SHA,
            source_shas=(_HEAD_ONE_SHA, _HEAD_TWO_SHA),
            command_ids=("test-one", "lint-shared", "test-two"),
            run_count=1,
            passed=True,
        ),
        exact_gate=ExactGateEvidence(
            tip_sha=_FINAL_SHA,
            command_id="ci-gate-exact:3.11",
            run_count=1,
            passed=True,
        ),
    )


def test_plan_applies_heads_once_then_validates_and_gates_once() -> None:
    plan = _plan()

    assert tuple(step.kind for step in plan.steps) == (
        IntegrationStepKind.APPLY_ONE_HEAD,
        IntegrationStepKind.APPLY_ONE_HEAD,
        IntegrationStepKind.FOCUSED_VALIDATION,
        IntegrationStepKind.EXACT_GATE,
    )
    assert plan.steps[0].source_shas == (_HEAD_ONE_SHA,)
    assert plan.steps[1].source_shas == (_HEAD_TWO_SHA,)
    assert plan.steps[0].application_mode is HeadApplicationMode.CHERRY_PICK
    assert plan.steps[1].application_mode is HeadApplicationMode.MERGE_TWO_PARENT
    assert plan.focused_validation_ids == ("test-one", "lint-shared", "test-two")
    assert plan.steps[-1].command_ids == ("ci-gate-exact:3.11",)


def test_plan_rejects_duplicate_heads_and_per_head_full_gates() -> None:
    with pytest.raises(ValueError, match="distinct reviewed heads"):
        build_reviewed_head_integration_plan(
            base_sha=_BASE_SHA,
            heads=(_heads()[0], _heads()[0]),
            exact_gate_id="ci-gate-exact:3.11",
        )

    with pytest.raises(ValueError, match="full gates are forbidden"):
        replace(_heads()[0], focused_validation_ids=("gate",))


def test_step_rejects_octopus_sources_and_mixed_validation_shapes() -> None:
    with pytest.raises(ValueError, match="exactly one reviewed head"):
        IntegrationStep(
            kind=IntegrationStepKind.APPLY_ONE_HEAD,
            ordinal=1,
            source_shas=(_HEAD_ONE_SHA, _HEAD_TWO_SHA),
            command_ids=(),
            application_mode=HeadApplicationMode.MERGE_TWO_PARENT,
        )

    with pytest.raises(ValueError, match="cannot apply source heads"):
        IntegrationStep(
            kind=IntegrationStepKind.FOCUSED_VALIDATION,
            ordinal=3,
            source_shas=(_HEAD_ONE_SHA,),
            command_ids=("test-one",),
            application_mode=None,
        )


def test_plan_rejects_gate_between_heads_or_missing_bulk_validation() -> None:
    plan = _plan()
    per_head_gate = IntegrationStep(
        kind=IntegrationStepKind.EXACT_GATE,
        ordinal=2,
        source_shas=(),
        command_ids=("ci-gate-exact:3.11",),
        application_mode=None,
    )
    with pytest.raises(ValueError, match="canonical integration sequence"):
        replace(plan, steps=(plan.steps[0], per_head_gate, *plan.steps[1:]))

    with pytest.raises(ValueError, match="canonical integration sequence"):
        replace(plan, steps=(*plan.steps[:2], plan.steps[-1]))


def test_receipt_binds_each_single_head_application_and_final_tip() -> None:
    receipt = _receipt()

    assert receipt.final_sha == _FINAL_SHA
    assert receipt.applied_heads[0].parent_shas == (_BASE_SHA,)
    assert receipt.applied_heads[1].parent_shas == (
        _AFTER_ONE_SHA,
        _HEAD_TWO_SHA,
    )
    assert receipt.focused_validation.tip_sha == receipt.exact_gate.tip_sha


def test_receipt_rejects_swapped_provenance_or_octopus_parentage() -> None:
    receipt = _receipt()
    first, second = receipt.applied_heads

    with pytest.raises(ValueError, match="reviewed-head order"):
        replace(receipt, applied_heads=(second, first))

    with pytest.raises(ValueError, match="exact one-head parent attribution"):
        replace(
            receipt,
            applied_heads=(
                replace(
                    first,
                    parent_shas=(_BASE_SHA, _HEAD_ONE_SHA, _HEAD_TWO_SHA),
                ),
                second,
            ),
        )

    with pytest.raises(ValueError, match="review receipt"):
        replace(
            receipt,
            applied_heads=(
                replace(first, review_receipt_sha256="3" * 64),
                second,
            ),
        )


def test_receipt_rejects_per_head_or_repeated_validation_and_gate() -> None:
    receipt = _receipt()

    with pytest.raises(ValueError, match="all reviewed heads"):
        replace(
            receipt,
            focused_validation=replace(
                receipt.focused_validation,
                source_shas=(_HEAD_ONE_SHA,),
            ),
        )
    with pytest.raises(ValueError, match="exactly once"):
        replace(
            receipt,
            focused_validation=replace(receipt.focused_validation, run_count=2),
        )
    with pytest.raises(ValueError, match="final integrated tip"):
        replace(receipt, exact_gate=replace(receipt.exact_gate, tip_sha=_AFTER_ONE_SHA))
    with pytest.raises(ValueError, match="exactly once"):
        replace(receipt, exact_gate=replace(receipt.exact_gate, run_count=2))


def test_receipt_fails_closed_on_red_focused_validation_or_gate() -> None:
    receipt = _receipt()
    with pytest.raises(ValueError, match="focused validation must pass"):
        replace(
            receipt,
            focused_validation=replace(receipt.focused_validation, passed=False),
        )
    with pytest.raises(ValueError, match="exact gate must pass"):
        replace(receipt, exact_gate=replace(receipt.exact_gate, passed=False))


def test_head_and_step_inputs_fail_closed_before_integration() -> None:
    head = _heads()[0]
    with pytest.raises(ValueError, match="source_ref"):
        replace(head, source_ref="feature/../escape")
    with pytest.raises(ValueError, match="bounded nonempty tuple"):
        replace(head, focused_validation_ids=())
    with pytest.raises(ValueError, match="distinct per head"):
        replace(head, focused_validation_ids=("test-one", "test-one"))

    with pytest.raises(ValueError, match="positive integer"):
        IntegrationStep(
            kind=IntegrationStepKind.APPLY_ONE_HEAD,
            ordinal=0,
            source_shas=(_HEAD_ONE_SHA,),
            command_ids=(),
            application_mode=HeadApplicationMode.CHERRY_PICK,
        )
    with pytest.raises(ValueError, match="cannot execute validation"):
        IntegrationStep(
            kind=IntegrationStepKind.APPLY_ONE_HEAD,
            ordinal=1,
            source_shas=(_HEAD_ONE_SHA,),
            command_ids=("test-one",),
            application_mode=HeadApplicationMode.CHERRY_PICK,
        )
    with pytest.raises(ValueError, match="require one application mode"):
        IntegrationStep(
            kind=IntegrationStepKind.APPLY_ONE_HEAD,
            ordinal=1,
            source_shas=(_HEAD_ONE_SHA,),
            command_ids=(),
            application_mode=None,
        )
    with pytest.raises(ValueError, match="requires at least one command"):
        IntegrationStep(
            kind=IntegrationStepKind.FOCUSED_VALIDATION,
            ordinal=2,
            source_shas=(),
            command_ids=(),
            application_mode=None,
        )


def test_plan_rejects_empty_base_alias_and_nonexact_gate() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="bounded nonempty tuple"):
        replace(plan, heads=())
    with pytest.raises(ValueError, match="cannot equal"):
        build_reviewed_head_integration_plan(
            base_sha=_BASE_SHA,
            heads=(replace(_heads()[0], source_sha=_BASE_SHA),),
            exact_gate_id="ci-gate-exact:3.11",
        )
    with pytest.raises(ValueError, match="stable union"):
        replace(plan, focused_validation_ids=("test-one",))
    with pytest.raises(ValueError, match="final full gate"):
        replace(plan, exact_gate_id="test-one")


def test_evidence_shapes_reject_aliases_duplicates_and_non_gate_commands() -> None:
    receipt = _receipt()
    first = receipt.applied_heads[0]
    with pytest.raises(ValueError, match="new integration commit"):
        replace(first, after_sha=first.before_sha)
    with pytest.raises(ValueError, match="nonempty and distinct"):
        replace(
            receipt.focused_validation,
            command_ids=("test-one", "test-one"),
        )
    with pytest.raises(ValueError, match="identify a full gate"):
        replace(receipt.exact_gate, command_id="test-one")


def test_receipt_rejects_missing_chain_mode_and_command_bindings() -> None:
    receipt = _receipt()
    first, second = receipt.applied_heads
    with pytest.raises(ValueError, match="one application per reviewed head"):
        replace(receipt, applied_heads=(first,))
    with pytest.raises(ValueError, match="chain is discontinuous"):
        replace(
            receipt,
            applied_heads=(
                first,
                replace(
                    second,
                    before_sha=_BASE_SHA,
                    parent_shas=(_BASE_SHA, _HEAD_TWO_SHA),
                ),
            ),
        )
    with pytest.raises(ValueError, match="planned mode"):
        replace(
            receipt,
            applied_heads=(
                replace(
                    first,
                    application_mode=HeadApplicationMode.MERGE_TWO_PARENT,
                    parent_shas=(_BASE_SHA, _HEAD_ONE_SHA),
                ),
                second,
            ),
        )
    with pytest.raises(ValueError, match="planned union"):
        replace(
            receipt,
            focused_validation=replace(
                receipt.focused_validation,
                command_ids=("lint-shared", "test-one", "test-two"),
            ),
        )
    with pytest.raises(ValueError, match="planned command"):
        replace(receipt, exact_gate=replace(receipt.exact_gate, command_id="gate-full"))
