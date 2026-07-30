"""Unit tests for AIML Phase C: adaptation (AIML-008) and evaluation (AIML-016).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §6.4 (LoRA/QLoRA/adapters/
distillation) and AIML-016 (Evaluation):

  - ``AdapterManifest`` records base-model digest, method, target modules,
    rank, alpha, dropout, quantization, optimizer, seed, dataset manifest,
    and precision.
  - Serving an adapter against a different base-model digest is a hard
    failure (spec §6.4: "Serving an adapter against a different base digest
    is a hard failure"; AIML-AT-008).
  - ``plan_adaptation`` produces a restartable ``TrainingPlan`` with
    checkpoint and resume strategy (spec §6.4: "Training jobs are
    restartable from verified checkpoints").
  - OOM, NaN/Inf, divergent loss, budget overrun, and corrupt checkpoint
    conditions stop safely and preserve diagnostic artifacts (spec §6.4,
    §11).
  - ``EvaluationHarness`` compares candidates on quality, safety, latency,
    cost, energy, robustness, and calibration; ``BenchmarkResult`` carries
    per-metric scores; regression detection blocks promotion when a
    statistically significant regression is detected (AIML-016, §5.3).
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.adaptation import (
    AdapterManifest,
    AdapterMethod,
    CheckpointStrategy,
    Quantization,
    SafeStopResult,
    TrainingPlan,
    TrainingStopReason,
    plan_adaptation,
    safe_stop,
    validate_adapter,
)
from general_ludd.ai_ml.evaluation import (
    BenchmarkResult,
    EvaluationHarness,
    MetricKind,
    MetricScore,
    PromotionDecision,
    RegressionVerdict,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_DATASET = "c" * 64
_SHA_DEPS = "d" * 64


# ---------------------------------------------------------------------------
# AIML-008 — AdapterManifest provenance fields
# ---------------------------------------------------------------------------


class TestAdapterManifestProvenance:
    def test_manifest_records_base_model_digest_method_rank_alpha_seed(self) -> None:
        """Spec §6.4: adapter_train records base-model digest, adapter method,
        target modules, rank, alpha, dropout, quantization, optimizer, seed."""
        m = AdapterManifest(
            base_model_digest=_SHA_A,
            method=AdapterMethod.LORA,
            target_modules=("q_proj", "v_proj"),
            rank=8,
            alpha=16,
            dropout=0.05,
            quantization=None,
            optimizer="adamw",
            seed=42,
            dataset_manifest_sha256=_SHA_DATASET,
            tokenizer="tiktoken-cl100k",
            precision="bf16",
            dependency_lock_sha256=_SHA_DEPS,
            base_model_record_id="mdl-base-1",
        )
        assert m.base_model_digest == _SHA_A
        assert m.method is AdapterMethod.LORA
        assert m.rank == 8
        assert m.alpha == 16
        assert m.dropout == 0.05
        assert m.seed == 42
        assert m.target_modules == ("q_proj", "v_proj")
        assert m.optimizer == "adamw"
        assert m.precision == "bf16"
        assert m.dataset_manifest_sha256 == _SHA_DATASET

    def test_manifest_rejects_missing_base_model_digest(self) -> None:
        with pytest.raises(ValueError, match="base_model_digest"):
            AdapterManifest(
                base_model_digest="not-a-sha",
                method=AdapterMethod.LORA,
                target_modules=("q_proj",),
                rank=8,
                alpha=16,
                dropout=0.0,
                quantization=None,
                optimizer="adamw",
                seed=0,
                dataset_manifest_sha256=_SHA_DATASET,
                tokenizer="tok",
                precision="bf16",
                dependency_lock_sha256=_SHA_DEPS,
                base_model_record_id="m",
            )

    def test_manifest_rejects_nonpositive_rank(self) -> None:
        with pytest.raises(ValueError, match="rank"):
            AdapterManifest(
                base_model_digest=_SHA_A,
                method=AdapterMethod.LORA,
                target_modules=("q_proj",),
                rank=0,
                alpha=16,
                dropout=0.0,
                quantization=None,
                optimizer="adamw",
                seed=0,
                dataset_manifest_sha256=_SHA_DATASET,
                tokenizer="tok",
                precision="bf16",
                dependency_lock_sha256=_SHA_DEPS,
                base_model_record_id="m",
            )

    def test_manifest_rejects_dropout_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="dropout"):
            AdapterManifest(
                base_model_digest=_SHA_A,
                method=AdapterMethod.LORA,
                target_modules=("q_proj",),
                rank=4,
                alpha=8,
                dropout=1.5,
                quantization=None,
                optimizer="adamw",
                seed=0,
                dataset_manifest_sha256=_SHA_DATASET,
                tokenizer="tok",
                precision="bf16",
                dependency_lock_sha256=_SHA_DEPS,
                base_model_record_id="m",
            )

    def test_manifest_rejects_empty_target_modules(self) -> None:
        with pytest.raises(ValueError, match="target_modules"):
            AdapterManifest(
                base_model_digest=_SHA_A,
                method=AdapterMethod.LORA,
                target_modules=(),
                rank=4,
                alpha=8,
                dropout=0.0,
                quantization=None,
                optimizer="adamw",
                seed=0,
                dataset_manifest_sha256=_SHA_DATASET,
                tokenizer="tok",
                precision="bf16",
                dependency_lock_sha256=_SHA_DEPS,
                base_model_record_id="m",
            )

    def test_qlora_requires_quantization(self) -> None:
        """QLoRA implies 4-bit quantization; missing it is a contract violation."""
        with pytest.raises(ValueError, match="quantization"):
            AdapterManifest(
                base_model_digest=_SHA_A,
                method=AdapterMethod.QLORA,
                target_modules=("q_proj",),
                rank=4,
                alpha=8,
                dropout=0.0,
                quantization=None,
                optimizer="adamw",
                seed=0,
                dataset_manifest_sha256=_SHA_DATASET,
                tokenizer="tok",
                precision="bf16",
                dependency_lock_sha256=_SHA_DEPS,
                base_model_record_id="m",
            )


# ---------------------------------------------------------------------------
# AIML-008 / AIML-AT-008 — Base-model digest hard-binding
# ---------------------------------------------------------------------------


class TestAdapterBaseDigestBinding:
    def _manifest(self, digest: str = _SHA_A) -> AdapterManifest:
        return AdapterManifest(
            base_model_digest=digest,
            method=AdapterMethod.LORA,
            target_modules=("q_proj",),
            rank=8,
            alpha=16,
            dropout=0.0,
            quantization=None,
            optimizer="adamw",
            seed=42,
            dataset_manifest_sha256=_SHA_DATASET,
            tokenizer="tok",
            precision="bf16",
            dependency_lock_sha256=_SHA_DEPS,
            base_model_record_id="m",
        )

    def test_validate_adapter_accepts_matching_base_digest(self) -> None:
        m = self._manifest(_SHA_A)
        # AIML-AT-008: succeeds reproducibly with the pinned digest.
        validate_adapter(m, serving_base_digest=_SHA_A)

    def test_validate_adapter_rejects_one_byte_digest_mismatch(self) -> None:
        """Spec §6.4: Serving an adapter against a different base digest is a
        hard failure. AIML-AT-008: a one-byte digest mismatch fails the load."""
        m = self._manifest(_SHA_A)
        one_byte_off = "a" * 63 + "b"
        with pytest.raises(ValueError, match="base_model_digest"):
            validate_adapter(m, serving_base_digest=one_byte_off)


# ---------------------------------------------------------------------------
# AIML-008 — plan_adaptation produces restartable training plan
# ---------------------------------------------------------------------------


class TestPlanAdaptation:
    def test_plan_adaptation_returns_training_plan_with_manifest_and_checkpoint(self) -> None:
        plan = plan_adaptation(
            base_model_digest=_SHA_A,
            method=AdapterMethod.LORA,
            target_modules=("q_proj", "v_proj"),
            rank=8,
            alpha=16,
            dataset_manifest_sha256=_SHA_DATASET,
            tokenizer="tiktoken",
            dependency_lock_sha256=_SHA_DEPS,
            seed=42,
            checkpoint_dir="artifacts://ckpts/run-1",
        )
        assert isinstance(plan, TrainingPlan)
        assert isinstance(plan.manifest, AdapterManifest)
        assert isinstance(plan.checkpoint, CheckpointStrategy)
        # Manifest carries all constraint inputs.
        assert plan.manifest.rank == 8
        assert plan.manifest.alpha == 16
        assert plan.manifest.seed == 42
        assert plan.manifest.base_model_digest == _SHA_A

    def test_training_plan_checkpoint_strategy_includes_interval_and_dir(self) -> None:
        plan = plan_adaptation(
            base_model_digest=_SHA_A,
            method=AdapterMethod.LORA,
            target_modules=("q_proj",),
            rank=4,
            alpha=8,
            dataset_manifest_sha256=_SHA_DATASET,
            tokenizer="tok",
            dependency_lock_sha256=_SHA_DEPS,
            seed=7,
            checkpoint_dir="artifacts://ckpts/run-2",
        )
        assert plan.checkpoint.checkpoint_dir == "artifacts://ckpts/run-2"
        # Spec §6.4: "Training jobs are restartable from verified checkpoints."
        assert plan.checkpoint.checkpoint_interval_steps > 0
        assert plan.checkpoint.verify_checkpoints is True

    def test_training_plan_supports_resume_from_verified_checkpoint(self) -> None:
        """Spec §6.4 / §10: preemption resumes from the last verified
        checkpoint; it does not restart spending from zero."""
        plan = plan_adaptation(
            base_model_digest=_SHA_A,
            method=AdapterMethod.LORA,
            target_modules=("q_proj",),
            rank=4,
            alpha=8,
            dataset_manifest_sha256=_SHA_DATASET,
            tokenizer="tok",
            dependency_lock_sha256=_SHA_DEPS,
            seed=7,
            checkpoint_dir="artifacts://ckpts/run-3",
            resume_from="artifacts://ckpts/run-3/step-1000",
        )
        assert plan.checkpoint.resume_from == "artifacts://ckpts/run-3/step-1000"
        assert plan.checkpoint.start_step == 1000

    def test_plan_adaptation_qlora_sets_quantization(self) -> None:
        plan = plan_adaptation(
            base_model_digest=_SHA_A,
            method=AdapterMethod.QLORA,
            target_modules=("q_proj",),
            rank=4,
            alpha=8,
            dataset_manifest_sha256=_SHA_DATASET,
            tokenizer="tok",
            dependency_lock_sha256=_SHA_DEPS,
            seed=7,
            checkpoint_dir="artifacts://ckpts/run-q",
        )
        assert plan.manifest.method is AdapterMethod.QLORA
        assert plan.manifest.quantization is Quantization.NF4


# ---------------------------------------------------------------------------
# AIML-008 / §11 — safe-stop conditions
# ---------------------------------------------------------------------------


class TestSafeStop:
    def test_oom_stops_safely_and_preserves_checkpoint(self) -> None:
        """Spec §11 / §6.4: GPU OOM -> save verified checkpoint if possible,
        release resources, return retry plan. Partial outputs quarantined."""
        result = safe_stop(
            reason=TrainingStopReason.OOM,
            step=420,
            preserved_checkpoint="artifacts://ckpts/run-1/step-400",
        )
        assert isinstance(result, SafeStopResult)
        assert result.reason is TrainingStopReason.OOM
        assert result.preserved_checkpoint == "artifacts://ckpts/run-1/step-400"
        assert result.terminal_step == 420
        assert result.retryable is True
        # Diagnostic artifacts must be preserved (spec §6.4).
        assert len(result.diagnostics) > 0

    def test_nan_loss_stops_safely(self) -> None:
        result = safe_stop(
            reason=TrainingStopReason.NAN_INF,
            step=100,
            preserved_checkpoint="artifacts://ckpts/run-1/step-50",
        )
        assert result.reason is TrainingStopReason.NAN_INF
        # NaN/Inf is not automatically retryable with the same config — the
        # training plan must change (lower LR, gradient clipping, etc.).
        assert result.retryable is False

    def test_budget_overrun_stops_before_exceeding(self) -> None:
        """Spec §11: Budget/quota exhaustion -> Stop before overrun and return
        ``awaiting_approval`` or ``failed``."""
        result = safe_stop(
            reason=TrainingStopReason.BUDGET_OVERRUN,
            step=9000,
            preserved_checkpoint="artifacts://ckpts/run-1/step-8800",
        )
        assert result.reason is TrainingStopReason.BUDGET_OVERRUN
        assert result.awaiting_approval is True
        assert result.retryable is False

    def test_corrupt_checkpoint_is_terminal(self) -> None:
        result = safe_stop(
            reason=TrainingStopReason.CORRUPT_CHECKPOINT,
            step=500,
            preserved_checkpoint=None,
        )
        assert result.preserved_checkpoint is None
        assert result.retryable is False


# ---------------------------------------------------------------------------
# AIML-016 — Evaluation: BenchmarkResult
# ---------------------------------------------------------------------------


def _score(metric: MetricKind, value: float, higher_is_better: bool, unit: str = "") -> MetricScore:
    return MetricScore(metric=metric, value=value, higher_is_better=higher_is_better, unit=unit)


class TestBenchmarkResult:
    def test_benchmark_result_records_per_metric_scores(self) -> None:
        """Spec §6.3 / AIML-016: compare candidates on quality, safety,
        latency, cost, energy, robustness, and calibration."""
        result = BenchmarkResult(
            candidate_id="cand-A",
            suite_id="eval-v1",
            run_id="run-1",
            scores=(
                _score(MetricKind.QUALITY, 0.82, higher_is_better=True),
                _score(MetricKind.SAFETY, 0.99, higher_is_better=True),
                _score(MetricKind.LATENCY_MS, 120.0, higher_is_better=False, unit="ms"),
                _score(MetricKind.COST_USD, 0.42, higher_is_better=False, unit="usd"),
                _score(MetricKind.ENERGY_KWH, 0.08, higher_is_better=False, unit="kwh"),
                _score(MetricKind.ROBUSTNESS, 0.71, higher_is_better=True),
                _score(MetricKind.CALIBRATION, 0.04, higher_is_better=False, unit="ece"),
            ),
        )
        metrics = {s.metric for s in result.scores}
        assert MetricKind.QUALITY in metrics
        assert MetricKind.SAFETY in metrics
        assert MetricKind.LATENCY_MS in metrics
        assert MetricKind.COST_USD in metrics
        assert MetricKind.ENERGY_KWH in metrics
        assert MetricKind.ROBUSTNESS in metrics
        assert MetricKind.CALIBRATION in metrics

    def test_benchmark_result_overall_score_in_unit_range(self) -> None:
        result = BenchmarkResult(
            candidate_id="cand-A",
            suite_id="eval-v1",
            run_id="run-1",
            scores=(
                _score(MetricKind.QUALITY, 0.8, higher_is_better=True),
                _score(MetricKind.SAFETY, 0.9, higher_is_better=True),
            ),
        )
        assert 0.0 <= result.overall_score <= 1.0

    def test_metric_score_rejects_negative_value(self) -> None:
        with pytest.raises(ValueError, match="value"):
            _score(MetricKind.QUALITY, -0.1, higher_is_better=True)


# ---------------------------------------------------------------------------
# AIML-016 — EvaluationHarness: regression detection + promotion gate
# ---------------------------------------------------------------------------


class TestEvaluationHarnessRegression:
    def _candidate(
        self,
        quality: float = 0.85,
        safety: float = 0.99,
        latency_ms: float = 100.0,
    ) -> BenchmarkResult:
        return BenchmarkResult(
            candidate_id="cand",
            suite_id="eval-v1",
            run_id="r",
            scores=(
                _score(MetricKind.QUALITY, quality, higher_is_better=True),
                _score(MetricKind.SAFETY, safety, higher_is_better=True),
                _score(MetricKind.LATENCY_MS, latency_ms, higher_is_better=False, unit="ms"),
            ),
        )

    def test_compare_returns_regression_verdict_on_significant_drop(self) -> None:
        harness = EvaluationHarness(significance_threshold=0.02)
        baseline = self._candidate(quality=0.90)
        candidate = self._candidate(quality=0.80)  # 10-point drop > 2% threshold
        verdict = harness.compare(baseline, candidate)
        assert isinstance(verdict, RegressionVerdict)
        assert verdict.is_regression is True
        assert verdict.metric is MetricKind.QUALITY
        assert verdict.is_statistically_significant is True

    def test_compare_no_regression_when_drop_within_tolerance(self) -> None:
        harness = EvaluationHarness(significance_threshold=0.05)
        baseline = self._candidate(quality=0.90)
        candidate = self._candidate(quality=0.88)  # 2-point drop < 5% threshold
        verdict = harness.compare(baseline, candidate)
        assert verdict.is_regression is False

    def test_promotion_gate_blocks_on_significant_regression(self) -> None:
        """Spec §11 / §5.3: Evaluation regression -> Block promotion and retain
        current alias."""
        harness = EvaluationHarness(significance_threshold=0.02)
        baseline = self._candidate(quality=0.90)
        candidate = self._candidate(quality=0.70)  # severe regression
        decision = harness.promotion_gate(baseline, candidate)
        assert isinstance(decision, PromotionDecision)
        assert decision.promote is False
        assert len(decision.blocked_metrics) > 0

    def test_promotion_gate_allows_when_no_regression(self) -> None:
        harness = EvaluationHarness(significance_threshold=0.05)
        baseline = self._candidate(quality=0.85, safety=0.99, latency_ms=120.0)
        candidate = self._candidate(quality=0.88, safety=0.995, latency_ms=110.0)
        decision = harness.promotion_gate(baseline, candidate)
        assert decision.promote is True
        assert decision.blocked_metrics == ()

    def test_safety_regression_always_blocks_regardless_of_threshold(self) -> None:
        """Spec §5.3: 'no critical safety regression' — a safety drop is a hard
        block even when the suite-level tolerance would forgive it."""
        harness = EvaluationHarness(significance_threshold=0.5)
        baseline = self._candidate(quality=0.90, safety=0.999)
        candidate = self._candidate(quality=0.95, safety=0.95)  # safety regressed
        decision = harness.promotion_gate(baseline, candidate)
        assert decision.promote is False
        assert MetricKind.SAFETY in decision.blocked_metrics
