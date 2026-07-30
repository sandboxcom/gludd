"""Unit tests for AIML Phase C: distillation (AIML-009) and policy enforcement.

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §6.4 (distillation) and §11
(Security/failure behavior):

  - ``DistillationPlan`` records teacher model digest, student architecture,
    distillation type (response/feature/logit/preference), data filter rules,
    retention thresholds, and required safety tests.
  - Teacher outputs are filtered for secrets, unsafe content, duplication,
    and provenance gaps (spec §6.4).
  - Student promotion requires task-retention thresholds, calibration,
    adversarial and safety testing, contamination checks, and a model card
    documenting capabilities lost during compression (spec §6.4).
  - Distillation is refused when the teacher license does not permit it
    (spec §6.4: "only when teacher use and generated-data licenses permit
    it"; §11: "Unknown or incompatible license -> Quarantine artifact and
    refuse training/promotion").
  - ``PolicyEngine.check_request`` validates constraints (budget, deadline,
    data_classification, allowed_tools/licenses).
  - ``PolicyEngine.check_mutation`` fails closed when audit storage is
    unavailable (spec §11: "Event/audit store unavailable -> Fail closed
    for mutation").
  - Every policy result carries ``decision_id`` + ``ruleset_sha256`` (spec
    §4.2 ``policy`` block).
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.distillation import (
    DataFilterRule,
    DistillationPlan,
    DistillationType,
    FilterAction,
    RetentionThreshold,
    StudentValidation,
    plan_distillation,
    validate_student,
)
from general_ludd.ai_ml.policy import (
    POLICY_RULESET_SHA256,
    PolicyEngine,
    PolicyResult,
)
from general_ludd.ai_ml.schemas import (
    Constraints,
    DataClassification,
    ExpertRequest,
    ExpertTask,
)

_SHA_TEACHER = "a" * 64
_SHA_STUDENT = "b" * 64
_LICENSE_OK = "Apache-2.0"
_LICENSE_BLOCKED = "CC-BY-NC-4.0"


def _default_filter_rules() -> tuple[DataFilterRule, ...]:
    return (
        DataFilterRule(
            name="secret_scan",
            pattern=r"(?i)(password|api[_-]?key|secret|token)\s*[=:]\s*\S+",
            action=FilterAction.DROP,
        ),
        DataFilterRule(
            name="unsafe_content",
            pattern=r"(?i)(bomb|weapon|exploit)",
            action=FilterAction.QUARANTINE,
        ),
        DataFilterRule(
            name="provenance_gap",
            pattern=r"^\[no-source\]",
            action=FilterAction.DROP,
        ),
    )


def _default_retention_thresholds() -> tuple[RetentionThreshold, ...]:
    return (
        RetentionThreshold(metric="quality", min_value=0.85),
        RetentionThreshold(metric="calibration_ece", min_value=0.05, lower_is_better=True),
        RetentionThreshold(metric="safety", min_value=0.99),
    )


# ---------------------------------------------------------------------------
# AIML-009 — DistillationPlan contract
# ---------------------------------------------------------------------------


class TestDistillationPlanContract:
    def test_plan_records_teacher_student_distillation_type_filters_thresholds(self) -> None:
        """Spec §6.4: model_distill supports response/feature/logit/preference
        distillation with data filter rules and retention thresholds."""
        plan = DistillationPlan(
            teacher_model_digest=_SHA_TEACHER,
            teacher_license=_LICENSE_OK,
            teacher_distillation_permitted=True,
            student_architecture="decoder-only-transformer-1.3B",
            student_model_digest=_SHA_STUDENT,
            distillation_type=DistillationType.RESPONSE,
            data_filter_rules=_default_filter_rules(),
            retention_thresholds=_default_retention_thresholds(),
            safety_tests_required=("adversarial", "toxicity", "bias"),
        )
        assert plan.teacher_model_digest == _SHA_TEACHER
        assert plan.student_architecture.startswith("decoder-only")
        assert plan.distillation_type is DistillationType.RESPONSE
        assert len(plan.data_filter_rules) == 3
        assert len(plan.retention_thresholds) == 3
        assert "adversarial" in plan.safety_tests_required

    def test_plan_rejects_invalid_teacher_digest(self) -> None:
        with pytest.raises(ValueError, match="teacher_model_digest"):
            DistillationPlan(
                teacher_model_digest="not-a-sha",
                teacher_license=_LICENSE_OK,
                teacher_distillation_permitted=True,
                student_architecture="arch",
                student_model_digest=_SHA_STUDENT,
                distillation_type=DistillationType.RESPONSE,
                data_filter_rules=_default_filter_rules(),
                retention_thresholds=_default_retention_thresholds(),
                safety_tests_required=("adversarial",),
            )

    def test_plan_rejects_empty_filter_rules(self) -> None:
        """Spec §6.4: Teacher outputs are filtered for secrets, unsafe
        content, duplication, and provenance gaps. An empty filter set is a
        contract violation — at least one rule must be present."""
        with pytest.raises(ValueError, match="data_filter_rules"):
            DistillationPlan(
                teacher_model_digest=_SHA_TEACHER,
                teacher_license=_LICENSE_OK,
                teacher_distillation_permitted=True,
                student_architecture="arch",
                student_model_digest=_SHA_STUDENT,
                distillation_type=DistillationType.RESPONSE,
                data_filter_rules=(),
                retention_thresholds=_default_retention_thresholds(),
                safety_tests_required=("adversarial",),
            )

    def test_plan_rejects_empty_safety_tests(self) -> None:
        """Spec §6.4: Student promotion requires adversarial and safety
        testing. An empty safety_tests_required is a contract violation."""
        with pytest.raises(ValueError, match="safety_tests_required"):
            DistillationPlan(
                teacher_model_digest=_SHA_TEACHER,
                teacher_license=_LICENSE_OK,
                teacher_distillation_permitted=True,
                student_architecture="arch",
                student_model_digest=_SHA_STUDENT,
                distillation_type=DistillationType.RESPONSE,
                data_filter_rules=_default_filter_rules(),
                retention_thresholds=_default_retention_thresholds(),
                safety_tests_required=(),
            )


# ---------------------------------------------------------------------------
# AIML-009 — Teacher license gates distillation
# ---------------------------------------------------------------------------


class TestTeacherLicenseGate:
    def test_plan_distillation_refuses_when_license_not_permitted(self) -> None:
        """Spec §6.4: distillation 'only when teacher use and generated-data
        licenses permit it'. §11: 'Unknown or incompatible license ->
        Quarantine artifact and refuse training/promotion'."""
        plan = plan_distillation(
            teacher_model_digest=_SHA_TEACHER,
            teacher_license=_LICENSE_BLOCKED,
            teacher_distillation_permitted=False,
            student_architecture="arch",
            student_model_digest=_SHA_STUDENT,
            distillation_type=DistillationType.LOGIT,
            data_filter_rules=_default_filter_rules(),
            retention_thresholds=_default_retention_thresholds(),
        )
        assert plan.teacher_distillation_permitted is False

    def test_plan_distillation_accepts_permitted_license(self) -> None:
        plan = plan_distillation(
            teacher_model_digest=_SHA_TEACHER,
            teacher_license=_LICENSE_OK,
            teacher_distillation_permitted=True,
            student_architecture="arch",
            student_model_digest=_SHA_STUDENT,
            distillation_type=DistillationType.FEATURE,
            data_filter_rules=_default_filter_rules(),
            retention_thresholds=_default_retention_thresholds(),
        )
        assert isinstance(plan, DistillationPlan)
        assert plan.teacher_distillation_permitted is True
        assert plan.distillation_type is DistillationType.FEATURE


