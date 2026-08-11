"""Unit tests for AIML Phase E — promotion gate (promotion.py).

Covers spec 12 acceptance criteria:
  - CanaryBudgets / CanaryMetrics validation and field bounds
  - CanaryVerdict healthy/unhealthy + invariant (healthy cannot carry breaches)
  - PromotionGate.canary_check — all 5 budget dimensions
  - AliasSwap atomic swap + in-flight drain
  - PromotionGate.rollback — AIML-AT-005 (60s SLO, prior-version retention)
  - PromotionGate.enforce_retention — spec 12 step 8 (≥2 prior versions)
  - Edge cases: empty prior_versions, zero budgets, boundary values
"""

from __future__ import annotations

import dataclasses

import pytest

from general_ludd.ai_ml.promotion import (
    ROLLBACK_SLO_SECONDS,
    AliasSwap,
    CanaryBudgets,
    CanaryMetrics,
    CanaryVerdict,
    PromotionGate,
    PromotionPhase,
    RollbackResult,
)

# ---------------------------------------------------------------------------
# PromotionPhase enum
# ---------------------------------------------------------------------------


class TestPromotionPhase:
    def test_phases_match_spec_order(self) -> None:
        phases = list(PromotionPhase)
        assert phases == [
            PromotionPhase.BUILD,
            PromotionPhase.VALIDATE,
            PromotionPhase.SHADOW,
            PromotionPhase.CANARY,
            PromotionPhase.COMPARE,
            PromotionPhase.SWAP,
        ]

    def test_phase_values_are_strings(self) -> None:
        for phase in PromotionPhase:
            assert isinstance(phase.value, str)
            assert phase.value

    def test_rollback_slo_is_60(self) -> None:
        assert ROLLBACK_SLO_SECONDS == 60


# ---------------------------------------------------------------------------
# CanaryBudgets
# ---------------------------------------------------------------------------


class TestCanaryBudgets:
    @pytest.fixture
    def valid_budgets(self) -> CanaryBudgets:
        return CanaryBudgets(
            quality_floor=0.8,
            safety_floor=0.9,
            latency_p99_ceiling_ms=200.0,
            error_rate_ceiling=0.05,
            cost_ceiling_usd_per_kreq=0.01,
        )

    def test_valid_budgets_construct(self, valid_budgets: CanaryBudgets) -> None:
        assert valid_budgets.quality_floor == 0.8
        assert valid_budgets.safety_floor == 0.9
        assert valid_budgets.latency_p99_ceiling_ms == 200.0
        assert valid_budgets.error_rate_ceiling == 0.05
        assert valid_budgets.cost_ceiling_usd_per_kreq == 0.01

    def test_budgets_are_frozen(self, valid_budgets: CanaryBudgets) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            valid_budgets.quality_floor = 0.5  # type: ignore[misc]

    def test_negative_quality_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="quality_floor"):
            CanaryBudgets(
                quality_floor=-0.1,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            )

    def test_negative_safety_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="safety_floor"):
            CanaryBudgets(
                quality_floor=0.8,
                safety_floor=-0.1,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            )

    def test_negative_latency_ceiling_rejected(self) -> None:
        with pytest.raises(ValueError, match="latency_p99_ceiling_ms"):
            CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=-1.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            )

    def test_negative_cost_ceiling_rejected(self) -> None:
        with pytest.raises(ValueError, match="cost_ceiling_usd_per_kreq"):
            CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=-0.01,
            )

    def test_error_rate_ceiling_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="error_rate_ceiling"):
            CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=1.5,
                cost_ceiling_usd_per_kreq=0.01,
            )

    def test_error_rate_ceiling_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="error_rate_ceiling"):
            CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=-0.01,
                cost_ceiling_usd_per_kreq=0.01,
            )

    def test_bool_as_number_rejected_quality(self) -> None:
        with pytest.raises(ValueError, match="quality_floor"):
            CanaryBudgets(
                quality_floor=True,  # type: ignore[arg-type]
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            )

    def test_zero_values_are_valid(self) -> None:
        budgets = CanaryBudgets(
            quality_floor=0.0,
            safety_floor=0.0,
            latency_p99_ceiling_ms=0.0,
            error_rate_ceiling=0.0,
            cost_ceiling_usd_per_kreq=0.0,
        )
        assert budgets.quality_floor == 0.0

    def test_large_values_construct(self) -> None:
        budgets = CanaryBudgets(
            quality_floor=1e9,
            safety_floor=1e9,
            latency_p99_ceiling_ms=1e9,
            error_rate_ceiling=1.0,
            cost_ceiling_usd_per_kreq=1e9,
        )
        assert budgets.quality_floor == 1e9

    def test_int_fields_accepted(self) -> None:
        budgets = CanaryBudgets(
            quality_floor=1,
            safety_floor=1,
            latency_p99_ceiling_ms=100,
            error_rate_ceiling=0,
            cost_ceiling_usd_per_kreq=0,
        )
        assert budgets.quality_floor == 1


