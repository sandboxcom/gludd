"""AIML Phase C — evaluation suite: candidate comparison + regression gate.

Implements capability AIML-016 from docs/specs/FEATURE_AI_ML_EXPERT.md:

    Compare candidates on quality, safety, latency, cost, energy,
    robustness, and calibration. (§2 AIML-016 row)

This module provides the *runner* side of evaluation:

  - :class:`MetricScore` and :class:`BenchmarkResult` — per-candidate
    per-metric scores with units and direction (higher-is-better vs.
    lower-is-better).
  - :class:`EvaluationHarness` — compares a baseline candidate against a
    candidate, computes per-metric regressions, and gates promotion.
  - :class:`RegressionVerdict` / :class:`PromotionDecision` — typed
    outputs so promotion can be mechanically blocked (spec §11: "Evaluation
    regression -> Block promotion and retain current alias").

The :class:`EvaluationSuite` registry record (spec §4.3) lives in
``general_ludd.ai_ml.registries`` and represents the *pinned* suite
descriptor. This module's :class:`EvaluationHarness` is the runner that
applies a suite to candidates. The two collaborate: a suite record pins
which metrics, slices, and tolerances the harness uses; the harness
produces :class:`BenchmarkResult` records that cite the suite via
``suite_id``.

Promotion gate rules (spec §5.3, §11):

  - A statistically significant regression on any metric beyond the
    suite-specific tolerance blocks promotion.
  - A safety regression ALWAYS blocks, regardless of tolerance — "no
    critical safety regression" is a non-negotiable gate (spec §5.3).
  - The current alias is retained when promotion is blocked.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MetricKind(enum.StrEnum):
    """Candidate-comparison metric families (AIML-016, spec §13 metrics).

    The seven core families required by the spec:
      - ``QUALITY``       — task quality (accuracy/F1/bleu/rouge/etc.).
      - ``SAFETY``        — safety pass rate on the suite's safety slice.
      - ``LATENCY_MS``    — serving latency (lower is better).
      - ``COST_USD``      — per-request or per-run USD cost (lower is better).
      - ``ENERGY_KWH``    — energy proxy (lower is better).
      - ``ROBUSTNESS``    — adversarial / OOD robustness (higher is better).
      - ``CALIBRATION``   — calibration error, e.g. ECE (lower is better).

    Latency/cost/energy/calibration are "lower is better" metrics; the
    harness inverts their delta when computing regressions.
    """

    QUALITY = "quality"
    SAFETY = "safety"
    LATENCY_MS = "latency_ms"
    COST_USD = "cost_usd"
    ENERGY_KWH = "energy_kwh"
    ROBUSTNESS = "robustness"
    CALIBRATION = "calibration"


# Metrics where a regression is ALWAYS a hard promotion block, regardless of
# the suite-level significance threshold. Spec §5.3: "no critical safety
# regression" is a non-negotiable gate.
_HARD_BLOCK_METRICS: frozenset[MetricKind] = frozenset({MetricKind.SAFETY})


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricScore:
    """A single per-metric score for a candidate (spec §13 metrics).

    ``value`` must be non-negative. ``higher_is_better`` encodes metric
    direction so the harness can compute signed deltas uniformly:
    a positive delta is *good* for higher-is-better metrics and *bad* for
    lower-is-better metrics.
    """

    metric: MetricKind
    value: float
    higher_is_better: bool
    unit: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.metric, MetricKind):
            object.__setattr__(
                self,
                "metric",
                MetricKind(self.metric) if isinstance(self.metric, str) else MetricKind.QUALITY,
            )
        if not isinstance(self.value, int | float) or self.value < 0:
            raise ValueError(f"value must be a non-negative number, got {self.value!r}")


@dataclass(frozen=True)
class BenchmarkResult:
    """A candidate's per-metric scores on a pinned suite (AIML-016).

    ``overall_score`` is a normalized composite in ``[0.0, 1.0]``: the
    mean of per-metric scores after inverting lower-is-better metrics so
    that higher always means better. A single :class:`BenchmarkResult`
    never mixes candidates — the harness compares two results.
    """

    candidate_id: str
    suite_id: str
    run_id: str
    scores: tuple[MetricScore, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be a non-empty string")
        if not isinstance(self.suite_id, str) or not self.suite_id.strip():
            raise ValueError("suite_id must be a non-empty string")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not self.scores:
            raise ValueError("scores must contain at least one MetricScore")

    @property
    def overall_score(self) -> float:
        """Normalized composite in ``[0.0, 1.0]``.

        For each metric, we normalize to a "higher is better" score in
        ``[0, 1]``. Lower-is-better metrics are inverted via ``1 - value``
        when value is already in ``[0, 1]``; otherwise we use a softmax-free
        rank-preserving transform (1 / (1 + value)). The mean across
        normalized scores is the composite. Higher is always better.
        """
        normalized: list[float] = []
        for s in self.scores:
            if s.higher_is_better:
                # For higher-is-better metrics in [0, 1], value is already
                # a normalized score. For values > 1 (e.g. perplexity inv?),
                # squash with 1/(1+value).
                normalized.append(s.value if s.value <= 1.0 else 1.0 / (1.0 + s.value))
            else:
                # Lower is better — invert so higher means better.
                if s.value <= 1.0:
                    normalized.append(1.0 - s.value)
                else:
                    normalized.append(1.0 / (1.0 + s.value))
        if not normalized:
            return 0.0
        return sum(normalized) / len(normalized)

    def score_for(self, metric: MetricKind) -> MetricScore | None:
        for s in self.scores:
            if s.metric is metric:
                return s
        return None


@dataclass(frozen=True)
class RegressionVerdict:
    """Per-comparison regression verdict for one metric.

    ``delta`` is signed so that negative = regression for higher-is-better
    metrics and positive = regression for lower-is-better metrics (the
    harness inverts direction before computing ``delta``).
    """

    metric: MetricKind
    baseline_value: float
    candidate_value: float
    delta: float
    is_regression: bool
    is_statistically_significant: bool


@dataclass(frozen=True)
class PromotionDecision:
    """Promotion verdict: ``promote`` + the metrics that blocked it (if any)."""

    promote: bool
    blocked_metrics: tuple[MetricKind, ...] = ()
    regressions: tuple[RegressionVerdict, ...] = ()
    reason: str = ""


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass
class EvaluationHarness:
    """Compare candidates and gate promotion (AIML-016, spec §5.3/§11).

    Parameters:
      significance_threshold: the minimum absolute regression (in the
        metric's native scale) that counts as "statistically significant".
        Spec §5.3: "no statistically significant regression beyond the
        suite-specific tolerance".
      hard_block_metrics: metrics whose regression ALWAYS blocks promotion,
        regardless of threshold. Defaults to :data:`_HARD_BLOCK_METRICS`
        (safety). Spec §5.3: "no critical safety regression".
    """

    significance_threshold: float = 0.02
    hard_block_metrics: frozenset[MetricKind] = field(default_factory=lambda: _HARD_BLOCK_METRICS)

    def __post_init__(self) -> None:
        if self.significance_threshold < 0:
            raise ValueError(f"significance_threshold must be >= 0, got {self.significance_threshold}")

    def compare(self, baseline: BenchmarkResult, candidate: BenchmarkResult) -> RegressionVerdict:
        """Compare two candidates and return the worst regression verdict.

        Returns the regression verdict for the metric with the largest
        significant regression. If no metric regressed significantly,
        returns a verdict with ``is_regression=False`` for the metric that
        moved the most.
        """
        verdicts = self._all_verdicts(baseline, candidate)
        if not verdicts:
            # No overlapping metrics — nothing to compare.
            return RegressionVerdict(
                metric=MetricKind.QUALITY,
                baseline_value=0.0,
                candidate_value=0.0,
                delta=0.0,
                is_regression=False,
                is_statistically_significant=False,
            )

        # Worst regression = the significant one with the largest |delta|.
        # If no verdict is significant, return the largest-movement one but
        # mark it non-regression.
        significant = [v for v in verdicts if v.is_statistically_significant]
        pool = significant if significant else verdicts
        worst = max(pool, key=lambda v: abs(v.delta))
        if not significant:
            return RegressionVerdict(
                metric=worst.metric,
                baseline_value=worst.baseline_value,
                candidate_value=worst.candidate_value,
                delta=worst.delta,
                is_regression=False,
                is_statistically_significant=False,
            )
        return worst

    def promotion_gate(
        self,
        baseline: BenchmarkResult,
        candidate: BenchmarkResult,
    ) -> PromotionDecision:
        """Decide whether a candidate may be promoted (spec §5.3, §11).

        Blocks when ANY metric regresses significantly, OR when ANY hard-block
        metric regresses at all (e.g. safety). Spec §11: "Evaluation
        regression -> Block promotion and retain current alias."
        """
        verdicts = self._all_verdicts(baseline, candidate)
        blocked: list[MetricKind] = []
        regressions: list[RegressionVerdict] = []

        for v in verdicts:
            is_hard_block = v.metric in self.hard_block_metrics
            if v.is_regression and (v.is_statistically_significant or is_hard_block):
                blocked.append(v.metric)
                regressions.append(v)

        if blocked:
            return PromotionDecision(
                promote=False,
                blocked_metrics=tuple(blocked),
                regressions=tuple(regressions),
                reason=(
                    f"promotion blocked: {len(blocked)} metric(s) regressed "
                    f"({', '.join(sorted(m.value for m in blocked))})"
                ),
            )
        return PromotionDecision(promote=True, regressions=tuple(regressions))

    def run_benchmark(
        self,
        candidate_id: str,
        suite_id: str,
        scores: tuple[MetricScore, ...],
    ) -> BenchmarkResult:
        """Wrap caller-supplied scores in a :class:`BenchmarkResult`.

        In production the harness invokes the suite's actual evaluators
        (task, safety, robustness, latency, cost, calibration); this typed
        entry point is the contract the runner plugs into. The run_id is
        always unique so two runs over the same candidate are distinct.
        """
        return BenchmarkResult(
            candidate_id=candidate_id,
            suite_id=suite_id,
            run_id=f"eval-{uuid.uuid4().hex[:16]}",
            scores=scores,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _all_verdicts(
        self,
        baseline: BenchmarkResult,
        candidate: BenchmarkResult,
    ) -> list[RegressionVerdict]:
        verdicts: list[RegressionVerdict] = []
        for cand_score in candidate.scores:
            base_score = baseline.score_for(cand_score.metric)
            if base_score is None:
                continue
            verdicts.append(self._one_verdict(base_score, cand_score))
        return verdicts

    def _one_verdict(self, baseline: MetricScore, candidate: MetricScore) -> RegressionVerdict:
        if baseline.metric is not candidate.metric:
            raise ValueError(f"metric mismatch: baseline={baseline.metric!r}, candidate={candidate.metric!r}")
        # Signed delta so negative = regression for higher-is-better,
        # positive = regression for lower-is-better.
        if candidate.higher_is_better:
            delta = candidate.value - baseline.value
            is_regression = candidate.value < baseline.value
        else:
            delta = baseline.value - candidate.value
            is_regression = candidate.value > baseline.value

        is_significant = is_regression and abs(delta) >= self.significance_threshold
        return RegressionVerdict(
            metric=candidate.metric,
            baseline_value=baseline.value,
            candidate_value=candidate.value,
            delta=delta,
            is_regression=is_regression,
            is_statistically_significant=is_significant,
        )


__all__ = [
    "BenchmarkResult",
    "EvaluationHarness",
    "MetricKind",
    "MetricScore",
    "PromotionDecision",
    "RegressionVerdict",
]
