"""AIML Phase E — world-model contract (AIML-014, spec §8.1).

A world-model environment defines observation/action/state schemas, units,
time step, reset/terminal behavior, stochastic seed, legal actions, constraints,
reward or objective, simulator/source version, and dataset manifest. Models may
be latent dynamics, state-space, predictive video, object-centric, or hybrid
physics/learned systems.

Training and evaluation measure multi-horizon prediction, calibration,
constraint violations, compounding error, out-of-distribution detection,
controllability, planning regret, and wall-clock/accelerator cost. Rollouts
expose epistemic and aleatoric uncertainty. A low-confidence rollout cannot
authorize real-world actuation (AIML-AT-013: "unsafe actuation is impossible").

This module provides the typed contract:

  - :class:`ConstraintSpec` — an environment constraint (name/value/unit).
  - :class:`WorldModelEnvironment` — the immutable environment record.
  - :class:`HorizonMetrics` — per-horizon prediction metrics; the historical
    ``HorizonError`` spelling remains an import-compatible alias.
  - :class:`ConstraintViolation` — a constraint violation finding.
  - :class:`RolloutUncertainty` — epistemic + aleatoric rollout uncertainty.
  - :class:`RolloutEvaluation` — the typed ``evaluate_rollout`` result.
  - :func:`evaluate_rollout` — assemble a rollout evaluation and decide
    whether the rollout's confidence is high enough to authorize actuation.

The actuation gate is the safety-critical invariant: a rollout may authorize
real-world actuation only when epistemic uncertainty is below
:data:`ACTUATION_MAX_EPISTEMIC`, the epistemic+aleatoric sum is below
:data:`ACTUATION_MAX_UNCERTAINTY_SUM`, no constraint was violated, and the
calibration error is acceptable. Any other condition yields
``can_authorize_actuation=False``.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any

from general_ludd.ai_ml.schemas import _require_nonempty_str

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

# Actuation safety thresholds (spec §8.1: "A low-confidence rollout cannot
# authorize real-world actuation"). A rollout must clear ALL of these to
# authorize actuation: epistemic below the cap, combined uncertainty below the
# sum cap, zero constraint violations, and acceptable calibration error.
ACTUATION_MAX_EPISTEMIC = 0.20
ACTUATION_MAX_UNCERTAINTY_SUM = 0.30
ACTUATION_MAX_CALIBRATION_ECE = 0.10


class WorldModelKind(enum.StrEnum):
    """The family of world model (spec §8.1)."""

    LATENT_DYNAMICS = "latent_dynamics"
    STATE_SPACE = "state_space"
    PREDICTIVE_VIDEO = "predictive_video"
    OBJECT_CENTRIC = "object_centric"
    HYBRID_PHYSICS_LEARNED = "hybrid_physics_learned"


def _validate_semver(version: str) -> None:
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise ValueError(f"version must be a semantic-version string (MAJOR.MINOR.PATCH), got {version!r}")


def _coerce_enum(value: Any, enum_cls: type[enum.Enum], field_name: str) -> enum.Enum:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    raise ValueError(f"invalid {field_name}: {value!r}")


@dataclass(frozen=True)
class ConstraintSpec:
    """An environment constraint (spec §8.1 ``constraints``).

    A constraint is a named bound on a state/observation variable with its
    unit, e.g. ``max_velocity=10.0 m/s``. ``evaluate_rollout`` flags any
    rollout whose trajectory exceeds these bounds as a
    :class:`ConstraintViolation`.
    """

    name: str
    value: float
    unit: str

    def __post_init__(self) -> None:
        """Reject incomplete constraints and non-numeric bound values."""
        _require_nonempty_str(self.name, "name")
        if not isinstance(self.value, int | float) or isinstance(self.value, bool):
            raise ValueError(f"value must be a real number, got {self.value!r}")
        _require_nonempty_str(self.unit, "unit")


@dataclass(frozen=True)
class WorldModelEnvironment:
    """A world-model environment contract (spec §8.1).

    Fields:
      - ``env_id``: stable environment identifier (e.g. ``gridworld-v1``).
      - ``observation_schema``/``action_schema``/``state_schema``: artifact
        URIs for the JSON schemas of observations, actions, and states.
      - ``units_system``: the unit system (``SI`` by default).
      - ``time_step_s``: the discrete time step in seconds (> 0).
      - ``legal_actions``: the exhaustive set of legal action tokens.
      - ``constraints``: environment constraints (bounds on state/obs).
      - ``reward_objective``: the reward / objective name.
      - ``simulator_record_id``: the registry record id of the backing
        simulator/source (spec §8.1: "simulator/source version").
      - ``dataset_manifest_uri``: the dataset manifest artifact URI
        (spec §8.1: "dataset manifest").
      - ``seed``: the stochastic seed (>= 0).
      - ``kind``: the world-model family (default HYBRID_PHYSICS_LEARNED).
      - ``reset_behavior``/``terminal_behavior``: free-text descriptions of
        reset and terminal semantics (spec §8.1: "reset/terminal behavior").
    """

    env_id: str
    observation_schema: str
    action_schema: str
    state_schema: str
    units_system: str
    time_step_s: float
    legal_actions: tuple[str, ...]
    constraints: tuple[ConstraintSpec, ...]
    reward_objective: str
    simulator_record_id: str
    dataset_manifest_uri: str
    seed: int
    kind: WorldModelKind = WorldModelKind.HYBRID_PHYSICS_LEARNED
    reset_behavior: str = "reset_to_initial_state"
    terminal_behavior: str = "terminal_on_goal_or_timeout"

    def __post_init__(self) -> None:
        """Validate the complete environment contract and normalize its kind."""
        _require_nonempty_str(self.env_id, "env_id")
        _require_nonempty_str(self.observation_schema, "observation_schema")
        _require_nonempty_str(self.action_schema, "action_schema")
        _require_nonempty_str(self.state_schema, "state_schema")
        _require_nonempty_str(self.units_system, "units_system")
        if not isinstance(self.time_step_s, int | float) or self.time_step_s <= 0:
            raise ValueError(f"time_step_s must be > 0, got {self.time_step_s!r}")
        if not self.legal_actions:
            raise ValueError("legal_actions must contain at least one action token")
        for a in self.legal_actions:
            if not isinstance(a, str) or not a.strip():
                raise ValueError("legal_actions entries must be non-empty strings")
        if not isinstance(self.constraints, tuple) or any(not isinstance(c, ConstraintSpec) for c in self.constraints):
            raise ValueError("constraints must be a tuple of ConstraintSpec")
        if not self.constraints:
            raise ValueError("constraints must contain at least one ConstraintSpec")
        _require_nonempty_str(self.reward_objective, "reward_objective")
        _require_nonempty_str(self.simulator_record_id, "simulator_record_id")
        _require_nonempty_str(self.dataset_manifest_uri, "dataset_manifest_uri")
        if not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError(f"seed must be a non-negative int, got {self.seed!r}")
        object.__setattr__(self, "kind", _coerce_enum(self.kind, WorldModelKind, "kind"))
        _require_nonempty_str(self.reset_behavior, "reset_behavior")
        _require_nonempty_str(self.terminal_behavior, "terminal_behavior")


@dataclass(frozen=True)
class HorizonMetrics:
    """Prediction error at a single rollout horizon (spec §8.1).

    Multi-horizon evaluation reports error at several horizon depths so
    compounding error is visible. ``mean_error`` and ``p95_error`` are the
    mean and 95th-percentile prediction error over the evaluation rollout
    set at this horizon.
    """

    horizon_steps: int
    mean_error: float
    p95_error: float

    def __post_init__(self) -> None:
        """Reject non-positive horizons and negative prediction errors."""
        if not isinstance(self.horizon_steps, int) or self.horizon_steps < 1:
            raise ValueError(f"horizon_steps must be a positive int, got {self.horizon_steps!r}")
        for fname in ("mean_error", "p95_error"):
            v = getattr(self, fname)
            if not isinstance(v, int | float) or isinstance(v, bool) or v < 0:
                raise ValueError(f"{fname} must be a non-negative number, got {v!r}")


# Compatibility alias: HorizonError was a metric value, never an exception.
HorizonError = HorizonMetrics


@dataclass(frozen=True)
class ConstraintViolation:
    """A constraint violation surfaced during a rollout (spec §8.1)."""

    constraint_name: str
    severity: float
    description: str

    def __post_init__(self) -> None:
        """Validate the violation identity, normalized severity, and description."""
        _require_nonempty_str(self.constraint_name, "constraint_name")
        if not isinstance(self.severity, int | float) or isinstance(self.severity, bool):
            raise ValueError(f"severity must be a real number, got {self.severity!r}")
        if not (0.0 <= float(self.severity) <= 1.0):
            raise ValueError(f"severity must be in [0.0, 1.0], got {self.severity}")
        _require_nonempty_str(self.description, "description")


@dataclass(frozen=True)
class RolloutUncertainty:
    """Epistemic + aleatoric uncertainty for a rollout (spec §8.1).

    - ``epistemic``: model/structural uncertainty (reducible with more data).
    - ``aleatoric``: inherent stochasticity of the environment.
    - ``method``: the estimation method (e.g. ``deep_ensemble``,
      ``mc_dropout``, ``bayesian``).
    """

    epistemic: float
    aleatoric: float
    method: str

    def __post_init__(self) -> None:
        """Require bounded uncertainty components and a named estimation method."""
        for fname in ("epistemic", "aleatoric"):
            v = getattr(self, fname)
            if not isinstance(v, int | float) or isinstance(v, bool) or not (0.0 <= float(v) <= 1.0):
                raise ValueError(f"{fname} must be in [0.0, 1.0], got {v}")
        _require_nonempty_str(self.method, "method")


@dataclass(frozen=True)
class RolloutEvaluation:
    """Typed result of :func:`evaluate_rollout` (spec §8.1, AIML-AT-013).

    ``can_authorize_actuation`` is the safety verdict: ``True`` only when the
    rollout is well-calibrated, low-uncertainty, and violation-free. A
    ``False`` verdict means the rollout MUST NOT authorize real-world
    actuation (spec §8.1: "A low-confidence rollout cannot authorize
    real-world actuation").
    """

    horizon_errors: tuple[HorizonMetrics, ...]
    calibration_ece: float
    constraint_violations: tuple[ConstraintViolation, ...]
    compounding_error_rate: float
    planning_regret: float
    uncertainty: RolloutUncertainty
    can_authorize_actuation: bool
    actuation_block_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Enforce consistency between rollout metrics and actuation safety."""
        if not self.horizon_errors:
            raise ValueError("horizon_errors must contain at least one HorizonMetrics")
        if any(not isinstance(h, HorizonMetrics) for h in self.horizon_errors):
            raise ValueError("horizon_errors entries must be HorizonMetrics")
        if not isinstance(self.calibration_ece, int | float) or isinstance(self.calibration_ece, bool):
            raise ValueError(f"calibration_ece must be a number, got {self.calibration_ece!r}")
        if self.calibration_ece < 0:
            raise ValueError(f"calibration_ece must be >= 0, got {self.calibration_ece}")
        if not isinstance(self.constraint_violations, tuple) or any(
            not isinstance(v, ConstraintViolation) for v in self.constraint_violations
        ):
            raise ValueError("constraint_violations must be a tuple of ConstraintViolation")
        for fname in ("compounding_error_rate", "planning_regret"):
            v = getattr(self, fname)
            if not isinstance(v, int | float) or isinstance(v, bool) or v < 0:
                raise ValueError(f"{fname} must be a non-negative number, got {v!r}")
        if not isinstance(self.uncertainty, RolloutUncertainty):
            raise ValueError("uncertainty must be a RolloutUncertainty")
        if not isinstance(self.can_authorize_actuation, bool):
            raise ValueError("can_authorize_actuation must be a bool")
        if self.can_authorize_actuation and self.actuation_block_reasons:
            raise ValueError("an actuation-authorized evaluation must not carry block reasons")


def evaluate_rollout(
    env: WorldModelEnvironment,
    *,
    horizon_errors: tuple[HorizonMetrics, ...],
    calibration_ece: float,
    constraint_violations: tuple[ConstraintViolation, ...],
    compounding_error_rate: float,
    planning_regret: float,
    uncertainty: RolloutUncertainty,
) -> RolloutEvaluation:
    """Assemble a :class:`RolloutEvaluation` and decide actuation authority.

    The actuation gate (spec §8.1, AIML-AT-013) clears only when ALL hold:

      1. ``uncertainty.epistemic <= ACTUATION_MAX_EPISTEMIC``
      2. ``uncertainty.epistemic + uncertainty.aleatoric <= ACTUATION_MAX_UNCERTAINTY_SUM``
      3. ``calibration_ece <= ACTUATION_MAX_CALIBRATION_ECE``
      4. ``constraint_violations`` is empty

    Any failure records a human-readable block reason and forces
    ``can_authorize_actuation=False``. This makes "unsafe actuation is
    impossible" a structural property of the evaluation, not a caller
    discipline.
    """
    if not isinstance(env, WorldModelEnvironment):
        raise ValueError("env must be a WorldModelEnvironment instance")

    block_reasons: list[str] = []
    if uncertainty.epistemic > ACTUATION_MAX_EPISTEMIC:
        block_reasons.append(
            f"epistemic uncertainty {uncertainty.epistemic:.3f} exceeds actuation cap {ACTUATION_MAX_EPISTEMIC:.3f}"
        )
    if (uncertainty.epistemic + uncertainty.aleatoric) > ACTUATION_MAX_UNCERTAINTY_SUM:
        block_reasons.append(
            f"combined uncertainty {uncertainty.epistemic + uncertainty.aleatoric:.3f} exceeds "
            f"actuation sum cap {ACTUATION_MAX_UNCERTAINTY_SUM:.3f}"
        )
    if calibration_ece > ACTUATION_MAX_CALIBRATION_ECE:
        block_reasons.append(
            f"calibration ECE {calibration_ece:.3f} exceeds actuation cap {ACTUATION_MAX_CALIBRATION_ECE:.3f}"
        )
    if constraint_violations:
        names = ", ".join(v.constraint_name for v in constraint_violations)
        block_reasons.append(f"constraint violations present: {names}")

    can_authorize = not block_reasons
    return RolloutEvaluation(
        horizon_errors=horizon_errors,
        calibration_ece=calibration_ece,
        constraint_violations=constraint_violations,
        compounding_error_rate=compounding_error_rate,
        planning_regret=planning_regret,
        uncertainty=uncertainty,
        can_authorize_actuation=can_authorize,
        actuation_block_reasons=tuple(block_reasons),
    )


__all__ = [
    "ACTUATION_MAX_CALIBRATION_ECE",
    "ACTUATION_MAX_EPISTEMIC",
    "ACTUATION_MAX_UNCERTAINTY_SUM",
    "ConstraintSpec",
    "ConstraintViolation",
    "HorizonError",
    "HorizonMetrics",
    "RolloutEvaluation",
    "RolloutUncertainty",
    "WorldModelEnvironment",
    "WorldModelKind",
    "evaluate_rollout",
]