# ---------------------------------------------------------------------------
# CanaryMetrics
# ---------------------------------------------------------------------------


class TestCanaryMetrics:
    def test_valid_metrics_construct(self) -> None:
        metrics = CanaryMetrics(
            quality=0.85,
            safety=0.95,
            latency_p99_ms=150.0,
            error_rate=0.02,
            cost_usd_per_kreq=0.005,
        )
        assert metrics.quality == 0.85
        assert metrics.error_rate == 0.02

    def test_metrics_are_frozen(self) -> None:
        metrics = CanaryMetrics(
            quality=0.85,
            safety=0.95,
            latency_p99_ms=150.0,
            error_rate=0.02,
            cost_usd_per_kreq=0.005,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            metrics.quality = 0.5  # type: ignore[misc]

    def test_negative_quality_rejected(self) -> None:
        with pytest.raises(ValueError, match="quality"):
            CanaryMetrics(
                quality=-0.1,
                safety=0.95,
                latency_p99_ms=150.0,
                error_rate=0.02,
                cost_usd_per_kreq=0.005,
            )

    def test_negative_safety_rejected(self) -> None:
        with pytest.raises(ValueError, match="safety"):
            CanaryMetrics(
                quality=0.85,
                safety=-0.1,
                latency_p99_ms=150.0,
                error_rate=0.02,
                cost_usd_per_kreq=0.005,
            )

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValueError, match="latency_p99_ms"):
            CanaryMetrics(
                quality=0.85,
                safety=0.95,
                latency_p99_ms=-1.0,
                error_rate=0.02,
                cost_usd_per_kreq=0.005,
            )

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValueError, match="cost_usd_per_kreq"):
            CanaryMetrics(
                quality=0.85,
                safety=0.95,
                latency_p99_ms=150.0,
                error_rate=0.02,
                cost_usd_per_kreq=-0.001,
            )

    def test_error_rate_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="error_rate"):
            CanaryMetrics(
                quality=0.85,
                safety=0.95,
                latency_p99_ms=150.0,
                error_rate=1.5,
                cost_usd_per_kreq=0.005,
            )

    def test_negative_error_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="error_rate"):
            CanaryMetrics(
                quality=0.85,
                safety=0.95,
                latency_p99_ms=150.0,
                error_rate=-0.01,
                cost_usd_per_kreq=0.005,
            )

    def test_bool_rejected(self) -> None:
        with pytest.raises(ValueError):
            CanaryMetrics(
                quality=True,  # type: ignore[arg-type]
                safety=0.95,
                latency_p99_ms=150.0,
                error_rate=0.02,
                cost_usd_per_kreq=0.005,
            )

    def test_zero_values_are_valid(self) -> None:
        metrics = CanaryMetrics(
            quality=0.0,
            safety=0.0,
            latency_p99_ms=0.0,
            error_rate=0.0,
            cost_usd_per_kreq=0.0,
        )
        assert metrics.quality == 0.0


# ---------------------------------------------------------------------------
# CanaryVerdict
# ---------------------------------------------------------------------------


