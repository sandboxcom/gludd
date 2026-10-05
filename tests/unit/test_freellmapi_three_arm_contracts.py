"""Frozen-contract tests for the FreeLLMAPI exact three-arm replay."""

from __future__ import annotations

import copy

import pytest

import general_ludd.models.freellmapi.three_arm_contracts as contracts
from general_ludd.models.freellmapi.three_arm_contracts import (
    EXPECTED_EXCLUSION_COUNT,
    EXPECTED_GROUP_COUNT,
    THREE_ARM_GATE,
    ThreeArmContractError,
    ThreeArmContractFault,
    build_frozen_documents,
    canonical_digest,
    validate_three_arm_documents,
)

CANDIDATE_ID = "sha256:69d63b09199c37f38c02c711559b15e0fd5dc5ecc0e64e2d94c597dc5e5d3998"
CANDIDATE_BUNDLE = "e9e5d87a0d1e9697e0681b52afb719b7fd1fcdd601bd7fcbaf430cc502114845"
ADMITTED_BUNDLE = "d3078364c02f482909681e21895c4e86dc11cc66c1da7ae2007ad35b096ddf7d"


def _documents() -> tuple[dict[str, object], dict[str, object]]:
    return build_frozen_documents(
        candidate_id=CANDIDATE_ID,
        candidate_bundle_sha256=CANDIDATE_BUNDLE,
        admitted_bundle_sha256=ADMITTED_BUNDLE,
    )


def test_frozen_documents_are_deterministic_complete_and_content_free() -> None:
    first_plan, first_corpus = _documents()
    second_plan, second_corpus = _documents()

    assert first_plan == second_plan
    assert first_corpus == second_corpus
    plan, corpus = validate_three_arm_documents(first_plan, first_corpus)

    assert plan.gate == THREE_ARM_GATE
    assert plan.decision == "hold_only"
    assert plan.runtime_admitted is False
    assert plan.active_bundle_sha256 == ADMITTED_BUNDLE
    assert plan.candidate_bundle_sha256 == CANDIDATE_BUNDLE
    assert len(corpus.groups) == EXPECTED_GROUP_COUNT == 32
    assert len(corpus.exclusions) == EXPECTED_EXCLUSION_COUNT == 8
    assert plan.corpus_sha256 == canonical_digest(first_corpus)
    assert set(plan.arms) == {
        "gludd_native",
        "freellmapi_v0_9_9",
        "freellmapi_v0_11_1",
    }
    combinations = {
        (
            group.provider_family,
            group.health_quota_state,
            group.endpoint_state,
            group.capability_class,
        )
        for group in corpus.groups
    }
    assert len(combinations) == 32
    serialized = repr(first_corpus).lower()
    for forbidden in ("prompt", "response", "model_name", "api_key", "provider_id"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("document", "mutation", "fault"),
    [
        ("plan", lambda value: value.__setitem__("decision", "promote"), ThreeArmContractFault.PLAN_INVALID),
        ("plan", lambda value: value.__setitem__("network_allowed", True), ThreeArmContractFault.PLAN_INVALID),
        (
            "plan",
            lambda value: value.__setitem__("active_bundle_sha256", CANDIDATE_BUNDLE),
            ThreeArmContractFault.PLAN_INVALID,
        ),
        ("plan", lambda value: value.__setitem__("unexpected", 1), ThreeArmContractFault.PLAN_INVALID),
        ("corpus", lambda value: value.__setitem__("unexpected", 1), ThreeArmContractFault.CORPUS_INVALID),
        ("corpus", lambda value: value["groups"].pop(), ThreeArmContractFault.CORPUS_INVALID),
        ("corpus", lambda value: value["exclusions"].pop(), ThreeArmContractFault.CORPUS_INVALID),
        (
            "corpus",
            lambda value: value["groups"][0].__setitem__("prompt", "secret"),
            ThreeArmContractFault.CORPUS_INVALID,
        ),
    ],
)
def test_contract_drift_is_rejected_with_typed_content_free_faults(
    document: str,
    mutation: object,
    fault: ThreeArmContractFault,
) -> None:
    plan, corpus = _documents()
    selected = plan if document == "plan" else corpus
    mutation(selected)  # type: ignore[operator]

    with pytest.raises(ThreeArmContractError) as caught:
        validate_three_arm_documents(plan, corpus)

    assert caught.value.fault is fault
    assert str(caught.value) == fault.value


def test_corpus_digest_and_per_record_digests_bind_every_frozen_value() -> None:
    plan, corpus = _documents()
    changed = copy.deepcopy(corpus)
    groups = changed["groups"]
    assert isinstance(groups, list)
    record = groups[0]
    assert isinstance(record, dict)
    record["successes"] = 999

    with pytest.raises(ThreeArmContractError) as caught:
        validate_three_arm_documents(plan, changed)

    assert caught.value.fault is ThreeArmContractFault.CORPUS_INVALID


