"""Unit tests for AIML Phase C/E: accelerator execution (AIML-017) and
zero-downtime delivery (AIML-020).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md:

  - §10 Accelerator-Aware Execution: the planner discovers permitted
    hardware WITHOUT provisioning it, then chooses topology, precision,
    parallelism, batching, checkpointing, and serving settings from
    measured capability. Provisioning requires an approval token. Teardown
    is idempotent and emits proof. Preemption resumes from the last
    verified checkpoint; it does not restart spending from zero without
    approval. Budget/quota exhaustion stops before overrun.
  - §12 Zero-Downtime Delivery: build -> validate -> shadow -> canary ->
    compare -> swap. Canary compares quality/safety/latency/error/cost
    budgets. Rollback within 60s of a hard threshold breach. Alias swap is
    atomic and in-flight requests finish on the original version.

Corresponds to AIML-AT-016 (dry-run identifies hardware without
provisioning; live path requires approval), AIML-AT-017 (preempted
training resumes from last verified checkpoint without double-counting
spend), and AIML-AT-005 (rollback serves 100% successful requests while
atomically returning within 60 seconds).
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.accelerators import (
    AcceleratorKind,
    AcceleratorPlanner,
    CheckpointRef,
    DryRunResult,
    ExecutionPlan,
    HardwareDescriptor,
    ResumeResult,
    TeardownProof,
)
from general_ludd.ai_ml.promotion import (
    AliasSwap,
    CanaryBudgets,
    CanaryMetrics,
    CanaryVerdict,
    PromotionGate,
    PromotionPhase,
    RollbackResult,
)

_SHA_IMAGE = "a" * 64
_SHA_CKPT_GOOD = "b" * 64
_SHA_CKPT_BAD = "c" * 64


# ---------------------------------------------------------------------------
# AIML-017 / AIML-AT-016 — hardware discovery + dry-run
# ---------------------------------------------------------------------------


def _local_cpu() -> HardwareDescriptor:
    return HardwareDescriptor(
        kind=AcceleratorKind.CPU,
        name="local-cpu",
        sku="cpu-x86-64",
        region="local",
        provider="local",
        approved=True,
    )


def _local_gpu() -> HardwareDescriptor:
    return HardwareDescriptor(
        kind=AcceleratorKind.GPU,
        name="local-gpu-a100",
        sku="a100-80gb",
        region="local",
        provider="local",
        approved=True,
        cuda_compute_capability="8.0",
    )


def _cloud_a100() -> HardwareDescriptor:
    return HardwareDescriptor(
        kind=AcceleratorKind.CLOUD,
        name="azure-nd-a100-v4",
        sku="Standard_ND96asr_v4",
        region="eastus",
        provider="azure",
        approved=True,
        cuda_compute_capability="8.0",
    )


def _cloud_h100_unapproved() -> HardwareDescriptor:
    return HardwareDescriptor(
        kind=AcceleratorKind.CLOUD,
        name="azure-nd-h100-v5",
        sku="Standard_ND96isr_H100_v5",
        region="westus3",
        provider="azure",
        approved=False,
        cuda_compute_capability="9.0",
    )


class TestDiscoverHardware:
    def test_discover_returns_local_cpu(self) -> None:
        """Spec §10: planner discovers permitted local CPU hardware."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
        )
        discovered = planner.discover_hardware()
        assert any(h.kind is AcceleratorKind.CPU for h in discovered)

    def test_discover_returns_local_gpu_when_present(self) -> None:
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset(),
            local_hardware=(_local_cpu(), _local_gpu()),
        )
        discovered = planner.discover_hardware()
        gpu_kinds = [h for h in discovered if h.kind is AcceleratorKind.GPU]
        assert len(gpu_kinds) == 1
        assert gpu_kinds[0].name == "local-gpu-a100"

    def test_discover_filters_to_approved_cloud_skus(self) -> None:
        """Spec §10: only APPROVED cloud accelerators are surfaced. An
        unapproved H100 SKU must not appear in the discovered set."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(), _cloud_h100_unapproved()),
        )
        discovered = planner.discover_hardware()
        names = {h.name for h in discovered}
        assert "azure-nd-a100-v4" in names  # approved
        assert "azure-nd-h100-v5" not in names  # not in approved_cloud_skus


# ---------------------------------------------------------------------------
# AIML-AT-016 — dry-run does NOT provision; live path requires approval
# ---------------------------------------------------------------------------


def _base_plan_kwargs() -> dict:
    return dict(
        sku="Standard_ND96asr_v4",
        region="eastus",
        quota_evidence="quota://azure-eastus-nd96/2026-07-30",
        image_digest=_SHA_IMAGE,
        driver_version="535.104.05",
        runtime_version="cuda-12.2",
        interconnect="infiniband-200g",
        storage_gb=2048,
        network_mbps=100000,
        budget_usd=50.0,
        timeout_s=7200,
        checkpoint_uri="artifacts://ckpts/run-1/step-1000",
        teardown_behavior="release",
    )


class TestDryRunVsLive:
    def test_dry_run_identifies_hardware_without_provisioning(self) -> None:
        """AIML-AT-016: dry-run identifies an approved Azure A100/H100-class
        plan WITHOUT provisioning. The result must carry provisioned=False
        and the planner must have no live resources."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(),),
        )
        result = planner.dry_run(**_base_plan_kwargs())
        assert isinstance(result, DryRunResult)
        assert result.provisioned is False
        assert isinstance(result.plan, ExecutionPlan)
        assert result.plan.approval_token is None
        # Dry-run MUST NOT create any provisioned resources.
        assert planner.live_resources() == frozenset()
        # The hardware the plan would use is identifiable.
        assert any(h.name == "azure-nd-a100-v4" for h in result.hardware)

    def test_live_plan_execution_requires_approval_token(self) -> None:
        """Spec §10: 'Provisioning requires an approval token.' Calling
        plan_execution without a token for a CLOUD SKU must refuse."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(),),
        )
        with pytest.raises(ValueError, match="approval_token"):
            planner.plan_execution(**_base_plan_kwargs())  # no token
        # No provisioning happened.
        assert planner.live_resources() == frozenset()

    def test_live_plan_execution_with_token_provisions_resource(self) -> None:
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(),),
        )
        plan = planner.plan_execution(
            **_base_plan_kwargs(),
            approval_token="tok-abc-123",
        )
        assert plan.approval_token == "tok-abc-123"
        assert plan.sku in planner.live_resources()

    def test_local_plan_does_not_require_approval_token(self) -> None:
        """Local CPU/GPU plans do not need an approval token; only cloud
        provisioning does (spec §10 mentions approved cloud accelerators)."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset(),
            local_hardware=(_local_cpu(),),
        )
        plan = planner.plan_execution(
            sku="cpu-x86-64",
            region="local",
            quota_evidence="local-no-quota",
            image_digest=_SHA_IMAGE,
            driver_version="n/a",
            runtime_version="cpu",
            interconnect="n/a",
            storage_gb=10,
            network_mbps=1000,
            budget_usd=0.0,
            timeout_s=300,
            checkpoint_uri=None,
            teardown_behavior="release",
        )
        assert plan.approval_token is None