class TestCanaryVerdict:
    def test_healthy_verdict_no_breaches(self) -> None:
        verdict = CanaryVerdict(healthy=True)
        assert verdict.healthy is True
        assert verdict.breached_budgets == ()

    def test_unhealthy_verdict_with_breaches(self) -> None:
        verdict = CanaryVerdict(healthy=False, breached_budgets=("quality", "latency"))
        assert verdict.healthy is False
        assert set(verdict.breached_budgets) == {"quality", "latency"}

    def test_healthy_with_breaches_rejected(self) -> None:
        with pytest.raises(ValueError, match="healthy verdict must not carry breached_budgets"):
            CanaryVerdict(healthy=True, breached_budgets=("quality",))

    def test_healthy_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="healthy must be a bool"):
            CanaryVerdict(healthy="yes")  # type: ignore[arg-type]

    def test_empty_breach_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="breached_budgets"):
            CanaryVerdict(healthy=False, breached_budgets=("quality", ""))

    def test_verdict_is_frozen(self) -> None:
        verdict = CanaryVerdict(healthy=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            verdict.healthy = False  # type: ignore[misc]

    def test_single_breach_healthy_false(self) -> None:
        verdict = CanaryVerdict(healthy=False, breached_budgets=("cost",))
        assert not verdict.healthy
        assert verdict.breached_budgets == ("cost",)

    def test_all_five_breaches(self) -> None:
        all_breaches = ("quality", "safety", "latency", "error_rate", "cost")
        verdict = CanaryVerdict(healthy=False, breached_budgets=all_breaches)
        assert set(verdict.breached_budgets) == set(all_breaches)


# ---------------------------------------------------------------------------
# AliasSwap
# ---------------------------------------------------------------------------


class TestAliasSwap:
    def test_valid_swap_construct(self) -> None:
        swap = AliasSwap(
            alias="production",
            from_version="v1.0.0",
            to_version="v2.0.0",
        )
        assert swap.alias == "production"
        assert swap.from_version == "v1.0.0"
        assert swap.to_version == "v2.0.0"
        assert swap.in_flight_requests == 0
        assert swap.drained is False

    def test_swap_with_in_flight_requests(self) -> None:
        swap = AliasSwap(
            alias="production",
            from_version="v1.0.0",
            to_version="v2.0.0",
            in_flight_requests=5,
        )
        assert swap.in_flight_requests == 5
        assert swap.drained is False

    def test_swap_is_frozen(self) -> None:
        swap = AliasSwap(alias="p", from_version="a", to_version="b")
        with pytest.raises(dataclasses.FrozenInstanceError):
            swap.alias = "other"  # type: ignore[misc]

    def test_empty_alias_rejected(self) -> None:
        with pytest.raises(ValueError, match="alias"):
            AliasSwap(alias="", from_version="a", to_version="b")

    def test_empty_from_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="from_version"):
            AliasSwap(alias="p", from_version="", to_version="b")

    def test_empty_to_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="to_version"):
            AliasSwap(alias="p", from_version="a", to_version="")

    def test_negative_in_flight_rejected(self) -> None:
        with pytest.raises(ValueError, match="in_flight_requests"):
            AliasSwap(alias="p", from_version="a", to_version="b", in_flight_requests=-1)

    def test_drained_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="drained"):
            AliasSwap(alias="p", from_version="a", to_version="b", drained="yes")  # type: ignore[arg-type]

    def test_drained_with_nonzero_in_flight_rejected(self) -> None:
        with pytest.raises(ValueError, match="drained swap must have zero in_flight_requests"):
            AliasSwap(
                alias="p",
                from_version="a",
                to_version="b",
                in_flight_requests=3,
                drained=True,
            )

    def test_drained_with_zero_in_flight_ok(self) -> None:
        swap = AliasSwap(
            alias="p",
            from_version="a",
            to_version="b",
            in_flight_requests=0,
            drained=True,
        )
        assert swap.drained is True
        assert swap.in_flight_requests == 0


# ---------------------------------------------------------------------------
# PromotionGate — canary_check
# ---------------------------------------------------------------------------


