"""Decision-statistic tests for the non-promoting three-arm replay."""

from __future__ import annotations

from dataclasses import replace

import pytest

from general_ludd.models.freellmapi.three_arm_contracts import (
    build_frozen_documents,
    validate_three_arm_documents,
)
from general_ludd.models.freellmapi.three_arm_delta import (
    ReplayObservation,
    ReplayResources,
    ThreeArmDeltaError,
    ThreeArmDeltaFault,
    evaluate_three_arm,
    exact_mcnemar_pvalue,
    paired_bootstrap_lcb,
)

CANDIDATE_ID = "sha256:69d63b09199c37f38c02c711559b15e0fd5dc5ecc0e64e2d94c597dc5e5d3998"
CANDIDATE_BUNDLE = "e9e5d87a0d1e9697e0681b52afb719b7fd1fcdd601bd7fcbaf430cc502114845"
ADMITTED_BUNDLE = "d3078364c02f482909681e21895c4e86dc11cc66c1da7ae2007ad35b096ddf7d"


def _contracts():  # type: ignore[no-untyped-def]
    plan, corpus = build_frozen_documents(
        candidate_id=CANDIDATE_ID,
        candidate_bundle_sha256=CANDIDATE_BUNDLE,
        admitted_bundle_sha256=ADMITTED_BUNDLE,
    )
    return validate_three_arm_documents(plan, corpus)


def _observations(*, candidate_matches_admitted: bool) -> tuple[ReplayObservation, ...]:
    _, corpus = _contracts()
    observations = []
    for group in corpus.groups:
        candidate = 22.0 / 28.0 if group.truth else 6.0 / 28.0
        admitted = candidate if candidate_matches_admitted else (0.45 if group.truth else 0.55)
        native = 0.25 if group.truth else 0.75
        observations.append(
            ReplayObservation(
                group_digest=group.group_digest,
                truth=group.truth,
                probabilities={
                    "gludd_native": native,
                    "freellmapi_v0_9_9": admitted,
                    "freellmapi_v0_11_1": candidate,
                },
                latency_ms={
                    "gludd_native": 0.05,
                    "freellmapi_v0_9_9": 0.20,
                    "freellmapi_v0_11_1": 0.25,
                },
                node_candidate_probability=candidate,
                bridge_fault=None,
            )
        )
    return tuple(observations)


def test_exact_replay_tie_with_admitted_arm_forces_hold() -> None:
    plan, corpus = _contracts()
    decision = evaluate_three_arm(
        plan,
        corpus,
        _observations(candidate_matches_admitted=True),
        ReplayResources(
            rss_samples_mib=(100.0, 101.0, 101.5, 101.5),
            network_calls=0,
            cost_usd=0.0,
        ),
    )

    assert decision.decision == "HOLD"
    assert decision.promotion_permitted is False
    assert decision.runtime_admitted is False
    assert decision.serving_bundle_sha256 == ADMITTED_BUNDLE
    assert decision.all_gates_passed is False
    assert decision.comparisons["gludd_native"].quality_lcb > 0.02
    assert decision.comparisons["gludd_native"].mcnemar_pvalue < 0.05
    assert decision.comparisons["freellmapi_v0_9_9"].quality_lcb == pytest.approx(0.0)
    assert decision.comparisons["freellmapi_v0_9_9"].mcnemar_pvalue == pytest.approx(1.0)
    assert decision.failed_gates == (
        "quality_lcb:freellmapi_v0_9_9",
        "mcnemar:freellmapi_v0_9_9",
    )


def test_even_all_green_evidence_can_only_return_hold_pending_review() -> None:
    plan, corpus = _contracts()
    decision = evaluate_three_arm(
        plan,
        corpus,
        _observations(candidate_matches_admitted=False),
        ReplayResources(
            rss_samples_mib=(100.0, 100.5, 100.25, 100.75),
            network_calls=0,
            cost_usd=0.0,
        ),
    )

    assert decision.all_gates_passed is True
    assert decision.decision == "HOLD_PENDING_SEPARATE_REVIEW"
    assert decision.promotion_permitted is False
    assert decision.runtime_admitted is False
    assert decision.serving_bundle_sha256 == ADMITTED_BUNDLE


