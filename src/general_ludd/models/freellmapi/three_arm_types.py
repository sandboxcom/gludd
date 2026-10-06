"""Passive value contracts for deterministic three-arm replay evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReplayObservation:
    """Content-free probabilities and timings for one frozen group."""

    group_digest: str
    truth: bool
    probabilities: Mapping[str, float]
    latency_ms: Mapping[str, float]
    node_candidate_probability: float
    bridge_fault: str | None


@dataclass(frozen=True, slots=True)
class ReplayResources:
    """Process-level resource and authority observations."""

    rss_samples_mib: tuple[float, ...]
    network_calls: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class ArmComparison:
    """Candidate evidence relative to one comparator arm."""

    quality_delta_mean: float
    quality_lcb: float
    mcnemar_pvalue: float
    candidate_only_correct: int
    comparator_only_correct: int
    max_stratum_loss: float
    p95_added_latency_ms: float


@dataclass(frozen=True, slots=True)
class ThreeArmDecision:
    """Non-promoting evaluation result and exact failed gates."""

    decision: str
    all_gates_passed: bool
    promotion_permitted: bool
    runtime_admitted: bool
    serving_bundle_sha256: str
    comparisons: Mapping[str, ArmComparison]
    failed_gates: tuple[str, ...]
    candidate_max_latency_ms: float
    rss_delta_mib: float
    bridge_fault_count: int