# ---------------------------------------------------------------------------
# AIML-017 — teardown idempotency
# ---------------------------------------------------------------------------


class TestTeardownIdempotent:
    def test_teardown_is_idempotent_and_emits_proof(self) -> None:
        """Spec §10: 'Teardown is idempotent and emits proof that resources
        were released.' Calling teardown twice on the same plan must not
        raise and must emit a proof each time."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(),),
        )
        plan = planner.plan_execution(
            **_base_plan_kwargs(),
            approval_token="tok-teardown-1",
        )
        proof1 = planner.teardown(plan)
        assert isinstance(proof1, TeardownProof)
        assert plan.sku in proof1.resources_released
        # Second teardown is idempotent: no raise, proof still emitted, but
        # the resource is no longer live.
        proof2 = planner.teardown(plan)
        assert isinstance(proof2, TeardownProof)
        assert plan.sku not in planner.live_resources()


# ---------------------------------------------------------------------------
# AIML-AT-017 — preemption resumes from verified checkpoint
# ---------------------------------------------------------------------------


class TestResumeFromCheckpoint:
    def test_resume_from_verified_checkpoint_carries_incurred_spend(self) -> None:
        """AIML-AT-017: preempted training resumes from the last VERIFIED
        checkpoint without double-counting spend. The resumed plan's budget
        must reflect already-incurred spend, NOT restart from zero."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(),),
        )
        ckpt = CheckpointRef(
            uri="artifacts://ckpts/run-1/step-2000",
            step=2000,
            sha256=_SHA_CKPT_GOOD,
            verified=True,
        )
        result = planner.resume_from_checkpoint(
            checkpoint=ckpt,
            spend_already_incurred_usd=12.50,
            original_budget_usd=50.0,
        )
        assert isinstance(result, ResumeResult)
        assert result.resume_from is ckpt
        # The carry-over budget MUST subtract spent, not zero-out.
        assert result.remaining_budget_usd == pytest.approx(50.0 - 12.50)
        assert result.resume_step == 2000

    def test_resume_rejects_unverified_checkpoint(self) -> None:
        """Spec §10: preemption resumes from the last VERIFIED checkpoint.
        An unverified checkpoint must NOT be used for resume — doing so
        risks resuming from a corrupt or partial state."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset(),
            local_hardware=(_local_cpu(),),
        )
        ckpt = CheckpointRef(
            uri="artifacts://ckpts/run-1/step-2000",
            step=2000,
            sha256=_SHA_CKPT_GOOD,
            verified=False,
        )
        with pytest.raises(ValueError, match="verified"):
            planner.resume_from_checkpoint(
                checkpoint=ckpt,
                spend_already_incurred_usd=10.0,
                original_budget_usd=50.0,
            )

    def test_checkpoint_integrity_check_detects_digest_mismatch(self) -> None:
        """Spec §11: 'Hash/signature mismatch -> Hard fail.' A checkpoint
        whose verified digest does not match the recorded sha256 must fail
        verification."""
        ckpt = CheckpointRef(
            uri="artifacts://ckpts/run-1/step-2000",
            step=2000,
            sha256=_SHA_CKPT_GOOD,
            verified=True,
        )
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset(),
            local_hardware=(_local_cpu(),),
        )
        # The on-disk digest differs from the recorded one.
        assert planner.verify_checkpoint(ckpt, on_disk_sha256=_SHA_CKPT_BAD) is False
        assert planner.verify_checkpoint(ckpt, on_disk_sha256=_SHA_CKPT_GOOD) is True


# ---------------------------------------------------------------------------
# AIML-017 / §11 — budget exhaustion stops before overrun
# ---------------------------------------------------------------------------


class TestBudgetExhaustion:
    def test_budget_exhaustion_returns_awaiting_approval_not_overrun(self) -> None:
        """Spec §11: 'Budget/quota exhaustion -> Stop before overrun and
        return awaiting_approval or failed.' The planner's budget gate must
        refuse a plan whose budget is exhausted before spend begins."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(),),
        )
        # Already-incurred spend equals original budget -> nothing left.
        ckpt = CheckpointRef(
            uri="artifacts://ckpts/run-1/step-5000",
            step=5000,
            sha256=_SHA_CKPT_GOOD,
            verified=True,
        )
        with pytest.raises(ValueError, match="budget"):
            planner.resume_from_checkpoint(
                checkpoint=ckpt,
                spend_already_incurred_usd=50.0,
                original_budget_usd=50.0,
            )

    def test_plan_execution_rejects_negative_budget(self) -> None:
        """A negative budget is structurally invalid — the contract-level
        invariant is enforced at plan construction."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset(),
            local_hardware=(_local_cpu(),),
        )
        kwargs = _base_plan_kwargs()
        kwargs["budget_usd"] = -1.0
        with pytest.raises(ValueError, match="budget"):
            planner.plan_execution(**kwargs)


# ---------------------------------------------------------------------------
# AIML-017 — ExecutionPlan carries every spec §10 declared field
# ---------------------------------------------------------------------------


class TestExecutionPlanFields:
    def test_execution_plan_records_all_declared_fields(self) -> None:
        """Spec §10: 'Each execution plan declares SKU, region, quota
        evidence, image digest, driver and runtime versions, interconnect
        assumptions, storage/network needs, budget, timeout, checkpoint
        path, teardown behavior, and fallback.'"""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(),),
        )
        plan = planner.plan_execution(
            **_base_plan_kwargs(),
            approval_token="tok-fields-1",
            fallback_sku="cpu-x86-64",
        )
        assert plan.sku == "Standard_ND96asr_v4"
        assert plan.region == "eastus"
        assert plan.quota_evidence.startswith("quota://")
        assert plan.image_digest == _SHA_IMAGE
        assert plan.driver_version == "535.104.05"
        assert plan.runtime_version == "cuda-12.2"
        assert plan.interconnect == "infiniband-200g"
        assert plan.storage_gb == 2048
        assert plan.network_mbps == 100000
        assert plan.budget_usd == 50.0
        assert plan.timeout_s == 7200
        assert plan.checkpoint_uri is not None
        assert plan.teardown_behavior == "release"
        assert plan.fallback_sku == "cpu-x86-64"

    def test_execution_plan_rejects_unapproved_sku(self) -> None:
        """Spec §10: only approved cloud accelerators may be provisioned."""
        planner = AcceleratorPlanner(
            approved_cloud_skus=frozenset({"Standard_ND96asr_v4"}),
            local_hardware=(_local_cpu(),),
            cloud_catalog=(_cloud_a100(), _cloud_h100_unapproved()),
        )
        kwargs = _base_plan_kwargs()
        kwargs["sku"] = "Standard_ND96isr_H100_v5"  # unapproved
        with pytest.raises(ValueError, match="approved"):
            planner.plan_execution(**kwargs, approval_token="t")


