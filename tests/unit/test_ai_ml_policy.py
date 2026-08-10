"""Unit tests for AIML Phase A/C — policy engine (spec §4.2, §11).

Covers:
  - PolicyResult validation (frozen dataclass invariants)
  - PolicyEngine construction + ruleset_sha256
  - check_request constraints (deadline, budget, restricted, tools)
  - check_mutation fail-closed on audit unavailability
  - POLICY_RULESET_SHA256 stability
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

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

# ---------------------------------------------------------------------------
# PolicyResult
# ---------------------------------------------------------------------------


class TestPolicyResult:
    def test_allowed_no_reasons(self) -> None:
        r = PolicyResult(
            decision_id="pol-abc",
            ruleset_sha256=POLICY_RULESET_SHA256,
            allowed=True,
        )
        assert r.allowed is True
        assert r.refusal_reasons == ()

    def test_refused_with_reasons(self) -> None:
        r = PolicyResult(
            decision_id="pol-abc",
            ruleset_sha256=POLICY_RULESET_SHA256,
            allowed=False,
            refusal_reasons=("budget insufficient",),
        )
        assert r.allowed is False
        assert r.refusal_reasons == ("budget insufficient",)

    def test_empty_decision_id_raises(self) -> None:
        with pytest.raises(ValueError, match="decision_id must be a non-empty string"):
            PolicyResult(
                decision_id="",
                ruleset_sha256=POLICY_RULESET_SHA256,
                allowed=True,
            )

    def test_whitespace_decision_id_raises(self) -> None:
        with pytest.raises(ValueError, match="decision_id must be a non-empty string"):
            PolicyResult(
                decision_id="   ",
                ruleset_sha256=POLICY_RULESET_SHA256,
                allowed=True,
            )

    def test_empty_ruleset_sha256_raises(self) -> None:
        with pytest.raises(ValueError, match="ruleset_sha256 must be a non-empty sha256 hex digest"):
            PolicyResult(
                decision_id="pol-abc",
                ruleset_sha256="",
                allowed=True,
            )

    def test_allowed_with_reasons_raises(self) -> None:
        with pytest.raises(ValueError, match="an allowed result must not carry refusal_reasons"):
            PolicyResult(
                decision_id="pol-abc",
                ruleset_sha256=POLICY_RULESET_SHA256,
                allowed=True,
                refusal_reasons=("something",),
            )

    def test_refusal_reasons_must_be_strings(self) -> None:
        with pytest.raises(ValueError, match="refusal_reasons entries must be non-empty strings"):
            PolicyResult(
                decision_id="pol-abc",
                ruleset_sha256=POLICY_RULESET_SHA256,
                allowed=False,
                refusal_reasons=("",),
            )

    def test_refusal_reason_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="refusal_reasons entries must be non-empty strings"):
            PolicyResult(
                decision_id="pol-abc",
                ruleset_sha256=POLICY_RULESET_SHA256,
                allowed=False,
                refusal_reasons=("  ",),
            )

    def test_allowed_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="allowed must be a bool"):
            PolicyResult(
                decision_id="pol-abc",
                ruleset_sha256=POLICY_RULESET_SHA256,
                allowed=None,
            )

    def test_refusal_reasons_must_be_tuple(self) -> None:
        with pytest.raises(ValueError, match="refusal_reasons must be a tuple of strings"):
            PolicyResult(
                decision_id="pol-abc",
                ruleset_sha256=POLICY_RULESET_SHA256,
                allowed=False,
                refusal_reasons=["list"],
            )

    def test_frozen(self) -> None:
        r = PolicyResult(
            decision_id="pol-abc",
            ruleset_sha256=POLICY_RULESET_SHA256,
            allowed=True,
        )
        with pytest.raises(FrozenInstanceError):
            r.allowed = False


# ---------------------------------------------------------------------------
# PolicyEngine construction
# ---------------------------------------------------------------------------


class TestPolicyEngineConstruction:
    def test_defaults(self) -> None:
        pe = PolicyEngine()
        assert pe.audit_available is True
        assert pe.required_tools == {}
        assert pe.ruleset_sha256 == POLICY_RULESET_SHA256

    def test_custom_ruleset_sha256(self) -> None:
        custom = "a" * 64
        pe = PolicyEngine(ruleset_sha256=custom)
        assert pe.ruleset_sha256 == custom

    def test_audit_available_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="audit_available must be a bool"):
            PolicyEngine(audit_available=None)

    def test_required_tools_must_be_dict(self) -> None:
        with pytest.raises(ValueError, match="required_tools must be a dict"):
            PolicyEngine(required_tools=["list"])

    def test_ruleset_sha256_wrong_length_raises(self) -> None:
        with pytest.raises(ValueError, match="ruleset_sha256 must be a 64-char hex digest"):
            PolicyEngine(ruleset_sha256="too-short")

    def test_ruleset_sha256_non_string_raises(self) -> None:
        with pytest.raises(ValueError, match="ruleset_sha256 must be a 64-char hex digest"):
            PolicyEngine(ruleset_sha256=42)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    task: ExpertTask = ExpertTask.QUESTION,
    deadline_s: int = 300,
    budget_usd: float = 1.0,
    data_classification: DataClassification = DataClassification.PUBLIC,
    offline: bool = False,
    allowed_tools: tuple[str, ...] = (),
) -> ExpertRequest:
    return ExpertRequest(
        request_id="req-1",
        tenant_id="tenant-1",
        task=task,
        query="test query",
        constraints=Constraints(
            deadline_s=deadline_s,
            budget_usd=budget_usd,
            data_classification=data_classification,
            offline=offline,
            allowed_tools=allowed_tools,
        ),
    )


# ---------------------------------------------------------------------------
# PolicyEngine.check_request — constraint checks
# ---------------------------------------------------------------------------


class TestCheckRequest:
    def test_allowed_for_basic_read_request(self) -> None:
        pe = PolicyEngine()
        req = _make_request()
        result = pe.check_request(req)
        assert result.allowed is True
        assert result.refusal_reasons == ()

    def test_decision_id_is_unique(self) -> None:
        pe = PolicyEngine()
        r1 = pe.check_request(_make_request())
        r2 = pe.check_request(_make_request())
        assert r1.decision_id != r2.decision_id
        assert r1.decision_id.startswith("pol-")

    def test_ruleset_sha256_in_result(self) -> None:
        pe = PolicyEngine()
        result = pe.check_request(_make_request())
        assert result.ruleset_sha256 == POLICY_RULESET_SHA256

    def test_deadline_s_zero_rejected(self) -> None:
        pe = PolicyEngine()
        req = _make_request()
        object.__setattr__(req.constraints, "deadline_s", 0)
        result = pe.check_request(req)
        assert result.allowed is False
        assert "deadline_s must be positive" in result.refusal_reasons

    def test_deadline_s_negative_rejected(self) -> None:
        pe = PolicyEngine()
        req = _make_request()
        object.__setattr__(req.constraints, "deadline_s", -5)
        result = pe.check_request(req)
        assert result.allowed is False
        assert "deadline_s must be positive" in result.refusal_reasons

    def test_train_with_zero_budget_rejected(self) -> None:
        pe = PolicyEngine()
        req = _make_request(task=ExpertTask.TRAIN, budget_usd=0.0)
        result = pe.check_request(req)
        assert result.allowed is False
        assert any("budget_usd must be > 0" in r for r in result.refusal_reasons)

    def test_deploy_with_zero_budget_rejected(self) -> None:
        pe = PolicyEngine()
        req = _make_request(task=ExpertTask.DEPLOY, budget_usd=0.0)
        result = pe.check_request(req)
        assert result.allowed is False
        assert any("budget_usd must be > 0" in r for r in result.refusal_reasons)

    def test_distill_with_zero_budget_rejected(self) -> None:
        pe = PolicyEngine()
        req = _make_request(task=ExpertTask.DISTILL, budget_usd=0.0)
        result = pe.check_request(req)
        assert result.allowed is False
        assert any("budget_usd must be > 0" in r for r in result.refusal_reasons)

    def test_dataset_no_budget_requirement(self) -> None:
        pe = PolicyEngine()
        req = _make_request(task=ExpertTask.DATASET, budget_usd=0.0)
        result = pe.check_request(req)
        assert result.allowed is True

    def test_question_no_budget_requirement(self) -> None:
        pe = PolicyEngine()
        req = _make_request(task=ExpertTask.QUESTION, budget_usd=0.0)
        result = pe.check_request(req)
        assert result.allowed is True

    def test_restricted_without_offline_rejected(self) -> None:
        pe = PolicyEngine()
        req = _make_request(
            data_classification=DataClassification.RESTRICTED,
            offline=False,
        )
        result = pe.check_request(req)
        assert result.allowed is False
        assert result.refusal_reasons[0].startswith("restricted data_classification requires offline=True")

    def test_restricted_with_offline_allowed(self) -> None:
        pe = PolicyEngine()
        req = _make_request(
            data_classification=DataClassification.RESTRICTED,
            offline=True,
        )
        result = pe.check_request(req)
        assert result.allowed is True

    def test_internal_without_offline_allowed(self) -> None:
        pe = PolicyEngine()
        req = _make_request(data_classification=DataClassification.INTERNAL)
        result = pe.check_request(req)
        assert result.allowed is True

    def test_required_tools_intersect_allowed(self) -> None:
        pe = PolicyEngine(required_tools={"train": ("gpu", "wandb")})
        req = _make_request(task=ExpertTask.TRAIN, allowed_tools=("gpu",))
        result = pe.check_request(req)
        assert result.allowed is True

    def test_required_tools_no_intersect_rejected(self) -> None:
        pe = PolicyEngine(required_tools={"train": ("gpu", "wandb")})
        req = _make_request(task=ExpertTask.TRAIN, allowed_tools=("cpu_only",))
        result = pe.check_request(req)
        assert result.allowed is False
        assert any("requires one of" in r for r in result.refusal_reasons)

    def test_required_tools_empty_allowed_set(self) -> None:
        pe = PolicyEngine(required_tools={"distill": ("v100",)})
        req = _make_request(task=ExpertTask.DISTILL, allowed_tools=())
        result = pe.check_request(req)
        assert result.allowed is False

    def test_required_tools_unknown_task_no_constraint(self) -> None:
        pe = PolicyEngine(required_tools={"train": ("gpu",)})
        req = _make_request(task=ExpertTask.QUESTION)
        result = pe.check_request(req)
        assert result.allowed is True

    def test_multiple_failures_accumulated(self) -> None:
        pe = PolicyEngine()
        req = _make_request(
            task=ExpertTask.TRAIN,
            budget_usd=0.0,
            data_classification=DataClassification.RESTRICTED,
            offline=False,
        )
        object.__setattr__(req.constraints, "deadline_s", -1)
        result = pe.check_request(req)
        assert result.allowed is False
        assert len(result.refusal_reasons) == 3


# ---------------------------------------------------------------------------
# PolicyEngine.check_mutation — fail-closed on audit unavailability
# ---------------------------------------------------------------------------


class TestCheckMutation:
    def test_mutation_with_audit_available_allowed(self) -> None:
        pe = PolicyEngine(audit_available=True)
        req = _make_request(task=ExpertTask.TRAIN, budget_usd=5.0)
        result = pe.check_mutation(req)
        assert result.allowed is True

    def test_mutation_with_audit_unavailable_refused(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.TRAIN, budget_usd=5.0)
        result = pe.check_mutation(req)
        assert result.allowed is False
        assert any("audit storage unavailable" in r for r in result.refusal_reasons)

    def test_deploy_mutation_audit_unavailable_refused(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.DEPLOY, budget_usd=5.0)
        result = pe.check_mutation(req)
        assert result.allowed is False
        assert any("audit storage unavailable" in r for r in result.refusal_reasons)

    def test_distill_mutation_audit_unavailable_refused(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.DISTILL, budget_usd=5.0)
        result = pe.check_mutation(req)
        assert result.allowed is False
        assert any("audit storage unavailable" in r for r in result.refusal_reasons)

    def test_dataset_mutation_audit_unavailable_refused(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.DATASET)
        result = pe.check_mutation(req)
        assert result.allowed is False
        assert any("audit storage unavailable" in r for r in result.refusal_reasons)

    def test_question_readonly_audit_unavailable_allowed(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.QUESTION)
        result = pe.check_mutation(req)
        assert result.allowed is True

    def test_research_readonly_audit_unavailable_allowed(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.RESEARCH)
        result = pe.check_mutation(req)
        assert result.allowed is True

    def test_evaluate_readonly_audit_unavailable_allowed(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.EVALUATE)
        result = pe.check_mutation(req)
        assert result.allowed is True

    def test_mutation_audit_unavailable_plus_constraint_failure(self) -> None:
        pe = PolicyEngine(audit_available=False)
        req = _make_request(task=ExpertTask.TRAIN, budget_usd=5.0)
        object.__setattr__(req.constraints, "deadline_s", -1)
        result = pe.check_mutation(req)
        assert result.allowed is False
        assert "deadline_s must be positive" in result.refusal_reasons
        assert any("audit storage unavailable" in r for r in result.refusal_reasons)
        assert len(result.refusal_reasons) == 2


# ---------------------------------------------------------------------------
# POLICY_RULESET_SHA256 stability
# ---------------------------------------------------------------------------


class TestPolicyRulesetSha256:
    def test_ruleset_is_64_char_hex(self) -> None:
        assert len(POLICY_RULESET_SHA256) == 64
        assert all(c in "0123456789abcdef" for c in POLICY_RULESET_SHA256)

    def test_ruleset_is_stable(self) -> None:
        assert POLICY_RULESET_SHA256 == "a28fa7b460d710e737b46d7d933129d1822350ed6929049ec9d54afda133a68f"