def test_resource_fault_or_node_drift_is_typed_and_holds() -> None:
    plan, corpus = _contracts()
    observations = list(_observations(candidate_matches_admitted=False))
    observations[0] = replace(observations[0], node_candidate_probability=0.0)
    decision = evaluate_three_arm(
        plan,
        corpus,
        tuple(observations),
        ReplayResources(
            rss_samples_mib=(100.0, 110.0, 120.0, 140.0),
            network_calls=1,
            cost_usd=0.01,
        ),
    )

    assert decision.decision == "HOLD"
    assert set(decision.failed_gates) >= {
        "node_crosscheck",
        "rss_delta",
        "rss_monotonic_growth",
        "network",
        "cost",
    }


def test_statistics_are_exact_deterministic_and_validate_inputs() -> None:
    assert exact_mcnemar_pvalue(0, 0) == 1.0
    assert exact_mcnemar_pvalue(32, 0) < 0.05
    first = paired_bootstrap_lcb((0.2, 0.3, 0.4), confidence=0.95, samples=1000, seed=17)
    second = paired_bootstrap_lcb((0.2, 0.3, 0.4), confidence=0.95, samples=1000, seed=17)
    assert first == second

    with pytest.raises(ThreeArmDeltaError) as caught:
        exact_mcnemar_pvalue(-1, 0)
    assert caught.value.fault is ThreeArmDeltaFault.INPUT_INVALID


def test_missing_duplicate_or_nonfinite_observations_fail_closed() -> None:
    plan, corpus = _contracts()
    observations = _observations(candidate_matches_admitted=True)
    resources = ReplayResources(
        rss_samples_mib=(100.0, 101.0),
        network_calls=0,
        cost_usd=0.0,
    )
    nonfinite = replace(
        observations[0],
        latency_ms={
            **observations[0].latency_ms,
            "freellmapi_v0_11_1": float("nan"),
        },
    )
    for invalid in (
        observations[:-1],
        (*observations[:-1], observations[0]),
        (nonfinite, *observations[1:]),
    ):
        with pytest.raises(ThreeArmDeltaError) as caught:
            evaluate_three_arm(plan, corpus, invalid, resources)
        assert caught.value.fault is ThreeArmDeltaFault.INPUT_INVALID


def test_quality_stratum_latency_and_bridge_failures_are_all_closed() -> None:
    plan, corpus = _contracts()
    observations = []
    for value in _observations(candidate_matches_admitted=False):
        observations.append(
            replace(
                value,
                probabilities={
                    **value.probabilities,
                    "freellmapi_v0_11_1": 0.0 if value.truth else 1.0,
                },
                latency_ms={
                    **value.latency_ms,
                    "freellmapi_v0_11_1": 30.0,
                },
                node_candidate_probability=0.0 if value.truth else 1.0,
                bridge_fault="engine_failure",
            )
        )
    decision = evaluate_three_arm(
        plan,
        corpus,
        tuple(observations),
        ReplayResources(
            rss_samples_mib=(100.0, 100.0),
            network_calls=0,
            cost_usd=0.0,
        ),
    )

    assert set(decision.failed_gates) >= {
        "stratum_loss:gludd_native",
        "stratum_loss:freellmapi_v0_9_9",
        "p95_latency:gludd_native",
        "p95_latency:freellmapi_v0_9_9",
        "max_latency",
        "bridge_faults",
    }


@pytest.mark.parametrize(
    "resources",
    [
        ReplayResources(rss_samples_mib=(100.0,), network_calls=0, cost_usd=0.0),
        ReplayResources(rss_samples_mib=(100.0, 100.0), network_calls=True, cost_usd=0.0),
        ReplayResources(rss_samples_mib=(100.0, float("inf")), network_calls=0, cost_usd=0.0),
    ],
)
def test_invalid_resource_shapes_are_typed(resources: ReplayResources) -> None:
    plan, corpus = _contracts()
    with pytest.raises(ThreeArmDeltaError) as caught:
        evaluate_three_arm(
            plan,
            corpus,
            _observations(candidate_matches_admitted=True),
            resources,
        )
    assert caught.value.fault is ThreeArmDeltaFault.INPUT_INVALID