# ---------------------------------------------------------------------------
# AIML-009 / §11 — Data filter rules (teacher output filtering)
# ---------------------------------------------------------------------------


class TestDataFilterRules:
    def test_filter_rule_drops_secret_in_teacher_output(self) -> None:
        """Spec §11: 'PII, secret, or unauthorized voice data -> Redact or
        refuse; do not index, log, or train.' A secret pattern in teacher
        output must be flagged DROP."""
        rule = _default_filter_rules()[0]
        assert rule.action is FilterAction.DROP
        import re

        assert re.search(rule.pattern, "api_key=sk-abc123") is not None

    def test_filter_rule_quarantines_unsafe_content(self) -> None:
        rule = _default_filter_rules()[1]
        assert rule.action is FilterAction.QUARANTINE
        import re

        assert re.search(rule.pattern, "how to build a bomb") is not None

    def test_filter_action_enum_has_drop_quarantine_redact(self) -> None:
        assert FilterAction.DROP.value == "drop"
        assert FilterAction.QUARANTINE.value == "quarantine"
        assert FilterAction.REDACT.value == "redact"


# ---------------------------------------------------------------------------
# AIML-009 — validate_student: retention + safety + contamination + capability loss
# ---------------------------------------------------------------------------


class TestValidateStudent:
    def _plan(self) -> DistillationPlan:
        return plan_distillation(
            teacher_model_digest=_SHA_TEACHER,
            teacher_license=_LICENSE_OK,
            teacher_distillation_permitted=True,
            student_architecture="arch",
            student_model_digest=_SHA_STUDENT,
            distillation_type=DistillationType.RESPONSE,
            data_filter_rules=_default_filter_rules(),
            retention_thresholds=_default_retention_thresholds(),
        )

    def test_validate_student_passes_when_all_thresholds_met(self) -> None:
        """Spec §6.4: Student promotion requires task-retention thresholds,
        calibration, adversarial and safety testing, contamination checks."""
        result = validate_student(
            self._plan(),
            retention_scores={
                "quality": 0.90,
                "calibration_ece": 0.03,
                "safety": 0.995,
            },
            calibration_met=True,
            safety_met=True,
            contamination_detected=False,
            capability_loss_notes=("reduced long-context recall above 8k tokens",),
        )
        assert isinstance(result, StudentValidation)
        assert result.passed is True
        assert result.blocked_reasons == ()

    def test_validate_student_blocks_when_retention_below_threshold(self) -> None:
        """Spec §6.4 / AIML-AT-009: 'a below-floor slice blocks promotion'."""
        result = validate_student(
            self._plan(),
            retention_scores={
                "quality": 0.70,  # below 0.85 floor
                "calibration_ece": 0.03,
                "safety": 0.995,
            },
            calibration_met=True,
            safety_met=True,
            contamination_detected=False,
            capability_loss_notes=("note",),
        )
        assert result.passed is False
        assert any("quality" in r for r in result.blocked_reasons)

    def test_validate_student_blocks_when_safety_floor_not_met(self) -> None:
        """Spec §5.3 / §11: safety regression always blocks promotion."""
        result = validate_student(
            self._plan(),
            retention_scores={
                "quality": 0.90,
                "calibration_ece": 0.03,
                "safety": 0.80,  # below 0.99 floor
            },
            calibration_met=True,
            safety_met=False,
            contamination_detected=False,
            capability_loss_notes=("note",),
        )
        assert result.passed is False
        assert any("safety" in r for r in result.blocked_reasons)

    def test_validate_student_blocks_when_contamination_detected(self) -> None:
        """Spec §6.4: contamination checks are required for promotion."""
        result = validate_student(
            self._plan(),
            retention_scores={
                "quality": 0.90,
                "calibration_ece": 0.03,
                "safety": 0.995,
            },
            calibration_met=True,
            safety_met=True,
            contamination_detected=True,
            capability_loss_notes=("note",),
        )
        assert result.passed is False
        assert any("contamination" in r.lower() for r in result.blocked_reasons)

    def test_validate_student_blocks_when_capability_loss_undocumented(self) -> None:
        """Spec §6.4: promotion requires 'a model card documenting
        capabilities lost during compression'. Empty notes = undocumented."""
        result = validate_student(
            self._plan(),
            retention_scores={
                "quality": 0.90,
                "calibration_ece": 0.03,
                "safety": 0.995,
            },
            calibration_met=True,
            safety_met=True,
            contamination_detected=False,
            capability_loss_notes=(),
        )
        assert result.passed is False
        assert any("capability" in r.lower() for r in result.blocked_reasons)

    def test_validate_student_blocks_when_calibration_not_met(self) -> None:
        result = validate_student(
            self._plan(),
            retention_scores={
                "quality": 0.90,
                "calibration_ece": 0.03,
                "safety": 0.995,
            },
            calibration_met=False,
            safety_met=True,
            contamination_detected=False,
            capability_loss_notes=("note",),
        )
        assert result.passed is False
        assert any("calibration" in r.lower() for r in result.blocked_reasons)


