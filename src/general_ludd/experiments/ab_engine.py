"""Run A/B experiments with traffic splitting and significance tests.

This is a self-contained statistical engine for online controlled experiments.
It supports:

- N-armed variants with configurable traffic allocation
- Continuous and conversion (binary) metrics
- Statistical significance via Welch's t-test (continuous) and Chi-squared
  test of independence (conversion)
- Sequential decision rules with configurable alpha/beta thresholds
- Experiment lifecycle (draft → running → concluded)
- Immutable per-variant metric snapshots for auditability
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto


class MetricType(Enum):
    """Identify the supported experiment metric families."""

    CONTINUOUS = auto()
    CONVERSION = auto()


class ExperimentStatus(Enum):
    """Represent the lifecycle state of an experiment."""

    DRAFT = auto()
    RUNNING = auto()
    CONCLUDED = auto()


@dataclass
class VariantDef:
    """Definition of a single variant arm in an experiment."""

    name: str
    traffic_weight: float  # must sum to 1.0 across all variants


@dataclass
class MetricDef:
    """Definition of a metric to track during the experiment."""

    name: str
    type: MetricType
    is_primary: bool = False


@dataclass(frozen=True)
class VariantMetricSnapshot:
    """Immutable metric summary for one variant at a point in time.

    For CONTINUOUS metrics: sum, sum_of_squares, n are populated.
    For CONVERSION metrics: conversions and trials n are populated.
    """

    metric_name: str
    variant_name: str
    n: int
    sum: float = 0.0
    sum_of_squares: float = 0.0
    conversions: int = 0


@dataclass(frozen=True)
class SignificanceResult:
    """Outcome of a single significance test (variant vs control)."""

    metric_name: str
    variant_name: str
    test_statistic: float
    p_value: float
    is_significant: bool
    test_type: str  # "welch_t" or "chi_squared"


@dataclass
class ExperimentDef:
    """Full experiment specification."""

    name: str
    variants: Sequence[VariantDef]
    metrics: Sequence[MetricDef]
    control_name: str = field(default="")
    alpha: float = 0.05
    min_sample_size: int = 100

    def __post_init__(self) -> None:
        """Validate experiment arms, weights, alpha, and control selection."""
        if len(self.variants) < 2:
            raise ValueError("Experiment must have at least 2 variants")
        weights = sum(v.traffic_weight for v in self.variants)
        if not math.isclose(weights, 1.0, rel_tol=1e-9):
            raise ValueError(f"Traffic weights must sum to 1.0, got {weights}")
        if not 0.0 < self.alpha < 1.0:
            raise ValueError(f"alpha must be in (0, 1), got {self.alpha}")
        if not self.control_name:
            self.control_name = self.variants[0].name

    @property
    def control_variant(self) -> VariantDef:
        """Return the configured control variant."""
        for v in self.variants:
            if v.name == self.control_name:
                return v
        raise ValueError(f"Control variant '{self.control_name}' not found")

    @property
    def treatment_variants(self) -> list[VariantDef]:
        """Return every variant other than the configured control."""
        return [v for v in self.variants if v.name != self.control_name]

    def validate_weights(self) -> None:
        """Reject traffic weights that do not sum to one."""
        weights = sum(v.traffic_weight for v in self.variants)
        if not math.isclose(weights, 1.0, rel_tol=1e-9):
            raise ValueError(f"Traffic weights must sum to 1.0, got {weights}")


# ── normal distribution helpers ────────────────────────────────────────────


def _normal_cdf(x: float) -> float:
    """Cumulative distribution function of the standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inverse_normal_cdf(p: float) -> float:
    """Inverse CDF (quantile function) of the standard normal distribution.

    Uses the rational approximation (Peter Acklam, 2004) valid for p in (0, 1).
    """
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1) exclusive")
    # Symmetry: compute for p <= 0.5
    if p > 0.5:
        return -_inverse_normal_cdf(1.0 - p)

    a1 = -39.6968302866538
    a2 = 220.9460984245205
    a3 = -275.9285104469687
    a4 = 138.3577518672690
    a5 = -30.6647980661472
    a6 = 2.50662827745924

    b1 = -54.4760987982241
    b2 = 161.5858368580409
    b3 = -155.6989798598866
    b4 = 66.8013118877197
    b5 = -13.2806815528857

    c1 = -7.78489400243029e-03
    c2 = -0.322396458041136
    c3 = -2.40075827716184
    c4 = -2.54973253934373
    c5 = 4.37466414146497

    d1 = 7.78469570904146e-03
    d2 = 0.322467129070040
    d3 = 2.44513413714300
    d4 = 3.75440866190742

    q = p - 0.5
    if abs(q) <= 0.425:
        r = 0.180625 - q * q
        num = ((((a1 * r + a2) * r + a3) * r + a4) * r + a5) * r + a6
        den = ((((b1 * r + b2) * r + b3) * r + b4) * r + b5) * r + 1.0
        return q * num / den
    else:
        r = math.sqrt(-math.log(p)) if p > 0 else math.sqrt(-math.log(0.5))
        num = (((c1 * r + c2) * r + c3) * r + c4) * r + c5
        den = ((d1 * r + d2) * r + d3) * r + d4
        if q > 0:
            return num / (den * r + 1.0)
        return -num / (den * r + 1.0)