# ===========================================================================
# AIML-020 — Zero-downtime promotion
# ===========================================================================


def _default_budgets() -> CanaryBudgets:
    return CanaryBudgets(
        quality_floor=0.85,
        safety_floor=0.99,
        latency_p99_ceiling_ms=250.0,
        error_rate_ceiling=0.01,
        cost_ceiling_usd_per_kreq=0.50,
    )


def _healthy_metrics() -> CanaryMetrics:
    return CanaryMetrics(
        quality=0.90,
        safety=0.995,
        latency_p99_ms=180.0,
        error_rate=0.003,
        cost_usd_per_kreq=0.42,
    )


# ---------------------------------------------------------------------------
# AIML-020 §12 — promotion pipeline phases
# ---------------------------------------------------------------------------


class TestPromotionPhases:
    def test_promotion_phase_order_matches_spec(self) -> None:
        """Spec §12: build -> validate -> shadow -> canary -> compare ->
        swap. The enum order must reflect the spec."""
        order = [
            PromotionPhase.BUILD,
            PromotionPhase.VALIDATE,
            PromotionPhase.SHADOW,
            PromotionPhase.CANARY,
            PromotionPhase.COMPARE,
            PromotionPhase.SWAP,
        ]
        # Each phase has the spec name.
        assert [p.value for p in order] == [
            "build",
            "validate",
            "shadow",
            "canary",
            "compare",
            "swap",
        ]