class TestPromotionGateCanaryCheck:
    @pytest.fixture
    def gate(self) -> PromotionGate:
        return PromotionGate(
            budgets=CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            ),
            current_version="v1.0.0",
        )

    @pytest.fixture
    def healthy_metrics(self) -> CanaryMetrics:
        return CanaryMetrics(
            quality=0.85,
            safety=0.95,
            latency_p99_ms=150.0,
            error_rate=0.02,
            cost_usd_per_kreq=0.005,
        )

    def test_all_healthy_returns_healthy_verdict(self, gate: PromotionGate, healthy_metrics: CanaryMetrics) -> None:
        verdict = gate.canary_check(healthy_metrics)
        assert verdict.healthy is True
        assert verdict.breached_budgets == ()

    def test_quality_below_floor_breaches(self, gate: PromotionGate) -> None:
        metrics = CanaryMetrics(
            quality=0.7,
            safety=0.95,
            latency_p99_ms=150.0,
            error_rate=0.02,
            cost_usd_per_kreq=0.005,
        )
        verdict = gate.canary_check(metrics)
        assert not verdict.healthy
        assert "quality" in verdict.breached_budgets

    def test_safety_below_floor_breaches(self, gate: PromotionGate) -> None:
        metrics = CanaryMetrics(
            quality=0.85,
            safety=0.85,
            latency_p99_ms=150.0,
            error_rate=0.02,
            cost_usd_per_kreq=0.005,
        )
        verdict = gate.canary_check(metrics)
        assert not verdict.healthy
        assert "safety" in verdict.breached_budgets

    def test_latency_above_ceiling_breaches(self, gate: PromotionGate) -> None:
        metrics = CanaryMetrics(
            quality=0.85,
            safety=0.95,
            latency_p99_ms=250.0,
            error_rate=0.02,
            cost_usd_per_kreq=0.005,
        )
        verdict = gate.canary_check(metrics)
        assert not verdict.healthy
        assert "latency" in verdict.breached_budgets

    def test_error_rate_above_ceiling_breaches(self, gate: PromotionGate) -> None:
        metrics = CanaryMetrics(
            quality=0.85,
            safety=0.95,
            latency_p99_ms=150.0,
            error_rate=0.10,
            cost_usd_per_kreq=0.005,
        )
        verdict = gate.canary_check(metrics)
        assert not verdict.healthy
        assert "error_rate" in verdict.breached_budgets

    def test_cost_above_ceiling_breaches(self, gate: PromotionGate) -> None:
        metrics = CanaryMetrics(
            quality=0.85,
            safety=0.95,
            latency_p99_ms=150.0,
            error_rate=0.02,
            cost_usd_per_kreq=0.02,
        )
        verdict = gate.canary_check(metrics)
        assert not verdict.healthy
        assert "cost" in verdict.breached_budgets

    def test_multiple_breaches(self, gate: PromotionGate) -> None:
        metrics = CanaryMetrics(
            quality=0.5,
            safety=0.5,
            latency_p99_ms=300.0,
            error_rate=0.20,
            cost_usd_per_kreq=0.05,
        )
        verdict = gate.canary_check(metrics)
        assert not verdict.healthy
        assert len(verdict.breached_budgets) == 5

    def test_boundary_exactly_at_floor(self, gate: PromotionGate) -> None:
        metrics = CanaryMetrics(
            quality=0.8,
            safety=0.9,
            latency_p99_ms=200.0,
            error_rate=0.05,
            cost_usd_per_kreq=0.01,
        )
        verdict = gate.canary_check(metrics)
        assert verdict.healthy is True

    def test_non_metrics_instance_rejected(self, gate: PromotionGate) -> None:
        with pytest.raises(ValueError, match="metrics must be a CanaryMetrics"):
            gate.canary_check({"quality": 0.9})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PromotionGate — alias_swap + drain_in_flight
# ---------------------------------------------------------------------------