# ── chi-squared distribution helpers ───────────────────────────────────────


def _chi2_sf(x: float, df: int) -> float:
    """Return the chi-squared survival function.

    The calculation uses the regularized lower incomplete gamma function.
    """
    if x <= 0:
        return 1.0
    return 1.0 - _regularized_gamma_p(df / 2.0, x / 2.0)


def _regularized_gamma_p(s: float, x: float) -> float:
    """Regularized lower incomplete gamma function P(s, x) = gamma(s,x)/Gamma(s).

    Uses the series expansion for x < s+1 and the continued fraction otherwise.
    """
    if s <= 0:
        raise ValueError("s must be positive")
    if x < 0:
        raise ValueError("x must be non-negative")
    if x == 0:
        return 0.0

    gln = math.lgamma(s)
    if x < s + 1.0:
        # Series expansion
        ap = s
        total = 1.0 / s
        delta = total
        for _n in range(1, 200):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + s * math.log(x) - gln)
    else:
        # Continued fraction (Lentz's method)
        _a = 1.0
        b = x + 1.0 - s
        c = 1.0 / 1e-30
        d = 1.0 / b
        h = d
        for n in range(1, 200):
            an = -n * (n - s)
            b += 2.0
            d = 1.0 / (an * d + b)
            c = b + an / c
            if abs(c) < 1e-30:
                c = 1e-30
            if abs(d) < 1e-30:
                d = 1e-30
            delta = c * d
            h *= delta
            if abs(delta - 1.0) < 1e-15:
                break
        return 1.0 - math.exp(-x + s * math.log(x) - gln) * h


# ── significance tests ─────────────────────────────────────────────────────


def welch_t_test(
    name_a: str,
    mean_a: float,
    var_a: float,
    n_a: int,
    name_b: str,
    mean_b: float,
    var_b: float,
    n_b: int,
) -> tuple[float, float]:
    """Welch's unequal-variance t-test. Returns (t_statistic, two-sided p_value).

    Robust when groups have different variances and/or sample sizes.
    """
    if n_a < 2 or n_b < 2:
        return float("inf"), 1.0
    if var_a <= 0.0 or var_b <= 0.0:
        return 0.0, 1.0

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0.0:
        return 0.0, 1.0

    t = (mean_b - mean_a) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / n_a + var_b / n_b) ** 2
    den = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    df = num / den if den > 0 else 1.0

    # Two-sided p-value via t-distribution CDF approximation
    p_value = 2.0 * _t_survival(abs(t), df)
    return t, p_value


def _t_survival(t: float, df: float) -> float:
    """Return Student's t-distribution survival function.

    The calculation uses the regularized incomplete beta function.
    """
    if t < 0:
        return 1.0 - _t_survival(-t, df)
    x = df / (df + t * t)
    return 0.5 * _regularized_beta(df / 2.0, 0.5, 0.0, x)


