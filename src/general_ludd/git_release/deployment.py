"""Deployment orchestration for the ZDD protocol (spec GRC-001 §7, GRC-ZDD-001..005).

Three deployment strategies are supported (spec §4.2): canary, rolling, and
blue-green. Each evaluates the same :class:`HealthGate` (availability + error
rate + latency) per spec GRC-ZDD-003 and returns one of:

- :class:`PromoteDecision` — sample is healthy, advance traffic.
- :class:`AbortDecision` — sample violates a health bound; stop traffic shift.
- :class:`RollbackDecision` — abort + automatic rollback target resolved to the
  prior known-good digest (spec §8 "Canary regression").
- :class:`HoldDecision` — missing or stale telemetry; promotion is blocked
  (GRC-ZDD-003 "Missing or stale telemetry SHALL block promotion").

Traffic shift bounds (spec GRC-ZDD-004): each step is capped by
``max_step_percent`` and carries a minimum observation window and an abort
threshold. A manual pause does not discard the digest or release evidence —
``next_shift`` is pure and re-derivable from the current traffic percentage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "AbortDecision",
    "BlueGreenCutComplete",
    "Decision",
    "DeploymentConfig",
    "DeploymentOrchestrator",
    "DeploymentStrategy",
    "HealthGate",
    "HealthSample",
    "HoldDecision",
    "PromoteDecision",
    "RollbackDecision",
    "TrafficShift",
]


class DeploymentStrategy(StrEnum):
    """Spec §4.2 deployment strategies."""

    CANARY = "canary"
    ROLLING = "rolling"
    BLUE_GREEN = "blue_green"


@dataclass(frozen=True)
class HealthGate:
    """GRC-ZDD-003 health bounds. A sample violating any bound triggers abort."""

    max_error_rate: float
    min_availability: float
    max_latency_p99_ms: float


@dataclass(frozen=True)
class HealthSample:
    """A single telemetry observation from the candidate's serving slice."""

    availability: float
    error_rate: float
    latency_p99_ms: float


@dataclass(frozen=True)
class DeploymentConfig:
    """Operator policy for one deployment.

    ``abort_threshold`` is the fraction of samples (or magnitude, depending on
    operator convention) above which an abort becomes a rollback. The simple
    model here treats any single health-bound violation as an abort, and an
    abort whose ``error_rate`` exceeds ``abort_threshold`` as a rollback.
    """

    strategy: DeploymentStrategy
    health_gate: HealthGate
    abort_threshold: float
    max_step_percent: int = 25
    observation_window_s: int = 120


# ---------------------------------------------------------------------------
# Traffic shift planning (spec GRC-ZDD-004)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrafficShift:
    """A bounded, observable traffic change."""

    next_percent: int
    observation_window_s: int
    abort_threshold: float


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class Decision:
    """Base class for orchestrator decisions."""


@dataclass(frozen=True)
class PromoteDecision(Decision):
    """Health gate satisfied; advance to ``next_percent``."""

    next_percent: int
    digest: str


@dataclass(frozen=True)
class HoldDecision(Decision):
    """Telemetry missing or stale; block promotion without rolling back."""

    reason: str


@dataclass(frozen=True)
class AbortDecision(Decision):
    """A health bound was violated. Stop the traffic increase."""

    reason: str
    metric: str
    observed: float
    threshold: float


@dataclass(frozen=True)
class RollbackDecision(AbortDecision):
    """Abort severe enough to trigger automatic rollback (spec §8).

    The ``target_digest`` is the prior known-good digest the orchestrator was
    constructed with; restoring it is the rollback target.
    """

    target_digest: str = ""