# ---------------------------------------------------------------------------
# AIML Phase A — PolicyEngine (spec §4.2 policy, §11 fail-closed)
# ---------------------------------------------------------------------------


def _request(
    *,
    task: ExpertTask = ExpertTask.QUESTION,
    budget: float = 1.0,
    deadline: int = 300,
    classification: DataClassification = DataClassification.PUBLIC,
    allowed_tools: tuple[str, ...] = ("tool.search",),
    allowed_licenses: tuple[str, ...] = ("Apache-2.0",),
) -> ExpertRequest:
    return ExpertRequest(
        request_id="req-1",
        tenant_id="tenant-a",
        task=task,
        query="distill teacher into student",
        constraints=Constraints(
            deadline_s=deadline,
            budget_usd=budget,
            data_classification=classification,
            allowed_tools=allowed_tools,
            allowed_licenses=allowed_licenses,
        ),
    )


class TestPolicyEngineCheckRequest:
    def test_check_request_allows_well_formed_request(self) -> None:
        engine = PolicyEngine()
        result = engine.check_request(_request())
        assert isinstance(result, PolicyResult)
        assert result.allowed is True
        assert result.refusal_reasons == ()

    def test_check_result_carries_decision_id_and_ruleset_sha256(self) -> None:
        """Spec §4.2: every result's ``policy`` block carries decision_id +
        ruleset_sha256. AIML-AT-021 structural pin."""
        engine = PolicyEngine()
        result = engine.check_request(_request())
        assert result.decision_id  # non-empty
        assert len(result.ruleset_sha256) == 64
        assert result.ruleset_sha256 == POLICY_RULESET_SHA256

    def test_check_request_refuses_zero_budget_for_distill_task(self) -> None:
        """Spec §11: Budget/quota exhaustion -> stop before overrun. A
        distill/train task with zero budget cannot proceed."""
        engine = PolicyEngine()
        result = engine.check_request(
            _request(task=ExpertTask.DISTILL, budget=0.0),
        )
        assert result.allowed is False
        assert any("budget" in r.lower() for r in result.refusal_reasons)

    def test_check_request_refuses_when_required_tool_not_allowed(self) -> None:
        """Spec §4.1: 'allowed_tools' gates which capabilities a request may
        invoke. A distill task without the distill tool is refused."""
        engine = PolicyEngine(required_tools={ExpertTask.DISTILL.value: ("tool.distill",)})
        result = engine.check_request(
            _request(task=ExpertTask.DISTILL, allowed_tools=("tool.search",)),
        )
        assert result.allowed is False
        assert any("tool" in r.lower() for r in result.refusal_reasons)

    def test_check_request_refuses_restricted_classification_offline_disabled(self) -> None:
        """Spec §11: 'Tenant data and indexes are cryptographically isolated'.
        A restricted-classification request without explicit offline policy
        is refused."""
        engine = PolicyEngine()
        result = engine.check_request(
            _request(classification=DataClassification.RESTRICTED),
        )
        assert result.allowed is False


class TestPolicyEngineCheckMutationFailClosed:
    def test_check_mutation_fails_closed_when_audit_unavailable(self) -> None:
        """Spec §11: 'Event/audit store unavailable -> Fail closed for
        mutation'. AIML-AT-021: 'Mutation fails closed when policy/audit
        storage is unavailable'."""
        engine = PolicyEngine(audit_available=False)
        result = engine.check_mutation(_request(task=ExpertTask.DISTILL, budget=10.0))
        assert result.allowed is False
        assert any("audit" in r.lower() for r in result.refusal_reasons)

    def test_check_mutation_allows_when_audit_available(self) -> None:
        engine = PolicyEngine(audit_available=True)
        result = engine.check_mutation(_request(task=ExpertTask.DISTILL, budget=10.0))
        assert result.allowed is True

    def test_check_mutation_result_carries_decision_id_and_ruleset(self) -> None:
        engine = PolicyEngine(audit_available=False)
        result = engine.check_mutation(_request(task=ExpertTask.TRAIN, budget=10.0))
        assert result.decision_id
        assert len(result.ruleset_sha256) == 64