# ---------------------------------------------------------------------------
# AIML-020 §12 step 6 — canary budget comparison
# ---------------------------------------------------------------------------


class TestCanaryCheck:
    def test_canary_check_passes_when_all_budgets_satisfied(self) -> None:
        gate = PromotionGate(
            budgets=_default_budgets(),
            current_version="v1",
        )
        verdict = gate.canary_check(_healthy_metrics())
        assert isinstance(verdict, CanaryVerdict)
        assert verdict.healthy is True
        assert verdict.breached_budgets == ()

    def test_canary_check_blocks_on_safety_breach(self) -> None:
        """Spec §11: 'Knowledge/index/model canary regression -> Automatic
        rollback.' Safety is a hard floor; breaching it must flag unhealthy."""
        gate = PromotionGate(budgets=_default_budgets(), current_version="v1")
        metrics = CanaryMetrics(
            quality=0.90,
            safety=0.95,  # below 0.99 floor
            latency_p99_ms=180.0,
            error_rate=0.003,
            cost_usd_per_kreq=0.42,
        )
        verdict = gate.canary_check(metrics)
        assert verdict.healthy is False
        assert "safety" in verdict.breached_budgets

    def test_canary_check_blocks_on_latency_breach(self) -> None:
        gate = PromotionGate(budgets=_default_budgets(), current_version="v1")
        metrics = CanaryMetrics(
            quality=0.90,
            safety=0.995,
            latency_p99_ms=400.0,  # above 250ms ceiling
            error_rate=0.003,
            cost_usd_per_kreq=0.42,
        )
        verdict = gate.canary_check(metrics)
        assert verdict.healthy is False
        assert "latency" in verdict.breached_budgets

    def test_canary_check_blocks_on_error_rate_breach(self) -> None:
        gate = PromotionGate(budgets=_default_budgets(), current_version="v1")
        metrics = CanaryMetrics(
            quality=0.90,
            safety=0.995,
            latency_p99_ms=180.0,
            error_rate=0.05,  # above 0.01 ceiling
            cost_usd_per_kreq=0.42,
        )
        verdict = gate.canary_check(metrics)
        assert verdict.healthy is False
        assert "error_rate" in verdict.breached_budgets

    def test_canary_check_blocks_on_cost_breach(self) -> None:
        gate = PromotionGate(budgets=_default_budgets(), current_version="v1")
        metrics = CanaryMetrics(
            quality=0.90,
            safety=0.995,
            latency_p99_ms=180.0,
            error_rate=0.003,
            cost_usd_per_kreq=0.80,  # above 0.50 ceiling
        )
        verdict = gate.canary_check(metrics)
        assert verdict.healthy is False
        assert "cost" in verdict.breached_budgets


