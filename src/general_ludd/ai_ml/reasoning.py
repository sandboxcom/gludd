"""Reasoning engine: plan/act/observe/verify state machine (AIML-007).

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §6.3 (Reasoning and verification):

  - A plan/act/observe/verify state machine with typed, bounded steps.
  - Externally visible reasoning is a concise rationale plus verifiable
    artifacts, not private token-level chain-of-thought.
  - Math and science answers preserve units, significant figures, assumptions,
    boundary conditions, and uncertainty.
  - At least one independent check is required for high-impact numerical
    answers (alternative solver, dimensional analysis, conserved quantity,
    known limiting case, benchmark dataset, or human approval). Failed checks
    produce ``degraded`` or ``failed``, never a confident answer.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass

from general_ludd.ai_ml.schemas import ResultStatus, Uncertainty, VerificationStatus


class ReasoningPhase(enum.StrEnum):
    """Phases of the plan/act/observe/verify state machine (spec §6.3)."""

    PLAN = "plan"
    ACT = "act"
    OBSERVE = "observe"
    VERIFY = "verify"
    TERMINAL = "terminal"


class IndependentCheckKind(enum.StrEnum):
    """Kinds of independent verification checks (spec §6.3).

    At least one is required for high-impact numerical answers.
    """

    ALTERNATIVE_SOLVER = "alternative_solver"
    DIMENSIONAL_ANALYSIS = "dimensional_analysis"
    CONSERVED_QUANTITY = "conserved_quantity"
    LIMITING_CASE = "limiting_case"
    BENCHMARK = "benchmark"
    HUMAN_APPROVAL = "human_approval"


@dataclass(frozen=True)
class StepArtifact:
    """A typed, bounded reasoning step (spec §6.3).

    Externally visible reasoning is a concise rationale plus a verifiable
    artifact URI — never private token-level chain-of-thought. Each step
    records which tool was invoked, a human-readable rationale, and an
    optional machine-checkable artifact.
    """

    phase: ReasoningPhase
    step_index: int
    tool: str
    rationale: str
    artifact_uri: str | None = None
    timestamp: int = 0

    def __post_init__(self) -> None:
        if self.step_index < 0:
            raise ValueError(f"step_index must be >= 0, got {self.step_index}")
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise ValueError("tool must be a non-empty string")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale must be a non-empty string")


@dataclass(frozen=True)
class NumericalAnswer:
    """A numerical answer that preserves units, sig figs, and uncertainty.

    Spec §6.3: "Math and science answers must preserve units, significant
    figures, assumptions, boundary conditions, and uncertainty."
    """

    value: float
    unit: str
    significant_figures: int
    uncertainty: float
    assumptions: tuple[str, ...] = ()
    boundary_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise ValueError("unit must be a non-empty string")
        if self.significant_figures < 1:
            raise ValueError(f"significant_figures must be >= 1, got {self.significant_figures}")
        if self.uncertainty < 0:
            raise ValueError(f"uncertainty must be >= 0, got {self.uncertainty}")


@dataclass(frozen=True)
class IndependentCheck:
    """One independent verification check (spec §6.3).

    Required for high-impact numerical answers. A failing check downgrades
    the result to ``degraded`` or ``failed``.
    """

    kind: IndependentCheckKind
    status: VerificationStatus
    artifact_uri: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IndependentCheckKind):
            try:
                object.__setattr__(
                    self,
                    "kind",
                    IndependentCheckKind(self.kind),
                )
            except ValueError as exc:
                raise ValueError(f"invalid kind: {self.kind!r}") from exc
        if not isinstance(self.status, VerificationStatus):
            try:
                object.__setattr__(
                    self,
                    "status",
                    VerificationStatus(self.status),
                )
            except ValueError as exc:
                raise ValueError(f"invalid status: {self.status!r}") from exc


@dataclass(frozen=True)
class ReasoningResult:
    """Terminal result of a reasoning episode (spec §6.3).

    Carries the concise rationale, all typed step artifacts, the independent
    checks that were run, the query rewrite used for retrieval, the retrieved
    source IDs, and uncertainty calibration.
    """

    status: ResultStatus
    answer: NumericalAnswer | None
    rationale: str
    steps: tuple[StepArtifact, ...]
    independent_checks: tuple[IndependentCheck, ...]
    query_rewrite: str
    retrieved_source_ids: tuple[str, ...]
    uncertainty: Uncertainty

    def __post_init__(self) -> None:
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale must be a non-empty string")
        if not isinstance(self.query_rewrite, str) or not self.query_rewrite.strip():
            raise ValueError("query_rewrite must be a non-empty string")
        object.__setattr__(self, "status", ResultStatus(self.status))


class ReasoningEngine:
    """Plan/act/observe/verify state machine with typed, bounded steps.

    Implements the AIML-007 reasoning contract (spec §6.3):

      - ``plan`` decomposes the query into sub-problems.
      - ``act`` invokes a tool (calculator, search, simulator, etc.).
      - ``observe`` records the tool's output.
      - ``verify`` runs independent checks and produces the terminal result.

    Steps are bounded by ``max_steps``. At least one independent check is
    required for high-impact numerical answers; a missing check downgrades
    to ``degraded`` and a failing check produces ``failed`` — never a
    confident ``succeeded`` answer.
    """

    def __init__(self, *, max_steps: int = 20) -> None:
        if max_steps < 1:
            raise ValueError(f"max_steps must be >= 1, got {max_steps}")
        self._max_steps = max_steps
        self._phase: ReasoningPhase = ReasoningPhase.PLAN
        self._steps: list[StepArtifact] = []

    @property
    def phase(self) -> ReasoningPhase:
        return self._phase

    @property
    def steps(self) -> tuple[StepArtifact, ...]:
        return tuple(self._steps)

    # -- internal helpers --------------------------------------------------

    def _check_bound(self) -> None:
        if len(self._steps) >= self._max_steps:
            raise ValueError(f"max_steps ({self._max_steps}) exceeded — already recorded {len(self._steps)} step(s)")

    def _record(
        self,
        phase: ReasoningPhase,
        tool: str,
        rationale: str,
        artifact_uri: str | None,
    ) -> StepArtifact:
        step = StepArtifact(
            phase=phase,
            step_index=len(self._steps),
            tool=tool,
            rationale=rationale,
            artifact_uri=artifact_uri,
            timestamp=int(time.time()),
        )
        self._steps.append(step)
        return step

    # -- state transitions -------------------------------------------------

    def plan(self, rationale: str, *, artifact_uri: str | None = None) -> StepArtifact:
        """Record a planning step and transition PLAN -> ACT."""
        if self._phase is not ReasoningPhase.PLAN:
            raise ValueError(f"cannot plan from phase {self._phase.value!r} (expected {ReasoningPhase.PLAN.value!r})")
        self._check_bound()
        step = self._record(ReasoningPhase.PLAN, "planner", rationale, artifact_uri)
        self._phase = ReasoningPhase.ACT
        return step

    def act(
        self,
        *,
        tool: str,
        rationale: str,
        artifact_uri: str | None = None,
    ) -> StepArtifact:
        """Record an action step and transition ACT -> OBSERVE."""
        if self._phase is not ReasoningPhase.ACT:
            raise ValueError(f"cannot act from phase {self._phase.value!r} (expected {ReasoningPhase.ACT.value!r})")
        if not tool.strip():
            raise ValueError("tool must be a non-empty string")
        self._check_bound()
        step = StepArtifact(
            phase=ReasoningPhase.ACT,
            step_index=len(self._steps),
            tool=tool,
            rationale=rationale,
            artifact_uri=artifact_uri,
            timestamp=int(time.time()),
        )
        self._steps.append(step)
        self._phase = ReasoningPhase.OBSERVE
        return step

    def observe(
        self,
        rationale: str,
        *,
        artifact_uri: str | None = None,
    ) -> StepArtifact:
        """Record an observation step. The engine stays in OBSERVE so the
        caller can choose to ``replan`` (iterate) or ``verify`` (finalize)."""
        if self._phase is not ReasoningPhase.OBSERVE:
            raise ValueError(
                f"cannot observe from phase {self._phase.value!r} (expected {ReasoningPhase.OBSERVE.value!r})"
            )
        self._check_bound()
        step = self._record(ReasoningPhase.OBSERVE, "observer", rationale, artifact_uri)
        return step

    def replan(
        self,
        rationale: str,
        *,
        artifact_uri: str | None = None,
    ) -> StepArtifact:
        """After observation, iterate: transition OBSERVE -> ACT for another cycle."""
        if self._phase is not ReasoningPhase.OBSERVE:
            raise ValueError(
                f"cannot replan from phase {self._phase.value!r} (expected {ReasoningPhase.OBSERVE.value!r})"
            )
        self._check_bound()
        step = self._record(ReasoningPhase.PLAN, "planner", rationale, artifact_uri)
        self._phase = ReasoningPhase.ACT
        return step

    def verify(
        self,
        *,
        rationale: str,
        query_rewrite: str,
        retrieved_source_ids: tuple[str, ...] = (),
        answer: NumericalAnswer | None = None,
        independent_checks: tuple[IndependentCheck, ...] = (),
    ) -> ReasoningResult:
        """Run independent checks and produce the terminal result.

        Spec §6.3 rules:
          - At least one independent check is required for high-impact
            numerical answers (``answer is not None``).
          - A missing required check downgrades to ``degraded``.
          - A failing check produces ``failed``.
          - Neither ever yields a confident ``succeeded``.
        """
        if self._phase is not ReasoningPhase.OBSERVE:
            raise ValueError(
                f"cannot verify from phase {self._phase.value!r} (expected {ReasoningPhase.OBSERVE.value!r})"
            )

        is_numerical = answer is not None
        failed = any(c.status is VerificationStatus.FAIL for c in independent_checks)
        required_check_missing = is_numerical and len(independent_checks) == 0

        if failed:
            status = ResultStatus.FAILED
            uncertainty = Uncertainty(
                score=0.9,
                method="independent_check_failed",
                limitations=("an independent verification check failed",),
            )
        elif required_check_missing:
            status = ResultStatus.DEGRADED
            uncertainty = Uncertainty(
                score=0.6,
                method="independent_check_not_run",
                limitations=("required independent numerical check was not run",),
            )
        else:
            status = ResultStatus.SUCCEEDED
            uncertainty = Uncertainty(
                score=0.3,
                method="evidence_grounded",
                limitations=(),
            )

        self._phase = ReasoningPhase.TERMINAL
        return ReasoningResult(
            status=status,
            answer=answer,
            rationale=rationale,
            steps=tuple(self._steps),
            independent_checks=independent_checks,
            query_rewrite=query_rewrite,
            retrieved_source_ids=retrieved_source_ids,
            uncertainty=uncertainty,
        )


__all__ = [
    "IndependentCheck",
    "IndependentCheckKind",
    "NumericalAnswer",
    "ReasoningEngine",
    "ReasoningPhase",
    "ReasoningResult",
    "StepArtifact",
]