def test_duplicate_cartesian_cell_fails_closed_even_with_resealed_digests() -> None:
    plan, corpus = _documents()
    groups = corpus["groups"]
    assert isinstance(groups, list)
    groups[1] = copy.deepcopy(groups[0])
    corpus["corpus_id"] = f"sha256:{canonical_digest({'groups': groups, 'exclusions': corpus['exclusions']})}"
    plan["corpus_sha256"] = canonical_digest(corpus)

    with pytest.raises(ThreeArmContractError) as caught:
        validate_three_arm_documents(plan, corpus)

    assert caught.value.fault is ThreeArmContractFault.CORPUS_INVALID


def _reseal_record(record: dict[str, object], field: str) -> None:
    unsigned = dict(record)
    unsigned.pop(field, None)
    record[field] = canonical_digest(unsigned)


def _reseal_corpus(corpus: dict[str, object]) -> None:
    unsigned = dict(corpus)
    unsigned.pop("corpus_id", None)
    corpus["corpus_id"] = f"sha256:{canonical_digest(unsigned)}"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.pop("truth"),
        lambda record: record.__setitem__("group_digest", "0" * 64),
        lambda record: (record.__setitem__("truth", "yes"), _reseal_record(record, "group_digest")),
        lambda record: (
            record.__setitem__("provider_family", "secret-provider"),
            _reseal_record(record, "group_digest"),
        ),
    ],
)
def test_group_schema_numeric_and_stratum_faults_are_typed(mutation: object) -> None:
    _, corpus = _documents()
    groups = corpus["groups"]
    assert isinstance(groups, list)
    record = copy.deepcopy(groups[0])
    assert isinstance(record, dict)
    mutation(record)  # type: ignore[operator]

    with pytest.raises(ThreeArmContractError) as caught:
        contracts._validate_group(record)
    assert caught.value.fault is ThreeArmContractFault.CORPUS_INVALID


def test_nonmapping_and_invalid_exclusion_values_fail_closed() -> None:
    with pytest.raises(ThreeArmContractError) as nonmapping:
        contracts._validate_group(None)
    assert nonmapping.value.fault is ThreeArmContractFault.CORPUS_INVALID

    _, corpus = _documents()
    exclusions = corpus["exclusions"]
    assert isinstance(exclusions, list)
    exclusion = copy.deepcopy(exclusions[0])
    assert isinstance(exclusion, dict)
    exclusion["reason"] = "unregistered"
    _reseal_record(exclusion, "exclusion_digest")
    with pytest.raises(ThreeArmContractError) as invalid:
        contracts._validate_exclusion(exclusion)
    assert invalid.value.fault is ThreeArmContractFault.CORPUS_INVALID


def test_resealed_duplicate_identity_truth_imbalance_and_reason_order_fail() -> None:
    for mutation in ("identity", "truth", "reason_order"):
        _, corpus = _documents()
        groups = corpus["groups"]
        exclusions = corpus["exclusions"]
        assert isinstance(groups, list)
        assert isinstance(exclusions, list)
        if mutation == "identity":
            first = groups[0]
            second = groups[1]
            assert isinstance(first, dict)
            assert isinstance(second, dict)
            second["candidate_identity_digest"] = first["candidate_identity_digest"]
            _reseal_record(second, "group_digest")
        elif mutation == "truth":
            first = groups[0]
            assert isinstance(first, dict)
            first["truth"] = not first["truth"]
            _reseal_record(first, "group_digest")
        else:
            exclusions[0], exclusions[1] = exclusions[1], exclusions[0]
        _reseal_corpus(corpus)
        with pytest.raises(ThreeArmContractError) as caught:
            contracts._validate_corpus(corpus)
        assert caught.value.fault is ThreeArmContractFault.CORPUS_INVALID


def test_resealed_plan_threshold_and_scalar_drift_reach_inner_guards() -> None:
    for field, value in (
        ("active_bundle_sha256", CANDIDATE_BUNDLE),
        ("thresholds", {}),
    ):
        plan, corpus = _documents()
        plan[field] = value
        unsigned = dict(plan)
        unsigned.pop("plan_id")
        plan["plan_id"] = f"sha256:{canonical_digest(unsigned)}"
        with pytest.raises(ThreeArmContractError) as caught:
            validate_three_arm_documents(plan, corpus)
        assert caught.value.fault is ThreeArmContractFault.PLAN_INVALID


@pytest.mark.parametrize("value", [True, -1, float("inf")])
def test_invalid_numeric_primitives_are_rejected(value: object) -> None:
    with pytest.raises(ThreeArmContractError) as caught:
        contracts._number(value, ThreeArmContractFault.CORPUS_INVALID)
    assert caught.value.fault is ThreeArmContractFault.CORPUS_INVALID