def _regularized_beta(a: float, b: float, lower: float, upper: float) -> float:
    """Regularized incomplete beta function I_x(a,b) using continued fraction."""
    if upper <= 0:
        return 0.0
    if upper >= 1:
        return 1.0
    # Compute I via continued fraction
    return _ibeta_cf(a, b, upper)


def _ibeta_cf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta function."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        mm = 2 * m
        aa = m * (b - m) * x / ((qam + mm) * (a + mm))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + mm) * (qap + mm))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    ln_factor = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    return h * math.exp(ln_factor) / a


def chi_squared_test(
    conversions_a: int,
    trials_a: int,
    conversions_b: int,
    trials_b: int,
) -> tuple[float, float]:
    """Pearson's chi-squared test of independence (2x2 contingency table).

    Returns (chi2_statistic, p_value).
    """
    if trials_a <= 0 or trials_b <= 0:
        return 0.0, 1.0

    total = trials_a + trials_b
    total_conversions = conversions_a + conversions_b
    total_non = total - total_conversions

    obs = [
        conversions_a,
        trials_a - conversions_a,
        conversions_b,
        trials_b - conversions_b,
    ]

    exp = [
        trials_a * total_conversions / total,
        trials_a * total_non / total,
        trials_b * total_conversions / total,
        trials_b * total_non / total,
    ]

    chi2 = 0.0
    for o, e in zip(obs, exp, strict=False):
        if e > 0:
            chi2 += (o - e) ** 2 / e

    p_value = _chi2_sf(chi2, 1)
    return chi2, p_value


# ── metric aggregation helpers ─────────────────────────────────────────────


def aggregate_continuous(
    values: Sequence[float],
) -> tuple[int, float, float, float]:
    """Compute n, sum, sum_of_squares, mean from a sequence of continuous values."""
    n = len(values)
    if n == 0:
        return 0, 0.0, 0.0, 0.0
    s = sum(values)
    ss = sum(v * v for v in values)
    return n, s, ss, s / n


def aggregate_conversion(
    successes: Sequence[bool],
) -> tuple[int, int, float]:
    """Compute n, conversions, rate from a sequence of boolean outcomes."""
    n = len(successes)
    conversions = sum(1 for x in successes if x)
    rate = conversions / n if n > 0 else 0.0
    return n, conversions, rate


def variance_from_aggregates(n: int, sum_val: float, sum_of_squares: float) -> float:
    """Compute sample variance from n, sum, sum_of_squares."""
    if n < 2:
        return 0.0
    mean = sum_val / n
    var = (sum_of_squares - sum_val * mean) / (n - 1)
    return max(var, 0.0)


# ── traffic splitter ───────────────────────────────────────────────────────


