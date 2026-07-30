"""AIML Phase C — model distillation: plan + validate a permitted teacher.

Implements capability AIML-009 from docs/specs/FEATURE_AI_ML_EXPERT.md §6.4:

    ``model_distill`` supports response, feature, logit, preference, and
    task-specific distillation only when teacher use and generated-data
    licenses permit it. Teacher outputs are filtered for secrets, unsafe
    content, duplication, and provenance gaps. Student promotion requires
    task-retention thresholds, calibration, adversarial and safety testing,
    contamination checks, and a model card documenting capabilities lost
    during compression.

Acceptance AIML-AT-009: "Distilled student meets suite-declared retention
and safety floors; a below-floor slice blocks promotion."

This module provides the typed contract:

  - :class:`DistillationType` — the four spec-named distillation modes.
  - :class:`DataFilterRule` / :class:`FilterAction` — the teacher-output
    filter rules (secrets/unsafe/duplication/provenance-gap).
  - :class:`RetentionThreshold` — the per-metric retention floor.
  - :class:`DistillationPlan` — the immutable plan a distill role records.
  - :func:`plan_distillation` — build a plan from teacher+student constraints.
  - :func:`validate_student` — check retention thresholds, calibration,
    safety, contamination, and capability-loss documentation before
    promotion.

The :class:`PolicyEngine` (``general_ludd.ai_ml.policy``) gates whether a
DISTILL task may even proceed; this module assumes the request already
passed the license/budget gate and focuses on the data + promotion gates.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DistillationType(enum.StrEnum):
    """Distillation modes supported by ``model_distill`` (spec §6.4).

    - ``RESPONSE``       — distill teacher *responses* (text/text).
    - ``FEATURE``        — distill intermediate hidden states / activations.
    - ``LOGIT``          — match teacher output distribution (logits).
    - ``PREFERENCE``     — preference / RLHF-style distillation.
    - ``TASK_SPECIFIC``  — distill task-specific heads or skills.
    """

    RESPONSE = "response"
    FEATURE = "feature"
    LOGIT = "logit"
    PREFERENCE = "preference"
    TASK_SPECIFIC = "task_specific"


class FilterAction(enum.StrEnum):
    """Action a data filter rule takes on a flagged teacher output.

    - ``DROP``        — exclude the output entirely (secrets, provenance gaps).
    - ``QUARANTINE``  — hold for human review (unsafe content).
    - ``REDACT``      — replace the matched span with a placeholder.
    """

    DROP = "drop"
    QUARANTINE = "quarantine"
    REDACT = "redact"


# ---------------------------------------------------------------------------
# Data filter rule + retention threshold
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataFilterRule:
    """A teacher-output filter rule (spec §6.4 + §11).

    Teacher outputs are filtered for: secrets (DROP), unsafe content
    (QUARANTINE), duplication (DROP), and provenance gaps (DROP). The
    ``pattern`` is a regex applied to each teacher output; ``action``
    determines the disposition.
    """

    name: str
    pattern: str
    action: FilterAction

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "name")
        _require_nonempty_str(self.pattern, "pattern")
        object.__setattr__(
            self,
            "action",
            _coerce_enum(self.action, FilterAction, "action"),
        )


@dataclass(frozen=True)
class RetentionThreshold:
    """A per-metric retention floor the student must meet (spec §6.4).

    ``min_value`` is the threshold. For metrics where lower is better
    (e.g. calibration error / ECE), set ``lower_is_better=True``; the
    student's score must then be <= ``min_value``.
    """

    metric: str
    min_value: float
    lower_is_better: bool = False

    def __post_init__(self) -> None:
        _require_nonempty_str(self.metric, "metric")
        if not isinstance(self.min_value, int | float) or self.min_value < 0:
            raise ValueError(f"min_value must be a non-negative number, got {self.min_value!r}")

    def is_met(self, actual: float) -> bool:
        """Return ``True`` when ``actual`` meets the retention floor."""
        if not isinstance(actual, int | float) or actual < 0:
            return False
        if self.lower_is_better:
            return actual <= self.min_value
        return actual >= self.min_value


# ---------------------------------------------------------------------------
# DistillationPlan — the immutable plan record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DistillationPlan:
    """A distillation plan: teacher + student + filters + thresholds (§6.4).

    Fields:
      - ``teacher_model_digest``: SHA-256 of the teacher weights.
      - ``teacher_license``: SPDX license string of the teacher model.
      - ``teacher_distillation_permitted``: whether the teacher license
        permits distillation (spec §6.4: "only when teacher use and
        generated-data licenses permit it").
      - ``student_architecture``: free-text architecture descriptor.
      - ``student_model_digest``: SHA-256 of the student weights.
      - ``distillation_type``: one of RESPONSE/FEATURE/LOGIT/PREFERENCE/
        TASK_SPECIFIC.
      - ``data_filter_rules``: >= 1 rule; secrets/unsafe/provenance.
      - ``retention_thresholds``: per-metric retention floors.
      - ``safety_tests_required``: >= 1 required safety test names.
      - ``capability_loss_notes``: model-card notes documenting lost
        capabilities (spec §6.4). Defaults to empty; validate_student
        enforces non-empty for promotion.
    """

    teacher_model_digest: str
    teacher_license: str
    teacher_distillation_permitted: bool
    student_architecture: str
    student_model_digest: str
    distillation_type: DistillationType
    data_filter_rules: tuple[DataFilterRule, ...]
    retention_thresholds: tuple[RetentionThreshold, ...]
    safety_tests_required: tuple[str, ...]
    capability_loss_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.teacher_model_digest, "teacher_model_digest")
        _require_nonempty_str(self.teacher_license, "teacher_license")
        if not isinstance(self.teacher_distillation_permitted, bool):
            raise ValueError("teacher_distillation_permitted must be a bool")
        _require_nonempty_str(self.student_architecture, "student_architecture")
        _require_sha256(self.student_model_digest, "student_model_digest")
        object.__setattr__(
            self,
            "distillation_type",
            _coerce_enum(self.distillation_type, DistillationType, "distillation_type"),
        )
        if not self.data_filter_rules:
            raise ValueError(
                "data_filter_rules must contain at least one rule "
                "(spec §6.4: secrets/unsafe/duplication/provenance filters required)"
            )
        for rule in self.data_filter_rules:
            if not isinstance(rule, DataFilterRule):
                raise ValueError(f"data_filter_rules entries must be DataFilterRule, got {type(rule).__name__}")
        if not isinstance(self.retention_thresholds, tuple) or any(
            not isinstance(t, RetentionThreshold) for t in self.retention_thresholds
        ):
            raise ValueError("retention_thresholds must be a tuple of RetentionThreshold")
        if not self.safety_tests_required:
            raise ValueError(
                "safety_tests_required must list at least one test (spec §6.4: adversarial and safety testing required)"
            )
        for t in self.safety_tests_required:
            if not isinstance(t, str) or not t.strip():
                raise ValueError("safety_tests_required entries must be non-empty strings")
        for note in self.capability_loss_notes:
            if not isinstance(note, str) or not note.strip():
                raise ValueError("capability_loss_notes entries must be non-empty strings")


# ---------------------------------------------------------------------------
# StudentValidation — promotion gate result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StudentValidation:
    """Typed result of :func:`validate_student`.

    ``passed`` is the promotion verdict. When ``False``, ``blocked_reasons``
    carries the human-readable list of gates that failed (retention floor,
    calibration, safety, contamination, capability-loss documentation).
    """

    passed: bool
    retention_scores: tuple[tuple[str, float, float, bool], ...]
    calibration_met: bool
    safety_met: bool
    contamination_detected: bool
    capability_loss_documented: bool
    blocked_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool")
        for entry in self.retention_scores:
            if len(entry) != 4:
                raise ValueError(f"retention_scores entries must be (metric, actual, threshold, met), got {entry!r}")
        if not isinstance(self.calibration_met, bool):
            raise ValueError("calibration_met must be a bool")
        if not isinstance(self.safety_met, bool):
            raise ValueError("safety_met must be a bool")
        if not isinstance(self.contamination_detected, bool):
            raise ValueError("contamination_detected must be a bool")
        if not isinstance(self.capability_loss_documented, bool):
            raise ValueError("capability_loss_documented must be a bool")
        if self.passed and self.blocked_reasons:
            raise ValueError("a passing validation must not carry blocked_reasons")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def plan_distillation(
    *,
    teacher_model_digest: str,
    teacher_license: str,
    teacher_distillation_permitted: bool,
    student_architecture: str,
    student_model_digest: str,
    distillation_type: DistillationType,
    data_filter_rules: tuple[DataFilterRule, ...],
    retention_thresholds: tuple[RetentionThreshold, ...],
    safety_tests_required: tuple[str, ...] = ("adversarial", "toxicity", "bias"),
    capability_loss_notes: tuple[str, ...] = (),
) -> DistillationPlan:
    """Build a :class:`DistillationPlan` from teacher+student constraints.

    The plan is the immutable record the ``model_distill`` role hands to
    the training loop. It captures every field required to reproduce the
    distillation and to gate the student's promotion.
    """
    return DistillationPlan(
        teacher_model_digest=teacher_model_digest,
        teacher_license=teacher_license,
        teacher_distillation_permitted=teacher_distillation_permitted,
        student_architecture=student_architecture,
        student_model_digest=student_model_digest,
        distillation_type=distillation_type,
        data_filter_rules=data_filter_rules,
        retention_thresholds=retention_thresholds,
        safety_tests_required=safety_tests_required,
        capability_loss_notes=capability_loss_notes,
    )


def validate_student(
    plan: DistillationPlan,
    *,
    retention_scores: dict[str, float],
    calibration_met: bool,
    safety_met: bool,
    contamination_detected: bool,
    capability_loss_notes: tuple[str, ...] = (),
) -> StudentValidation:
    """Check retention thresholds, calibration, safety, contamination, and
    capability-loss documentation for student promotion (spec §6.4).

    Returns a :class:`StudentValidation` whose ``passed`` is the promotion
    verdict. A below-floor retention slice blocks promotion (AIML-AT-009);
    a safety regression always blocks (§5.3); detected contamination blocks
    (§6.4); missing capability-loss documentation blocks (§6.4: "a model
    card documenting capabilities lost during compression").
    """
    if not isinstance(plan, DistillationPlan):
        raise ValueError("plan must be a DistillationPlan instance")
    if not isinstance(retention_scores, dict):
        raise ValueError("retention_scores must be a dict[str, float]")

    scored: list[tuple[str, float, float, bool]] = []
    blocked: list[str] = []

    for threshold in plan.retention_thresholds:
        actual = retention_scores.get(threshold.metric)
        if actual is None:
            blocked.append(f"retention metric {threshold.metric!r} has no measured score")
            continue
        met = threshold.is_met(actual)
        scored.append((threshold.metric, float(actual), threshold.min_value, met))
        if not met:
            direction = "<=" if threshold.lower_is_better else ">="
            blocked.append(
                f"retention floor not met: {threshold.metric}={actual} required {direction} {threshold.min_value}"
            )

    if not calibration_met:
        blocked.append("calibration check failed; student ECE exceeds suite threshold")
    if not safety_met:
        blocked.append("safety floor not met; student failed adversarial/toxicity/bias tests")
    if contamination_detected:
        blocked.append("contamination detected; teacher data overlaps evaluation set")
    if not capability_loss_notes:
        blocked.append(
            "capability-loss documentation missing; a model card documenting "
            "capabilities lost during compression is required for promotion"
        )

    passed = not blocked
    return StudentValidation(
        passed=passed,
        retention_scores=tuple(scored),
        calibration_met=calibration_met,
        safety_met=safety_met,
        contamination_detected=contamination_detected,
        capability_loss_documented=bool(capability_loss_notes),
        blocked_reasons=tuple(blocked),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_enum(value: object, enum_cls: type[enum.Enum], field_name: str) -> enum.Enum:
    """Coerce a string or enum member; raise ValueError on miss."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    raise ValueError(f"invalid {field_name}: {value!r}")


__all__ = [
    "DataFilterRule",
    "DistillationPlan",
    "DistillationType",
    "FilterAction",
    "RetentionThreshold",
    "StudentValidation",
    "plan_distillation",
    "validate_student",
]