# ---------------------------------------------------------------------------
# AIML-020 §12 step 7 — atomic alias swap with in-flight drain
# ---------------------------------------------------------------------------


class TestAliasSwap:
    def test_alias_swap_is_atomic_and_pins_in_flight_requests(self) -> None:
        """Spec §12 step 7: 'Atomically swap the alias; in-flight requests
        finish on their original version.' After swap, new requests see the
        new version; in-flight requests continue against the prior version
        until drained."""
        gate = PromotionGate(
            budgets=_default_budgets(),
            current_version="v1",
            prior_versions=("v0",),
        )
        swap = gate.alias_swap(
            alias="production-retrieval-index",
            to_version="v2",
            in_flight_requests=5,
        )
        assert isinstance(swap, AliasSwap)
        assert swap.alias == "production-retrieval-index"
        assert swap.from_version == "v1"
        assert swap.to_version == "v2"
        assert swap.in_flight_requests == 5
        assert swap.drained is False  # still in flight
        # The gate tracks the swap; new lookups resolve to v2.
        assert gate.resolve_alias("production-retrieval-index") == "v2"

    def test_alias_swap_drains_in_flight_requests(self) -> None:
        """Spec §12: zero dropped accepted requests. drain_in_flight must
        mark the swap complete and record zero remaining in-flight."""
        gate = PromotionGate(
            budgets=_default_budgets(),
            current_version="v1",
        )
        swap = gate.alias_swap(
            alias="prod",
            to_version="v2",
            in_flight_requests=3,
        )
        drained = gate.drain_in_flight(swap)
        assert drained.drained is True
        assert drained.in_flight_requests == 0


# ---------------------------------------------------------------------------
# AIML-AT-005 / §12 — rollback within 60s
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_within_60s_marks_success(self) -> None:
        """AIML-AT-005: rollback serves 100% successful requests while
        atomically returning to the prior snapshot within 60 seconds."""
        gate = PromotionGate(
            budgets=_default_budgets(),
            current_version="v2",
            prior_versions=("v1", "v0"),
        )
        result = gate.rollback(breach_time_s=12.0)
        assert isinstance(result, RollbackResult)
        assert result.swapped_back_to == "v1"  # immediate prior
        assert result.initiated_within_60s is True
        assert result.seconds_to_initiate == pytest.approx(12.0)
        # Alias now points back at the prior version.
        assert gate.resolve_alias("production") == "v1"

    def test_rollback_after_60s_flags_objective_miss(self) -> None:
        """Spec §12: 'rollback initiation within 60 seconds of a hard
        threshold breach.' A rollback past 60s is still executed (the
        prior version is restored) but the SLO is marked missed."""
        gate = PromotionGate(
            budgets=_default_budgets(),
            current_version="v2",
            prior_versions=("v1",),
        )
        result = gate.rollback(breach_time_s=95.0)
        assert result.initiated_within_60s is False
        assert result.swapped_back_to == "v1"

    def test_rollback_requires_prior_version_to_exist(self) -> None:
        """Spec §12 step 8: 'Retain at least the prior two known-good
        versions and rehearse rollback.' If no prior version exists,
        rollback must refuse (no silent no-op)."""
        gate = PromotionGate(
            budgets=_default_budgets(),
            current_version="v1",
            prior_versions=(),  # nothing to roll back to
        )
        with pytest.raises(ValueError, match="prior"):
            gate.rollback(breach_time_s=5.0)


# ---------------------------------------------------------------------------
# AIML-020 §12 step 8 — prior-version retention
# ---------------------------------------------------------------------------


class TestVersionRetention:
    def test_promotion_gate_retains_at_least_two_prior_versions(self) -> None:
        """Spec §12 step 8: 'Retain at least the prior two known-good
        versions and rehearse rollback.'"""
        gate = PromotionGate(
            budgets=_default_budgets(),
            current_version="v3",
            prior_versions=("v2", "v1", "v0"),
        )
        assert len(gate.prior_versions) >= 2

    def test_promotion_gate_rejects_insufficient_retention_at_construction(
        self,
    ) -> None:
        """When ``enforce_retention=True`` the gate refuses to construct
        without the spec-mandated two prior versions."""
        with pytest.raises(ValueError, match="retention"):
            PromotionGate(
                budgets=_default_budgets(),
                current_version="v1",
                prior_versions=("v0",),  # only 1
                enforce_retention=True,
            )
