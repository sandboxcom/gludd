"""Executable contract for integrating independently reviewed heads."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from general_ludd.git_release.reviewed_head_integration import (
    AppliedHeadEvidence,
    ExactGateEvidence,
    FocusedValidationEvidence,
    HeadApplicationMode,
    IntegrationStep,
    IntegrationStepKind,
    PrerequisiteAncestor,
    PrerequisiteResolution,
    ReviewedHead,
    ReviewedHeadIntegrationPlan,
    ReviewedHeadIntegrationReceipt,
    ReviewedHeadIntegrationReceiptError,
    build_reviewed_head_integration_plan,
    encode_reviewed_head_integration_receipt,
    load_reviewed_head_integration_receipt,
    parse_reviewed_head_integration_receipt,
    reviewed_head_integration_receipt_payload,
)

_BASE_SHA = "a" * 40
_HEAD_ONE_SHA = "b" * 40
_HEAD_TWO_SHA = "c" * 40
_AFTER_ONE_SHA = "d" * 40
_FINAL_SHA = "e" * 40
_ANCESTOR_ONE_SHA = "1" * 40
_ANCESTOR_TWO_SHA = "2" * 40
_DIFFERENT_REVIEW_BASE_SHA = "3" * 40
_ROOT = Path(__file__).resolve().parents[2]


def _plan_payload(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["plan"])


def _head_payload(payload: dict[str, object]) -> dict[str, object]:
    plan = _plan_payload(payload)
    return cast(list[dict[str, object]], plan["heads"])[0]


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


def test_plan_requires_every_unmerged_prerequisite_head_in_earlier_order() -> None:
    dependent = replace(
        _heads()[1],
        prerequisite_ancestry=(
            PrerequisiteAncestor(
                ancestor_sha=_HEAD_ONE_SHA,
                resolution=PrerequisiteResolution.REQUIRED_HEAD,
            ),
        ),
    )

    with pytest.raises(ValueError, match="required unmerged ancestor"):
        build_reviewed_head_integration_plan(
            base_sha=_BASE_SHA,
            heads=(dependent,),
            exact_gate_id="ci-gate-exact:3.11",
        )

    closed_plan = build_reviewed_head_integration_plan(
        base_sha=_BASE_SHA,
        heads=(_heads()[0], dependent),
        exact_gate_id="ci-gate-exact:3.11",
    )
    assert closed_plan.heads[-1].prerequisite_ancestry[0].ancestor_sha == (
        _HEAD_ONE_SHA
    )

    with pytest.raises(ValueError, match="required unmerged ancestor"):
        build_reviewed_head_integration_plan(
            base_sha=_BASE_SHA,
            heads=(dependent, _heads()[0]),
            exact_gate_id="ci-gate-exact:3.11",
        )


def test_plan_resolves_differing_review_bases_through_ancestry_closure() -> None:
    already_reachable = replace(
        _heads()[0],
        reviewed_base_sha=_DIFFERENT_REVIEW_BASE_SHA,
        prerequisite_ancestry=(
            PrerequisiteAncestor(
                ancestor_sha=_DIFFERENT_REVIEW_BASE_SHA,
                resolution=PrerequisiteResolution.ALREADY_REACHABLE,
            ),
        ),
    )
    dependent = replace(
        _heads()[1],
        reviewed_base_sha=_HEAD_ONE_SHA,
        prerequisite_ancestry=(
            PrerequisiteAncestor(
                ancestor_sha=_HEAD_ONE_SHA,
                resolution=PrerequisiteResolution.REQUIRED_HEAD,
            ),
        ),
    )

    plan = build_reviewed_head_integration_plan(
        base_sha=_BASE_SHA,
        heads=(already_reachable, dependent),
        exact_gate_id="ci-gate-exact:3.11",
    )

    assert plan.heads[0].reviewed_base_sha == _DIFFERENT_REVIEW_BASE_SHA
    assert plan.heads[1].reviewed_base_sha == _HEAD_ONE_SHA


def test_plan_rejects_a_differing_review_base_missing_from_closure() -> None:
    with pytest.raises(ValueError, match=r"reviewed base.*ancestry closure"):
        build_reviewed_head_integration_plan(
            base_sha=_BASE_SHA,
            heads=(
                replace(
                    _heads()[0],
                    reviewed_base_sha=_DIFFERENT_REVIEW_BASE_SHA,
                ),
            ),
            exact_gate_id="ci-gate-exact:3.11",
        )


@pytest.mark.parametrize(
    "resolution",
    (
        PrerequisiteResolution.ALREADY_REACHABLE,
        PrerequisiteResolution.PATCH_EQUIVALENT,
    ),
)
def test_plan_preserves_nonapplication_prerequisite_resolutions(
    resolution: PrerequisiteResolution,
) -> None:
    head = replace(
        _heads()[0],
        prerequisite_ancestry=(
            PrerequisiteAncestor(
                ancestor_sha=_ANCESTOR_ONE_SHA,
                resolution=resolution,
            ),
        ),
    )

    plan = build_reviewed_head_integration_plan(
        base_sha=_BASE_SHA,
        heads=(head,),
        exact_gate_id="ci-gate-exact:3.11",
    )

    assert plan.heads == (head,)


def test_prerequisite_ancestry_receipts_are_canonical_and_round_trip() -> None:
    ancestry = (
        PrerequisiteAncestor(
            ancestor_sha=_ANCESTOR_TWO_SHA,
            resolution=PrerequisiteResolution.PATCH_EQUIVALENT,
        ),
        PrerequisiteAncestor(
            ancestor_sha=_ANCESTOR_ONE_SHA,
            resolution=PrerequisiteResolution.ALREADY_REACHABLE,
        ),
        PrerequisiteAncestor(
            ancestor_sha=_DIFFERENT_REVIEW_BASE_SHA,
            resolution=PrerequisiteResolution.ALREADY_REACHABLE,
        ),
    )
    first = replace(
        _heads()[0],
        reviewed_base_sha=_DIFFERENT_REVIEW_BASE_SHA,
        prerequisite_ancestry=ancestry,
    )
    canonical_first = replace(
        _heads()[0],
        reviewed_base_sha=_DIFFERENT_REVIEW_BASE_SHA,
        prerequisite_ancestry=tuple(reversed(ancestry)),
    )
    plan = build_reviewed_head_integration_plan(
        base_sha=_BASE_SHA,
        heads=(first, _heads()[1]),
        exact_gate_id="ci-gate-exact:3.11",
    )
    canonical_plan = build_reviewed_head_integration_plan(
        base_sha=_BASE_SHA,
        heads=(canonical_first, _heads()[1]),
        exact_gate_id="ci-gate-exact:3.11",
    )
    receipt = replace(_receipt(), plan=plan)
    canonical_receipt = replace(_receipt(), plan=canonical_plan)

    encoded = encode_reviewed_head_integration_receipt(receipt)

    assert encoded == encode_reviewed_head_integration_receipt(canonical_receipt)
    assert json.loads(encoded)["plan"]["heads"][0]["prerequisite_ancestry"] == [
        {
            "ancestor_sha": _ANCESTOR_ONE_SHA,
            "resolution": "already_reachable",
        },
        {
            "ancestor_sha": _ANCESTOR_TWO_SHA,
            "resolution": "patch_equivalent",
        },
        {
            "ancestor_sha": _DIFFERENT_REVIEW_BASE_SHA,
            "resolution": "already_reachable",
        },
    ]
    assert (
        parse_reviewed_head_integration_receipt(
            json.loads(encoded),
            expected_final_sha=_FINAL_SHA,
        )
        == receipt
    )


def test_prerequisite_ancestry_rejects_duplicates_self_and_tampering() -> None:
    prerequisite = PrerequisiteAncestor(
        ancestor_sha=_ANCESTOR_ONE_SHA,
        resolution=PrerequisiteResolution.ALREADY_REACHABLE,
    )
    with pytest.raises(ValueError, match="distinct"):
        replace(
            _heads()[0],
            prerequisite_ancestry=(prerequisite, prerequisite),
        )
    with pytest.raises(ValueError, match="itself"):
        replace(
            _heads()[0],
            prerequisite_ancestry=(
                replace(prerequisite, ancestor_sha=_HEAD_ONE_SHA),
            ),
        )

    payload = reviewed_head_integration_receipt_payload(_receipt())
    _head_payload(payload)["prerequisite_ancestry"] = [
        {
            "ancestor_sha": _ANCESTOR_ONE_SHA,
            "resolution": "required_head",
        }
    ]
    with pytest.raises(
        ReviewedHeadIntegrationReceiptError,
        match="semantic validation failed",
    ):
        parse_reviewed_head_integration_receipt(
            payload,
            expected_final_sha=_FINAL_SHA,
        )

    payload = reviewed_head_integration_receipt_payload(_receipt())
    _head_payload(payload).pop("prerequisite_ancestry")
    with pytest.raises(
        ReviewedHeadIntegrationReceiptError,
        match="schema validation failed",
    ):
        parse_reviewed_head_integration_receipt(
            payload,
            expected_final_sha=_FINAL_SHA,
        )


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


@pytest.mark.parametrize(
    "command_id",
    (
        "gate-all-bypass",
        "gate-fullish",
        "ci-gate-exact",
        "ci-gate-exactish:3.11",
        "gate:",
    ),
)
def test_plan_rejects_full_gate_lookalike_ids(command_id: str) -> None:
    with pytest.raises(ValueError, match="full-gate"):
        build_reviewed_head_integration_plan(
            base_sha=_BASE_SHA,
            heads=_heads(),
            exact_gate_id=command_id,
        )


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


def test_receipt_rejects_chain_that_reuses_the_integration_base() -> None:
    receipt = _receipt()
    first, second = receipt.applied_heads

    with pytest.raises(ValueError, match="fresh integration SHA"):
        replace(
            receipt,
            applied_heads=(first, replace(second, after_sha=_BASE_SHA)),
            focused_validation=replace(receipt.focused_validation, tip_sha=_BASE_SHA),
            exact_gate=replace(receipt.exact_gate, tip_sha=_BASE_SHA),
        )


def test_receipt_json_schema_round_trip_is_typed_and_exact_sha_bound() -> None:
    receipt = _receipt()
    payload = reviewed_head_integration_receipt_payload(receipt)

    parsed = parse_reviewed_head_integration_receipt(
        payload,
        expected_final_sha=_FINAL_SHA,
    )

    assert payload["schema_version"] == 1
    assert parsed == receipt
    assert parsed.final_sha == _FINAL_SHA


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["focused_validation"].update(run_count=2),
            "schema validation failed",
        ),
        (
            lambda payload: payload["exact_gate"].update(passed=False),
            "schema validation failed",
        ),
        (
            lambda payload: payload["applied_heads"][0].update(
                review_receipt_sha256="3" * 64
            ),
            "semantic validation failed",
        ),
    ],
)
def test_receipt_parser_rejects_tampered_run_count_gate_and_provenance(
    mutate: object,
    message: str,
) -> None:
    payload = reviewed_head_integration_receipt_payload(_receipt())
    mutate(payload)  # type: ignore[operator]

    with pytest.raises(ReviewedHeadIntegrationReceiptError, match=message):
        parse_reviewed_head_integration_receipt(
            payload,
            expected_final_sha=_FINAL_SHA,
        )


def test_receipt_parser_errors_do_not_echo_untrusted_content() -> None:
    payload = reviewed_head_integration_receipt_payload(_receipt())
    secret = "untrusted-secret-material"  # pragma: allowlist secret
    payload[secret] = secret

    with pytest.raises(ReviewedHeadIntegrationReceiptError) as exc_info:
        parse_reviewed_head_integration_receipt(
            payload,
            expected_final_sha=_FINAL_SHA,
        )

    assert secret not in str(exc_info.value)


def test_receipt_parser_rejects_candidate_sha_or_reviewed_base_drift() -> None:
    payload = reviewed_head_integration_receipt_payload(_receipt())
    with pytest.raises(
        ReviewedHeadIntegrationReceiptError,
        match="final SHA does not match release candidate",
    ):
        parse_reviewed_head_integration_receipt(
            payload,
            expected_final_sha="f" * 40,
        )

    payload = reviewed_head_integration_receipt_payload(_receipt())
    _head_payload(payload)["reviewed_base_sha"] = "f" * 40
    with pytest.raises(
        ReviewedHeadIntegrationReceiptError,
        match="semantic validation failed",
    ):
        parse_reviewed_head_integration_receipt(
            payload,
            expected_final_sha=_FINAL_SHA,
        )

    payload = reviewed_head_integration_receipt_payload(_receipt())
    _plan_payload(payload)["focused_validation_ids"] = ["test-one"]
    with pytest.raises(
        ReviewedHeadIntegrationReceiptError,
        match="semantic validation failed",
    ):
        parse_reviewed_head_integration_receipt(
            payload,
            expected_final_sha=_FINAL_SHA,
        )


def test_receipt_loader_is_bounded_duplicate_safe_and_content_free(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(
        ReviewedHeadIntegrationReceiptError,
        match="missing or exceeds",
    ):
        load_reviewed_head_integration_receipt(
            missing,
            expected_final_sha=_FINAL_SHA,
        )

    artifact = tmp_path / "receipt.json"
    artifact.write_text("{", encoding="utf-8")
    with pytest.raises(ReviewedHeadIntegrationReceiptError, match="could not be decoded"):
        load_reviewed_head_integration_receipt(
            artifact,
            expected_final_sha=_FINAL_SHA,
        )

    artifact.write_bytes(b"{" + b" " * 1_048_576)
    with pytest.raises(
        ReviewedHeadIntegrationReceiptError,
        match="missing or exceeds",
    ):
        load_reviewed_head_integration_receipt(
            artifact,
            expected_final_sha=_FINAL_SHA,
        )

    artifact.write_bytes(b"\xff")
    with pytest.raises(ReviewedHeadIntegrationReceiptError) as exc_info:
        load_reviewed_head_integration_receipt(
            artifact,
            expected_final_sha=_FINAL_SHA,
        )
    assert "could not be decoded" in str(exc_info.value)

    secret = "duplicate-secret-key"  # pragma: allowlist secret
    artifact.write_text(
        '{"schema_version":1,"' + secret + '":1,"' + secret + '":2}',
        encoding="utf-8",
    )
    with pytest.raises(ReviewedHeadIntegrationReceiptError) as exc_info:
        load_reviewed_head_integration_receipt(
            artifact,
            expected_final_sha=_FINAL_SHA,
        )
    assert secret not in str(exc_info.value)

    encoded = encode_reviewed_head_integration_receipt(_receipt())
    assert json.loads(encoded)["schema_version"] == 1
    artifact.write_text(encoded, encoding="utf-8")
    assert (
        load_reviewed_head_integration_receipt(
            artifact,
            expected_final_sha=_FINAL_SHA,
        ).final_sha
        == _FINAL_SHA
    )


def test_receipt_loader_rejects_excessive_json_nesting_content_free(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "deeply-nested.json"
    artifact.write_text("[" * 2_000 + "0" + "]" * 2_000, encoding="utf-8")

    with pytest.raises(ReviewedHeadIntegrationReceiptError) as exc_info:
        load_reviewed_head_integration_receipt(
            artifact,
            expected_final_sha=_FINAL_SHA,
        )

    assert "receipt" in str(exc_info.value)
    assert "Recursion" not in str(exc_info.value)


def test_receipt_parser_rejects_malformed_expected_sha_without_echoing_it() -> None:
    malformed = "candidate-secret"
    with pytest.raises(ReviewedHeadIntegrationReceiptError) as exc_info:
        parse_reviewed_head_integration_receipt(
            reviewed_head_integration_receipt_payload(_receipt()),
            expected_final_sha=malformed,
        )
    assert "expected release candidate SHA is invalid" in str(exc_info.value)
    assert malformed not in str(exc_info.value)


def test_receipt_release_boundary_documents_generation_zdd_and_forum_evidence() -> None:
    documentation = (
        _ROOT / "docs" / "features" / "REVIEWED_HEAD_INTEGRATION.md"
    ).read_text(encoding="utf-8")

    for required in (
        "encode_reviewed_head_integration_receipt",
        "canonical JSON Schema",
        "REVIEWED_HEAD_INTEGRATION_RECEIPT",
        "Ordinary development `make gate`",
        "prerequisite ancestry closure",
        "`required_head`",
        "`already_reachable`",
        "`patch_equivalent`",
        "zero-downtime (ZDD)",
        "quarantine the stale",
        "stackoverflow.com/questions/14424414",
        "stackoverflow.com/questions/8963375",
        "github.com/orgs/community/discussions/43988",
    ):
        assert required in documentation