class TestPromotionGateAliasSwap:
    @pytest.fixture
    def gate(self) -> PromotionGate:
        return PromotionGate(
            budgets=CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            ),
            current_version="v1.0.0",
        )

    def test_alias_swap_updates_resolve_alias(self, gate: PromotionGate) -> None:
        assert gate.resolve_alias("production") == "v1.0.0"
        gate.alias_swap(alias="production", to_version="v2.0.0")
        assert gate.resolve_alias("production") == "v2.0.0"

    def test_alias_swap_returns_correct_swap(self, gate: PromotionGate) -> None:
        swap = gate.alias_swap(alias="production", to_version="v2.0.0", in_flight_requests=3)
        assert swap.alias == "production"
        assert swap.from_version == "v1.0.0"
        assert swap.to_version == "v2.0.0"
        assert swap.in_flight_requests == 3
        assert swap.drained is False

    def test_alias_swap_updates_current_version(self, gate: PromotionGate) -> None:
        gate.alias_swap(alias="production", to_version="v2.0.0")
        assert gate.current_version == "v2.0.0"

    def test_alias_swap_appends_prior_version(self, gate: PromotionGate) -> None:
        assert gate.prior_versions == ()
        gate.alias_swap(alias="production", to_version="v2.0.0")
        assert gate.prior_versions[0] == "v1.0.0"
        gate.alias_swap(alias="production", to_version="v3.0.0")
        assert gate.prior_versions == ("v2.0.0", "v1.0.0")

    def test_alias_swap_custom_alias(self, gate: PromotionGate) -> None:
        gate.alias_swap(alias="canary", to_version="v2.0.0")
        assert gate.resolve_alias("canary") == "v2.0.0"
        assert gate.resolve_alias("production") == "v1.0.0"  # unchanged

    def test_alias_swap_empty_alias_rejected(self, gate: PromotionGate) -> None:
        with pytest.raises(ValueError, match="alias"):
            gate.alias_swap(alias="", to_version="v2.0.0")

    def test_alias_swap_empty_to_version_rejected(self, gate: PromotionGate) -> None:
        with pytest.raises(ValueError, match="to_version"):
            gate.alias_swap(alias="production", to_version="")

    def test_alias_swap_negative_in_flight_rejected(self, gate: PromotionGate) -> None:
        with pytest.raises(ValueError, match="in_flight_requests"):
            gate.alias_swap(alias="production", to_version="v2.0.0", in_flight_requests=-5)

    def test_alias_swap_updates_new_alias_from_version_present(self, gate: PromotionGate) -> None:
        gate.alias_swap(alias="staging", to_version="v2.0.0")
        assert gate.resolve_alias("staging") == "v2.0.0"

    def test_drain_in_flight_sets_zero_and_drained(self, gate: PromotionGate) -> None:
        swap = gate.alias_swap(alias="production", to_version="v2.0.0", in_flight_requests=10)
        drained = gate.drain_in_flight(swap)
        assert drained.in_flight_requests == 0
        assert drained.drained is True
        assert drained.alias == "production"
        assert drained.to_version == "v2.0.0"

    def test_drain_in_flight_non_swap_rejected(self, gate: PromotionGate) -> None:
        with pytest.raises(ValueError, match="swap must be an AliasSwap"):
            gate.drain_in_flight("not_a_swap")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PromotionGate — rollback (AIML-AT-005)
# ---------------------------------------------------------------------------


class TestPromotionGateRollback:
    @pytest.fixture
    def gate_with_priors(self) -> PromotionGate:
        return PromotionGate(
            budgets=CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            ),
            current_version="v3.0.0",
            prior_versions=("v2.0.0", "v1.0.0"),
        )

    def test_rollback_swaps_to_immediate_prior(self, gate_with_priors: PromotionGate) -> None:
        result = gate_with_priors.rollback(breach_time_s=10.0)
        assert result.swapped_back_to == "v2.0.0"
        assert gate_with_priors.current_version == "v2.0.0"
        assert gate_with_priors.resolve_alias("production") == "v2.0.0"

    def test_rollback_within_slo_flagged_so(self, gate_with_priors: PromotionGate) -> None:
        result = gate_with_priors.rollback(breach_time_s=30.0)
        assert result.initiated_within_60s is True
        assert result.seconds_to_initiate == 30.0

    def test_rollback_beyond_slo_flagged_missed(self, gate_with_priors: PromotionGate) -> None:
        result = gate_with_priors.rollback(breach_time_s=90.0)
        assert result.initiated_within_60s is False
        assert result.seconds_to_initiate == 90.0

    def test_rollback_exactly_at_slo_is_within(self, gate_with_priors: PromotionGate) -> None:
        result = gate_with_priors.rollback(breach_time_s=60.0)
        assert result.initiated_within_60s is True

    def test_rollback_custom_rollback_window(self) -> None:
        gate = PromotionGate(
            budgets=CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            ),
            current_version="v2.0.0",
            prior_versions=("v1.0.0", "v0.9.0"),
            rollback_window_s=30,
        )
        result = gate.rollback(breach_time_s=25.0)
        assert result.initiated_within_60s is True
        assert result.swapped_back_to == "v1.0.0"

    def test_rollback_pops_prior_versions(self, gate_with_priors: PromotionGate) -> None:
        assert len(gate_with_priors.prior_versions) == 2
        gate_with_priors.rollback(breach_time_s=10.0)
        assert gate_with_priors.prior_versions == ("v1.0.0",)

    def test_rollback_empty_prior_versions_rejected(self) -> None:
        gate = PromotionGate(
            budgets=CanaryBudgets(
                quality_floor=0.8,
                safety_floor=0.9,
                latency_p99_ceiling_ms=200.0,
                error_rate_ceiling=0.05,
                cost_ceiling_usd_per_kreq=0.01,
            ),
            current_version="v1.0.0",
        )
        with pytest.raises(ValueError, match="cannot roll back"):
            gate.rollback(breach_time_s=10.0)

    def test_negative_breach_time_rejected(self, gate_with_priors: PromotionGate) -> None:
        with pytest.raises(ValueError, match="breach_time_s"):
            gate_with_priors.rollback(breach_time_s=-1.0)

    def test_bool_breach_time_rejected(self, gate_with_priors: PromotionGate) -> None:
        with pytest.raises(ValueError, match="breach_time_s"):
            gate_with_priors.rollback(breach_time_s=True)  # type: ignore[arg-type]

    def test_rollback_preserves_other_aliases(self, gate_with_priors: PromotionGate) -> None:
        gate_with_priors.alias_swap(alias="staging", to_version="v3.0.0")
        gate_with_priors.rollback(breach_time_s=10.0)
        assert gate_with_priors.resolve_alias("staging") == "v3.0.0"


