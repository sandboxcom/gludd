"""Deterministic statistics and fail-closed decision for three-arm replay."""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum
from itertools import pairwise
from typing import NoReturn

import numpy as np

from general_ludd.models.freellmapi.three_arm_contracts import (
    ThreeArmCorpus,
    ThreeArmPlan,
)
from general_ludd.models.freellmapi.three_arm_types import (
    ArmComparison,
    ReplayObservation,
    ReplayResources,
    ThreeArmDecision,
)

_CANDIDATE_ARM = "freellmapi_v0_11_1"
_COMPARATOR_ARMS = ("gludd_native", "freellmapi_v0_9_9")
_STRATUM_DIMENSIONS = (
    "provider_family",
    "health_quota_state",
    "endpoint_state",
    "capability_class",
)


class ThreeArmDeltaFault(StrEnum):
    """Stable, content-free evaluation fault categories."""

    INPUT_INVALID = "input_invalid"
    STATISTIC_INVALID = "statistic_invalid"


class ThreeArmDeltaError(ValueError):
    """A typed replay-evaluation error without observation content."""

    def __init__(self, fault: ThreeArmDeltaFault) -> None:
        """Create an error exposing only its stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


def _fail(fault: ThreeArmDeltaFault) -> NoReturn:
    raise ThreeArmDeltaError(fault)


def _finite_number(value: object, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail(ThreeArmDeltaFault.INPUT_INVALID)
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        _fail(ThreeArmDeltaFault.INPUT_INVALID)
    return number


def paired_bootstrap_lcb(
    paired_differences: Sequence[float],
    *,
    confidence: float,
    samples: int,
    seed: int,
) -> float:
    """Return a deterministic one-sided paired-bootstrap lower bound."""
    values = tuple(paired_differences)
    if (
        not values
        or isinstance(samples, bool)
        or not isinstance(samples, int)
        or samples <= 0
        or isinstance(seed, bool)
        or not isinstance(seed, int)
        or not 0.0 < confidence < 1.0
    ):
        _fail(ThreeArmDeltaFault.STATISTIC_INVALID)
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError):
        _fail(ThreeArmDeltaFault.STATISTIC_INVALID)
    if array.ndim != 1 or not np.all(np.isfinite(array)):
        _fail(ThreeArmDeltaFault.STATISTIC_INVALID)
    if np.all(array == array[0]):
        return float(array[0])
    generator = np.random.default_rng(seed)
    indexes = generator.integers(0, len(array), size=(samples, len(array)))
    means = array[indexes].mean(axis=1)
    return float(np.quantile(means, 1.0 - confidence, method="linear"))


def exact_mcnemar_pvalue(candidate_only: int, comparator_only: int) -> float:
    """Return the exact two-sided McNemar p-value for discordant pairs."""
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (candidate_only, comparator_only)
    ):
        _fail(ThreeArmDeltaFault.INPUT_INVALID)
    discordant = candidate_only + comparator_only
    if discordant == 0:
        return 1.0
    tail_count = min(candidate_only, comparator_only)
    tail_probability = sum(
        math.comb(discordant, count) for count in range(tail_count + 1)
    ) / (2**discordant)
    return float(min(1.0, 2.0 * tail_probability))


def _validated_observations(
    plan: ThreeArmPlan,
    corpus: ThreeArmCorpus,
    observations: Sequence[ReplayObservation],
) -> tuple[ReplayObservation, ...]:
    values = tuple(observations)
    groups = {group.group_digest: group for group in corpus.groups}
    if len(values) != len(groups) or any(
        not isinstance(value, ReplayObservation) for value in values
    ):
        _fail(ThreeArmDeltaFault.INPUT_INVALID)
    observed_digests = [value.group_digest for value in values]
    if len(set(observed_digests)) != len(groups) or set(observed_digests) != set(groups):
        _fail(ThreeArmDeltaFault.INPUT_INVALID)
    expected_arms = set(plan.arms)
    for value in values:
        group = groups[value.group_digest]
        if (
            value.truth is not group.truth
            or set(value.probabilities) != expected_arms
            or set(value.latency_ms) != expected_arms
            or (
                value.bridge_fault is not None
                and (not isinstance(value.bridge_fault, str) or not value.bridge_fault)
            )
        ):
            _fail(ThreeArmDeltaFault.INPUT_INVALID)
        for probability in (*value.probabilities.values(), value.node_candidate_probability):
            if _finite_number(probability) > 1.0:
                _fail(ThreeArmDeltaFault.INPUT_INVALID)
        for latency in value.latency_ms.values():
            _finite_number(latency)
    by_digest = {value.group_digest: value for value in values}
    return tuple(by_digest[group.group_digest] for group in corpus.groups)


def _validated_resources(resources: ReplayResources) -> tuple[float, ...]:
    if (
        not isinstance(resources, ReplayResources)
        or len(resources.rss_samples_mib) < 2
        or isinstance(resources.network_calls, bool)
        or not isinstance(resources.network_calls, int)
        or resources.network_calls < 0
    ):
        _fail(ThreeArmDeltaFault.INPUT_INVALID)
    samples = tuple(_finite_number(value) for value in resources.rss_samples_mib)
    _finite_number(resources.cost_usd)
    return samples


def _quality(probability: float, truth: bool) -> float:
    return 1.0 - (probability - float(truth)) ** 2


def _stratum_loss(
    corpus: ThreeArmCorpus,
    observations: tuple[ReplayObservation, ...],
    comparator: str,
) -> float:
    groups = {group.group_digest: group for group in corpus.groups}
    losses: list[float] = []
    for dimension in _STRATUM_DIMENSIONS:
        strata = sorted({getattr(group, dimension) for group in corpus.groups})
        for stratum in strata:
            selected = [
                value
                for value in observations
                if getattr(groups[value.group_digest], dimension) == stratum
            ]
            if not selected:
                _fail(ThreeArmDeltaFault.STATISTIC_INVALID)
            candidate_quality = sum(
                _quality(value.probabilities[_CANDIDATE_ARM], value.truth)
                for value in selected
            ) / len(selected)
            comparator_quality = sum(
                _quality(value.probabilities[comparator], value.truth)
                for value in selected
            ) / len(selected)
            losses.append(max(0.0, comparator_quality - candidate_quality))
    return max(losses)


def _percentile95(values: Sequence[float]) -> float:
    return float(np.quantile(np.asarray(tuple(values)), 0.95, method="linear"))


def _comparison(
    plan: ThreeArmPlan,
    corpus: ThreeArmCorpus,
    observations: tuple[ReplayObservation, ...],
    comparator: str,
) -> ArmComparison:
    differences = tuple(
        _quality(value.probabilities[_CANDIDATE_ARM], value.truth)
        - _quality(value.probabilities[comparator], value.truth)
        for value in observations
    )
    candidate_correct = tuple(
        (value.probabilities[_CANDIDATE_ARM] >= 0.5) is value.truth
        for value in observations
    )
    comparator_correct = tuple(
        (value.probabilities[comparator] >= 0.5) is value.truth
        for value in observations
    )
    candidate_only = sum(
        candidate and not baseline
        for candidate, baseline in zip(
            candidate_correct, comparator_correct, strict=True
        )
    )
    comparator_only = sum(
        baseline and not candidate
        for candidate, baseline in zip(
            candidate_correct, comparator_correct, strict=True
        )
    )
    added_latency = tuple(
        max(
            0.0,
            value.latency_ms[_CANDIDATE_ARM] - value.latency_ms[comparator],
        )
        for value in observations
    )
    thresholds = plan.thresholds
    return ArmComparison(
        quality_delta_mean=sum(differences) / len(differences),
        quality_lcb=paired_bootstrap_lcb(
            differences,
            confidence=thresholds.bootstrap_confidence,
            samples=thresholds.bootstrap_samples,
            seed=thresholds.bootstrap_seed,
        ),
        mcnemar_pvalue=exact_mcnemar_pvalue(candidate_only, comparator_only),
        candidate_only_correct=candidate_only,
        comparator_only_correct=comparator_only,
        max_stratum_loss=_stratum_loss(corpus, observations, comparator),
        p95_added_latency_ms=_percentile95(added_latency),
    )


def evaluate_three_arm(
    plan: ThreeArmPlan,
    corpus: ThreeArmCorpus,
    observations: Sequence[ReplayObservation],
    resources: ReplayResources,
) -> ThreeArmDecision:
    """Evaluate every preregistered gate while never authorizing promotion."""
    values = _validated_observations(plan, corpus, observations)
    rss_samples = _validated_resources(resources)
    comparisons = {
        arm: _comparison(plan, corpus, values, arm) for arm in _COMPARATOR_ARMS
    }
    thresholds = plan.thresholds
    failed: list[str] = []
    for arm, result in comparisons.items():
        if result.quality_lcb < thresholds.quality_lcb_min:
            failed.append(f"quality_lcb:{arm}")
        if not (
            result.mcnemar_pvalue < thresholds.mcnemar_p_max
            and result.candidate_only_correct > result.comparator_only_correct
        ):
            failed.append(f"mcnemar:{arm}")
        if result.max_stratum_loss > thresholds.max_stratum_loss:
            failed.append(f"stratum_loss:{arm}")
        if result.p95_added_latency_ms > thresholds.p95_added_latency_ms:
            failed.append(f"p95_latency:{arm}")
    candidate_max_latency = max(
        value.latency_ms[_CANDIDATE_ARM] for value in values
    )
    if candidate_max_latency > thresholds.max_call_latency_ms:
        failed.append("max_latency")
    rss_delta = max(0.0, max(rss_samples) - rss_samples[0])
    if rss_delta > thresholds.rss_delta_mib:
        failed.append("rss_delta")
    if len(rss_samples) >= 4 and all(
        later > earlier for earlier, later in pairwise(rss_samples)
    ):
        failed.append("rss_monotonic_growth")
    if resources.network_calls != 0:
        failed.append("network")
    if resources.cost_usd > thresholds.max_cost_usd:
        failed.append("cost")
    bridge_fault_count = sum(value.bridge_fault is not None for value in values)
    if bridge_fault_count > thresholds.max_bridge_faults:
        failed.append("bridge_faults")
    if plan.node_crosscheck_required and any(
        abs(
            value.probabilities[_CANDIDATE_ARM]
            - value.node_candidate_probability
        )
        > thresholds.node_tolerance
        for value in values
    ):
        failed.append("node_crosscheck")
    all_gates_passed = not failed
    return ThreeArmDecision(
        decision=("HOLD_PENDING_SEPARATE_REVIEW" if all_gates_passed else "HOLD"),
        all_gates_passed=all_gates_passed,
        promotion_permitted=False,
        runtime_admitted=False,
        serving_bundle_sha256=plan.active_bundle_sha256,
        comparisons=comparisons,
        failed_gates=tuple(failed),
        candidate_max_latency_ms=candidate_max_latency,
        rss_delta_mib=rss_delta,
        bridge_fault_count=bridge_fault_count,
    )


__all__ = [
    "ArmComparison",
    "ReplayObservation",
    "ReplayResources",
    "ThreeArmDecision",
    "ThreeArmDeltaError",
    "ThreeArmDeltaFault",
    "evaluate_three_arm",
    "exact_mcnemar_pvalue",
    "paired_bootstrap_lcb",
]