@dataclass(frozen=True)
class BlueGreenCutComplete(Decision):
    """Blue-green has a single 100% cutover; nothing further to shift."""

    digest: str


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class DeploymentOrchestrator:
    """Evaluate health samples against the gate and plan traffic shifts.

    The orchestrator is stateless beyond its construction parameters: callers
    feed it the current traffic percentage and a sample, and it returns the
    next decision. This makes a manual pause point safe (spec GRC-ZDD-004):
    re-invoking with the same inputs always yields the same decision.
    """

    def __init__(
        self,
        *,
        config: DeploymentConfig,
        prior_digest: str,
        new_digest: str,
    ) -> None:
        """Validate strategy/digests and record the deployment inputs.

        Args:
            config: The deployment configuration (strategy + gate thresholds).
            prior_digest: The artifact digest of the currently-deployed build.
            new_digest: The artifact digest of the candidate build.

        Raises:
            ValueError: If either digest is empty or the strategy is unknown.
        """
        if not prior_digest or not new_digest:
            raise ValueError("prior_digest and new_digest are required")
        if getattr(config.strategy, "value", config.strategy) not in {m.value for m in DeploymentStrategy}:
            raise ValueError(f"unknown strategy: {config.strategy}")
        self._config = config
        self._prior_digest = prior_digest
        self._new_digest = new_digest

    @property
    def prior_digest(self) -> str:
        """The artifact digest of the currently-deployed build."""
        return self._prior_digest

    @property
    def new_digest(self) -> str:
        """The artifact digest of the candidate build being promoted."""
        return self._new_digest

    # -- health evaluation (GRC-ZDD-003) -------------------------------------

    def evaluate(self, *, stage: str, sample: HealthSample | None) -> Decision:
        """Evaluate ``sample`` against the configured :class:`HealthGate`.

        Returns:
            - :class:`HoldDecision` when ``sample`` is None (missing telemetry).
            - :class:`RollbackDecision` when a bound is violated AND the
              error rate exceeds ``abort_threshold``.
            - :class:`AbortDecision` when a bound is violated but the
              violation is below the rollback threshold.
            - :class:`PromoteDecision` when every bound holds.
        """
        del stage  # reserved for future stage-specific gate overrides
        if sample is None:
            return HoldDecision(reason="missing-or-stale-telemetry")

        gate = self._config.health_gate
        if sample.error_rate > gate.max_error_rate:
            return self._abort(
                metric="error_rate",
                observed=sample.error_rate,
                threshold=gate.max_error_rate,
            )
        if sample.availability < gate.min_availability:
            return self._abort(
                metric="availability",
                observed=sample.availability,
                threshold=gate.min_availability,
            )
        if sample.latency_p99_ms > gate.max_latency_p99_ms:
            return self._abort(
                metric="latency_p99_ms",
                observed=sample.latency_p99_ms,
                threshold=gate.max_latency_p99_ms,
            )
        return PromoteDecision(next_percent=self._next_percent(0), digest=self._new_digest)

    # -- traffic shift planning (GRC-ZDD-004) --------------------------------

    def next_shift(self, *, current_percent: int) -> TrafficShift:
        """Return the next bounded traffic shift from ``current_percent``."""
        if self._config.strategy is DeploymentStrategy.BLUE_GREEN:
            # Blue-green cuts over in one step.
            return TrafficShift(
                next_percent=100,
                observation_window_s=self._config.observation_window_s,
                abort_threshold=self._config.abort_threshold,
            )
        return TrafficShift(
            next_percent=self._next_percent(current_percent),
            observation_window_s=self._config.observation_window_s,
            abort_threshold=self._config.abort_threshold,
        )

    # -- internals -----------------------------------------------------------

    def _next_percent(self, current_percent: int) -> int:
        step = self._config.max_step_percent
        if self._config.strategy is DeploymentStrategy.BLUE_GREEN:
            return 100
        next_pct = current_percent + step
        if next_pct >= 100:
            return 100
        return next_pct

    def _abort(self, *, metric: str, observed: float, threshold: float) -> Decision:
        reason = f"{metric} {observed} violates threshold {threshold}"
        # Spec §8 "Canary regression": a severe regression rolls back
        # automatically. We use error_rate as the rollback trigger because it
        # is the most direct signal of user-visible failure; latency and
        # availability violations stop the shift but do not roll back on
        # their own (an operator may still triage).
        if metric == "error_rate" and observed >= self._config.abort_threshold:
            return RollbackDecision(
                reason=reason,
                metric=metric,
                observed=observed,
                threshold=threshold,
                target_digest=self._prior_digest,
            )
        return AbortDecision(
            reason=reason,
            metric=metric,
            observed=observed,
            threshold=threshold,
        )