# ---------------------------------------------------------------------------
# PromotionGate — enforce_retention
# ---------------------------------------------------------------------------


class TestPromotionGateRetention:
    @pytest.fixture
    def budgets(self) -> CanaryBudgets:
        return CanaryBudgets(
            quality_floor=0.8,
            safety_floor=0.9,
            latency_p99_ceiling_ms=200.0,
            error_rate_ceiling=0.05,
            cost_ceiling_usd_per_kreq=0.01,
        )

    def test_enforce_retention_allows_two_priors(self, budgets: CanaryBudgets) -> None:
        gate = PromotionGate(
            budgets=budgets,
            current_version="v3.0.0",
            prior_versions=("v2.0.0", "v1.0.0"),
            enforce_retention=True,
        )
        assert gate.enforce_retention is True

    def test_enforce_retention_allows_three_priors(self, budgets: CanaryBudgets) -> None:
        gate = PromotionGate(
            budgets=budgets,
            current_version="v4.0.0",
            prior_versions=("v3.0.0", "v2.0.0", "v1.0.0"),
            enforce_retention=True,
        )
        assert len(gate.prior_versions) == 3

    def test_enforce_retention_rejects_one_prior(self, budgets: CanaryBudgets) -> None:
        with pytest.raises(ValueError, match="retention policy requires at least 2"):
            PromotionGate(
                budgets=budgets,
                current_version="v2.0.0",
                prior_versions=("v1.0.0",),
                enforce_retention=True,
            )

    def test_enforce_retention_rejects_zero_priors(self, budgets: CanaryBudgets) -> None:
        with pytest.raises(ValueError, match="retention policy requires at least 2"):
            PromotionGate(
                budgets=budgets,
                current_version="v1.0.0",
                enforce_retention=True,
            )

    def test_enforce_retention_off_allows_zero_priors(self, budgets: CanaryBudgets) -> None:
        gate = PromotionGate(
            budgets=budgets,
            current_version="v1.0.0",
            enforce_retention=False,
        )
        assert gate.prior_versions == ()

    def test_enforce_retention_off_allows_one_prior(self, budgets: CanaryBudgets) -> None:
        gate = PromotionGate(
            budgets=budgets,
            current_version="v2.0.0",
            prior_versions=("v1.0.0",),
            enforce_retention=False,
        )
        assert len(gate.prior_versions) == 1


# ---------------------------------------------------------------------------
# PromotionGate — constructor edge cases
# ---------------------------------------------------------------------------