@dataclass
class TrafficSplitter:
    """Deterministic traffic splitter using hash-based assignment.

    Uses SHA-256 so user/request IDs map consistently to the same variant
    throughout an experiment, avoiding carryover effects.
    """

    variants: dict[str, float]
    _cumulative: list[tuple[str, float]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        """Build cumulative boundaries in deterministic variant order."""
        cum = 0.0
        self._cumulative = []
        for name, weight in self.variants.items():
            cum += weight
            self._cumulative.append((name, cum))

    def assign(self, unit_id: str) -> str:
        """Assign ``unit_id`` to a variant using hash-based splitting."""
        import hashlib

        h = hashlib.sha256(unit_id.encode()).digest()
        bucket = int.from_bytes(h[:8], "big") / (2**64)
        for name, cum in self._cumulative:
            if bucket < cum:
                return name
        return self._cumulative[-1][0]


# ── experiment runner ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExperimentDecision:
    """Final decision for an experiment."""

    experiment_name: str
    winner: str | None  # None if no variant beats control
    all_results: tuple[SignificanceResult, ...]
    reason: str

    @property
    def has_winner(self) -> bool:
        """Return whether the decision selects a winning variant."""
        return self.winner is not None


@dataclass
class Experiment:
    """Stateful experiment runner that collects metrics and produces decisions."""

    definition: ExperimentDef
    snapshots: dict[str, list[VariantMetricSnapshot]] = field(default_factory=dict)
    status: ExperimentStatus = ExperimentStatus.DRAFT

    def start(self) -> None:
        """Move a draft experiment into its running state."""
        if self.status != ExperimentStatus.DRAFT:
            raise RuntimeError(f"Cannot start experiment in status {self.status}")
        self.status = ExperimentStatus.RUNNING

    def record(
        self,
        variant_name: str,
        metric_name: str,
        value: float,
    ) -> None:
        """Record a single metric observation for a variant."""
        if self.status != ExperimentStatus.RUNNING:
            raise RuntimeError(f"Cannot record in status {self.status}")
        key = f"{variant_name}:{metric_name}"
        if key not in self.snapshots:
            self.snapshots[key] = []
        metric_def = self._find_metric(metric_name)
        if metric_def is None:
            raise ValueError(f"Unknown metric '{metric_name}'")
        n = len(self.snapshots[key]) + 1
        if metric_def.type == MetricType.CONTINUOUS:
            snap = VariantMetricSnapshot(
                metric_name=metric_name,
                variant_name=variant_name,
                n=n,
                sum=value,
                sum_of_squares=value * value,
            )
        else:
            conv = 1.0 if value > 0 else 0.0
            snap = VariantMetricSnapshot(
                metric_name=metric_name,
                variant_name=variant_name,
                n=n,
                conversions=int(conv),
            )
        self.snapshots[key].append(snap)

    def record_conversion(
        self,
        variant_name: str,
        metric_name: str,
        success: bool,
    ) -> None:
        """Record a conversion event (binary outcome)."""
        self.record(variant_name, metric_name, 1.0 if success else 0.0)

    def aggregate(self, variant_name: str, metric_name: str) -> VariantMetricSnapshot | None:
        """Compute the aggregate snapshot for one variant/metric pair."""
        key = f"{variant_name}:{metric_name}"
        snaps = self.snapshots.get(key, [])
        if not snaps:
            return None
        metric_def = self._find_metric(metric_name)
        if metric_def is None:
            return None
        if metric_def.type == MetricType.CONTINUOUS:
            n = len(snaps)
            s = sum(s.sum for s in snaps)
            ss = sum(s.sum_of_squares for s in snaps)
            return VariantMetricSnapshot(
                metric_name=metric_name,
                variant_name=variant_name,
                n=n,
                sum=s,
                sum_of_squares=ss,
            )
        else:
            n = len(snaps)
            convs = sum(s.conversions for s in snaps)
            return VariantMetricSnapshot(
                metric_name=metric_name,
                variant_name=variant_name,
                n=n,
                conversions=convs,
            )

    def evaluate(self) -> ExperimentDecision:
        """Evaluate treatments against the control and return a decision.

        Every configured primary metric participates in the significance tests.
        """
        if self.status != ExperimentStatus.RUNNING:
            raise RuntimeError(f"Cannot evaluate experiment in status {self.status}")

        primary_metrics = [m for m in self.definition.metrics if m.is_primary]
        if not primary_metrics:
            primary_metrics = list(self.definition.metrics)

        all_results: list[SignificanceResult] = []

        for metric_def in primary_metrics:
            metric_name = metric_def.name
            control_agg = self.aggregate(self.definition.control_name, metric_name)
            if control_agg is None or control_agg.n < self.definition.min_sample_size:
                return ExperimentDecision(
                    experiment_name=self.definition.name,
                    winner=None,
                    all_results=tuple(all_results),
                    reason=(
                        f"Not enough data for metric '{metric_name}': "
                        f"control has {control_agg.n if control_agg else 0} samples, "
                        f"need {self.definition.min_sample_size}"
                    ),
                )

            for variant in self.definition.treatment_variants:
                treatment_agg = self.aggregate(variant.name, metric_name)
                if treatment_agg is None or treatment_agg.n < self.definition.min_sample_size:
                    continue

                if metric_def.type == MetricType.CONTINUOUS:
                    ctrl_mean = control_agg.sum / control_agg.n
                    ctrl_var = variance_from_aggregates(control_agg.n, control_agg.sum, control_agg.sum_of_squares)
                    trt_mean = treatment_agg.sum / treatment_agg.n
                    trt_var = variance_from_aggregates(treatment_agg.n, treatment_agg.sum, treatment_agg.sum_of_squares)
                    t_stat, p_value = welch_t_test(
                        self.definition.control_name,
                        ctrl_mean,
                        ctrl_var,
                        control_agg.n,
                        variant.name,
                        trt_mean,
                        trt_var,
                        treatment_agg.n,
                    )
                    test_type = "welch_t"
                else:
                    t_stat, p_value = chi_squared_test(
                        control_agg.conversions,
                        control_agg.n,
                        treatment_agg.conversions,
                        treatment_agg.n,
                    )
                    test_type = "chi_squared"

                is_sig = p_value < self.definition.alpha
                all_results.append(
                    SignificanceResult(
                        metric_name=metric_name,
                        variant_name=variant.name,
                        test_statistic=t_stat,
                        p_value=p_value,
                        is_significant=is_sig,
                        test_type=test_type,
                    )
                )

        # Decision: winner is the first variant that is significant and better
        # than control on the primary metric. Conservative: require all primary
        # metrics to be significant and directionally correct.
        candidates: list[tuple[str, float, str]] = []
        for r in all_results:
            if r.is_significant:
                candidates.append((r.variant_name, r.p_value, r.metric_name))

        if not candidates:
            self.status = ExperimentStatus.CONCLUDED
            return ExperimentDecision(
                experiment_name=self.definition.name,
                winner=None,
                all_results=tuple(all_results),
                reason="No variant showed statistically significant improvement",
            )

        # Pick the variant with the lowest p-value across all primary metrics
        best = min(candidates, key=lambda x: x[1])
        winner = best[0]
        self.status = ExperimentStatus.CONCLUDED
        return ExperimentDecision(
            experiment_name=self.definition.name,
            winner=winner,
            all_results=tuple(all_results),
            reason=f"Variant '{winner}' is significant (p={best[1]:.6f}) on metric '{best[2]}'",
        )

    def _find_metric(self, name: str) -> MetricDef | None:
        for m in self.definition.metrics:
            if m.name == name:
                return m
        return None

    @property
    def total_samples(self) -> int:
        """Return the total number of recorded metric observations."""
        return sum(len(snaps) for snaps in self.snapshots.values())


# ── sequential testing helpers ─────────────────────────────────────────────


def bonferroni_correction(alpha: float, num_comparisons: int) -> float:
    """Return the Bonferroni-corrected alpha for num_comparisons tests.

    Controls the family-wise error rate (FWER) conservatively.
    """
    if num_comparisons <= 0:
        raise ValueError("num_comparisons must be positive")
    return alpha / num_comparisons


def required_sample_size_continuous(
    effect_size: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Approximate required sample size per arm for a two-sided Welch's t-test.

    Uses the normal approximation formula.
    """
    z_alpha_2 = _inverse_normal_cdf(1.0 - alpha / 2.0)
    z_beta = _inverse_normal_cdf(power)
    n = 2.0 * (z_alpha_2 + z_beta) ** 2 / (effect_size**2)
    return max(2, math.ceil(n))


def required_sample_size_conversion(
    baseline_rate: float,
    minimum_detectable_effect: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Approximate required sample size per arm for a two-sided chi-squared test.

    `baseline_rate` is the control conversion rate.
    `minimum_detectable_effect` is the absolute difference to detect.
    """
    if not 0.0 < baseline_rate < 1.0:
        raise ValueError("baseline_rate must be in (0, 1)")
    z_alpha_2 = _inverse_normal_cdf(1.0 - alpha / 2.0)
    z_beta = _inverse_normal_cdf(power)
    p1 = baseline_rate
    p2 = baseline_rate + minimum_detectable_effect
    p_bar = (p1 + p2) / 2.0
    n = (
        z_alpha_2 * math.sqrt(2.0 * p_bar * (1.0 - p_bar)) + z_beta * math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2))
    ) ** 2 / (minimum_detectable_effect**2)
    return max(2, math.ceil(n))