class TestPromotionGateConstructor:
    @pytest.fixture
    def budgets(self) -> CanaryBudgets:
        return CanaryBudgets(
            quality_floor=0.8,
            safety_floor=0.9,
            latency_p99_ceiling_ms=200.0,
            error_rate_ceiling=0.05,
            cost_ceiling_usd_per_kreq=0.01,
        )

    def test_budgets_must_be_canary_budgets(self) -> None:
        with pytest.raises(ValueError, match="budgets must be a CanaryBudgets"):
            PromotionGate(budgets={"a": 1}, current_version="v1")  # type: ignore[arg-type]

    def test_empty_current_version_rejected(self, budgets: CanaryBudgets) -> None:
        with pytest.raises(ValueError, match="current_version"):
            PromotionGate(budgets=budgets, current_version="")

    def test_empty_prior_version_element_rejected(self, budgets: CanaryBudgets) -> None:
        with pytest.raises(ValueError, match="prior_versions"):
            PromotionGate(budgets=budgets, current_version="v1", prior_versions=("v2", ""))

    def test_prior_versions_must_be_tuple(self, budgets: CanaryBudgets) -> None:
        with pytest.raises(ValueError, match="prior_versions must be a tuple"):
            PromotionGate(budgets=budgets, current_version="v1", prior_versions=["v2"])  # type: ignore[arg-type]

    def test_negative_rollback_window_rejected(self, budgets: CanaryBudgets) -> None:
        with pytest.raises(ValueError, match="rollback_window_s"):
            PromotionGate(budgets=budgets, current_version="v1", rollback_window_s=0)

    def test_zero_rollback_window_rejected(self, budgets: CanaryBudgets) -> None:
        with pytest.raises(ValueError, match="rollback_window_s"):
            PromotionGate(budgets=budgets, current_version="v1", rollback_window_s=0)

    def test_production_alias_seeded(self, budgets: CanaryBudgets) -> None:
        gate = PromotionGate(budgets=budgets, current_version="v1.0.0")
        assert gate.resolve_alias("production") == "v1.0.0"

    def test_unknown_alias_returns_none(self, budgets: CanaryBudgets) -> None:
        gate = PromotionGate(budgets=budgets, current_version="v1.0.0")
        assert gate.resolve_alias("nonexistent") is None


# ---------------------------------------------------------------------------
# RollbackResult
# ---------------------------------------------------------------------------


class TestRollbackResult:
    def test_valid_result_construct(self) -> None:
        result = RollbackResult(
            swapped_back_to="v1.0.0",
            initiated_within_60s=True,
            seconds_to_initiate=10.5,
        )
        assert result.swapped_back_to == "v1.0.0"
        assert result.initiated_within_60s is True
        assert result.seconds_to_initiate == 10.5

    def test_result_is_frozen(self) -> None:
        result = RollbackResult(
            swapped_back_to="v1.0.0",
            initiated_within_60s=True,
            seconds_to_initiate=10.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.swapped_back_to = "v2"  # type: ignore[misc]

    def test_empty_swapped_back_to_rejected(self) -> None:
        with pytest.raises(ValueError, match="swapped_back_to"):
            RollbackResult(swapped_back_to="", initiated_within_60s=True, seconds_to_initiate=10.0)

    def test_initiated_within_60s_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="initiated_within_60s"):
            RollbackResult(swapped_back_to="v1", initiated_within_60s="yes", seconds_to_initiate=10.0)  # type: ignore[arg-type]

    def test_seconds_to_initiate_must_be_number(self) -> None:
        with pytest.raises(ValueError, match="seconds_to_initiate"):
            RollbackResult(swapped_back_to="v1", initiated_within_60s=True, seconds_to_initiate="slow")  # type: ignore[arg-type]

    def test_negative_seconds_rejected(self) -> None:
        with pytest.raises(ValueError, match="seconds_to_initiate"):
            RollbackResult(swapped_back_to="v1", initiated_within_60s=True, seconds_to_initiate=-1.0)

    def test_zero_seconds_is_valid(self) -> None:
        result = RollbackResult(
            swapped_back_to="v1.0.0",
            initiated_within_60s=True,
            seconds_to_initiate=0.0,
        )
        assert result.seconds_to_initiate == 0.0

    def test_bool_seconds_rejected(self) -> None:
        with pytest.raises(ValueError, match="seconds_to_initiate"):
            RollbackResult(swapped_back_to="v1", initiated_within_60s=True, seconds_to_initiate=False)  # type: ignore[arg-type]

    def test_missed_slo_result(self) -> None:
        result = RollbackResult(
            swapped_back_to="v1.0.0",
            initiated_within_60s=False,
            seconds_to_initiate=75.0,
        )
        assert result.initiated_within_60s is False
        assert result.seconds_to_initiate == 75.0
