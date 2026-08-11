"""Unit tests for AI/ML schemas — validation, coercion, edge cases.

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §4.1-4.3:
  - AIML-AT-001: contract validation (invalid enums, missing digests,
    negative budgets, out-of-range scores, empty fields → ValueError)
  - AIML-AT-002: evidence deduplication contract shape
  - All frozen dataclass __post_init__ guards
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from general_ludd.ai_ml.schemas import (
    ArtifactInput,
    ArtifactOutput,
    Citation,
    Constraints,
    CostRecord,
    DataClassification,
    ErrorRecord,
    EvidenceArtifact,
    ExpertRequest,
    ExpertResult,
    ExpertTask,
    PolicyDecision,
    ResultStatus,
    RouterDecision,
    ToolCandidate,
    ToolDecisionRecord,
    Uncertainty,
    Verification,
    VerificationStatus,
    _coerce_enum,
    _require_sha256,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_SHA256 = "a" * 64  # 64 lowercase hex chars


def _valid_evidence(**overrides: Any) -> EvidenceArtifact:
    ts = int(time.time())
    kwargs: dict[str, Any] = {
        "source_id": "src-1",
        "sha256": _VALID_SHA256,
        "media_type": "text/plain",
        "locators": ("https://example.com/doc",),
        "fetched_at": ts,
        "license": "CC-BY-4.0",
        **overrides,
    }
    return EvidenceArtifact(**kwargs)


# ---------------------------------------------------------------------------
# _coerce_enum
# ---------------------------------------------------------------------------


class TestCoerceEnum:
    def test_passes_through_enum_member(self) -> None:
        result = _coerce_enum(ExpertTask.QUESTION, ExpertTask, "task")
        assert result is ExpertTask.QUESTION

    def test_coerces_string_to_enum(self) -> None:
        result = _coerce_enum("question", ExpertTask, "task")
        assert result is ExpertTask.QUESTION

    def test_raises_on_invalid_string(self) -> None:
        with pytest.raises(ValueError, match="invalid task"):
            _coerce_enum("bogus", ExpertTask, "task")

    def test_raises_on_int(self) -> None:
        with pytest.raises(ValueError, match="invalid task"):
            _coerce_enum(42, ExpertTask, "task")


# ---------------------------------------------------------------------------
# _require_sha256
# ---------------------------------------------------------------------------


class TestRequireSha256:
    def test_accepts_valid_sha256(self) -> None:
        _require_sha256(_VALID_SHA256, "sha256")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            _require_sha256("", "sha256")

    def test_rejects_non_hex(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            _require_sha256("g" * 64, "sha256")

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            _require_sha256("a" * 63, "sha256")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            _require_sha256("A" * 64, "sha256")


# ---------------------------------------------------------------------------
# ArtifactInput
# ---------------------------------------------------------------------------


class TestArtifactInput:
    def test_valid_construction(self) -> None:
        a = ArtifactInput(uri="s3://bucket/model.pt", media_type="application/octet-stream", sha256=_VALID_SHA256)
        assert a.uri == "s3://bucket/model.pt"

    def test_rejects_empty_uri(self) -> None:
        with pytest.raises(ValueError, match="uri"):
            ArtifactInput(uri="", media_type="text/plain", sha256=_VALID_SHA256)

    def test_rejects_empty_media_type(self) -> None:
        with pytest.raises(ValueError, match="media_type"):
            ArtifactInput(uri="s3://x", media_type="", sha256=_VALID_SHA256)

    def test_rejects_bad_sha256(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ArtifactInput(uri="s3://x", media_type="text/plain", sha256="deadbeef")


# ---------------------------------------------------------------------------
# ArtifactOutput
# ---------------------------------------------------------------------------


class TestArtifactOutput:
    def test_valid_construction(self) -> None:
        a = ArtifactOutput(uri="s3://bucket/out.pt", sha256=_VALID_SHA256, media_type="application/octet-stream")
        assert a.uri == "s3://bucket/out.pt"

    def test_rejects_empty_sha256(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ArtifactOutput(uri="s3://x", sha256="", media_type="text/plain")


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


class TestConstraints:
    def test_defaults(self) -> None:
        c = Constraints()
        assert c.deadline_s == 300
        assert c.budget_usd == 0.0
        assert c.max_gpu_hours == 0.0
        assert c.data_classification is DataClassification.PUBLIC
        assert c.offline is False

    def test_rejects_negative_deadline(self) -> None:
        with pytest.raises(ValueError, match="deadline_s"):
            Constraints(deadline_s=0)

    def test_rejects_negative_budget(self) -> None:
        with pytest.raises(ValueError, match="budget_usd"):
            Constraints(budget_usd=-1.0)

    def test_rejects_negative_gpu_hours(self) -> None:
        with pytest.raises(ValueError, match="max_gpu_hours"):
            Constraints(max_gpu_hours=-1.0)

    def test_coerces_data_classification_string(self) -> None:
        c = Constraints(data_classification="confidential")
        assert c.data_classification is DataClassification.CONFIDENTIAL

    def test_rejects_invalid_classification(self) -> None:
        with pytest.raises(ValueError, match="data_classification"):
            Constraints(data_classification="top_secret")

    def test_rejects_restricted_enum(self) -> None:
        with pytest.raises(ValueError, match="data_classification"):
            Constraints(data_classification=42)


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------


class TestCitation:
    def test_valid(self) -> None:
        c = Citation(source_id="src-1", locator="https://example.com/doc#section-3", claim_ids=("claim-1",))
        assert c.source_id == "src-1"
        assert c.claim_ids == ("claim-1",)

    def test_default_claim_ids(self) -> None:
        c = Citation(source_id="src-1", locator="https://example.com")
        assert c.claim_ids == ()

    def test_rejects_empty_source_id(self) -> None:
        with pytest.raises(ValueError, match="source_id"):
            Citation(source_id="", locator="https://example.com")

    def test_rejects_empty_locator(self) -> None:
        with pytest.raises(ValueError, match="locator"):
            Citation(source_id="src-1", locator="")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class TestVerification:
    def test_valid_pass(self) -> None:
        v = Verification(check="independent numerical verification", status=VerificationStatus.PASS)
        assert v.status is VerificationStatus.PASS
        assert v.artifact_uri is None

    def test_valid_with_artifact(self) -> None:
        v = Verification(check="audit", status=VerificationStatus.FAIL, artifact_uri="s3://b/report.json")
        assert v.artifact_uri == "s3://b/report.json"

    def test_coerces_status_string(self) -> None:
        v = Verification(check="audit", status="not_run")
        assert v.status is VerificationStatus.NOT_RUN

    def test_rejects_empty_check(self) -> None:
        with pytest.raises(ValueError, match="check"):
            Verification(check="", status="pass")

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValueError, match="status"):
            Verification(check="audit", status="bogus")


# ---------------------------------------------------------------------------
# Uncertainty
# ---------------------------------------------------------------------------


class TestUncertainty:
    def test_valid(self) -> None:
        u = Uncertainty(score=0.5, method="confidence_interval", limitations=("small sample",))
        assert u.score == 0.5
        assert u.limitations == ("small sample",)

    def test_score_zero_boundary(self) -> None:
        u = Uncertainty(score=0.0, method="none")
        assert u.score == 0.0

    def test_score_one_boundary(self) -> None:
        u = Uncertainty(score=1.0, method="certain")
        assert u.score == 1.0

    def test_rejects_negative_score(self) -> None:
        with pytest.raises(ValueError, match="score"):
            Uncertainty(score=-0.1, method="bad")

    def test_rejects_score_above_one(self) -> None:
        with pytest.raises(ValueError, match="score"):
            Uncertainty(score=1.1, method="bad")

    def test_rejects_empty_method(self) -> None:
        with pytest.raises(ValueError, match="method"):
            Uncertainty(score=0.5, method="")


# ---------------------------------------------------------------------------
# CostRecord
# ---------------------------------------------------------------------------


class TestCostRecord:
    def test_defaults(self) -> None:
        c = CostRecord()
        assert c.usd == 0.0
        assert c.gpu_seconds == 0
        assert c.tokens == 0

    def test_valid_with_values(self) -> None:
        c = CostRecord(usd=1.5, gpu_seconds=3600, tokens=100000)
        assert c.usd == 1.5

    def test_rejects_negative_usd(self) -> None:
        with pytest.raises(ValueError, match="usd"):
            CostRecord(usd=-0.01)

    def test_rejects_negative_gpu_seconds(self) -> None:
        with pytest.raises(ValueError, match="gpu_seconds"):
            CostRecord(gpu_seconds=-1)

    def test_rejects_negative_tokens(self) -> None:
        with pytest.raises(ValueError, match="tokens"):
            CostRecord(tokens=-1)


# ---------------------------------------------------------------------------
# PolicyDecision
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def test_valid(self) -> None:
        p = PolicyDecision(decision_id="dec-1", ruleset_sha256=_VALID_SHA256)
        assert p.decision_id == "dec-1"

    def test_rejects_empty_decision_id(self) -> None:
        with pytest.raises(ValueError, match="decision_id"):
            PolicyDecision(decision_id="", ruleset_sha256=_VALID_SHA256)

    def test_rejects_bad_ruleset_sha256(self) -> None:
        with pytest.raises(ValueError, match="ruleset_sha256"):
            PolicyDecision(decision_id="dec-1", ruleset_sha256="bad")


# ---------------------------------------------------------------------------
# ErrorRecord
# ---------------------------------------------------------------------------


class TestErrorRecord:
    def test_valid(self) -> None:
        e = ErrorRecord(code="E_CONN", retryable=True, message="connection refused")
        assert e.code == "E_CONN"

    def test_rejects_empty_code(self) -> None:
        with pytest.raises(ValueError, match="code"):
            ErrorRecord(code="", retryable=False, message="err")

    def test_rejects_empty_message(self) -> None:
        with pytest.raises(ValueError, match="message"):
            ErrorRecord(code="E1", retryable=False, message="")


# ---------------------------------------------------------------------------
# ExpertRequest
# ---------------------------------------------------------------------------


class TestExpertRequest:
    def test_defaults(self) -> None:
        r = ExpertRequest(request_id="req-1", tenant_id="t-1", query="What is BERT?")
        assert r.task is ExpertTask.QUESTION
        assert r.schema_version == "1.0"

    def test_rejects_empty_request_id(self) -> None:
        with pytest.raises(ValueError, match="request_id"):
            ExpertRequest(request_id="", tenant_id="t-1", query="q")

    def test_rejects_empty_tenant_id(self) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            ExpertRequest(request_id="r-1", tenant_id="", query="q")

    def test_rejects_empty_query(self) -> None:
        with pytest.raises(ValueError, match="query"):
            ExpertRequest(request_id="r-1", tenant_id="t-1", query="")

    def test_coerces_task_string(self) -> None:
        r = ExpertRequest(request_id="r-1", tenant_id="t-1", query="q", task="train")
        assert r.task is ExpertTask.TRAIN

    def test_rejects_invalid_task(self) -> None:
        with pytest.raises(ValueError, match="task"):
            ExpertRequest(request_id="r-1", tenant_id="t-1", query="q", task="fly")

    def test_rejects_non_constraints_instance(self) -> None:
        with pytest.raises(ValueError, match="constraints"):
            ExpertRequest(request_id="r-1", tenant_id="t-1", query="q", constraints=42)  # type: ignore[arg-type]

    def test_with_inputs_and_approval(self) -> None:
        inp = ArtifactInput(uri="s3://b/data.csv", media_type="text/csv", sha256=_VALID_SHA256)
        r = ExpertRequest(
            request_id="r-1",
            tenant_id="t-1",
            query="train model",
            task="train",
            inputs=(inp,),
            approval_token="tok-abc",
        )
        assert r.inputs == (inp,)
        assert r.approval_token == "tok-abc"


# ---------------------------------------------------------------------------
# ExpertResult
# ---------------------------------------------------------------------------


class TestExpertResult:
    def test_defaults(self) -> None:
        r = ExpertResult(request_id="r-1", run_id="run-1")
        assert r.status is ResultStatus.SUCCEEDED

    def test_rejects_empty_request_id(self) -> None:
        with pytest.raises(ValueError, match="request_id"):
            ExpertResult(request_id="", run_id="run-1")

    def test_rejects_empty_run_id(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            ExpertResult(request_id="r-1", run_id="")

    def test_coerces_status_string(self) -> None:
        r = ExpertResult(request_id="r-1", run_id="run-1", status="failed")
        assert r.status is ResultStatus.FAILED

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValueError, match="status"):
            ExpertResult(request_id="r-1", run_id="run-1", status="crash")


# ---------------------------------------------------------------------------
# EvidenceArtifact
# ---------------------------------------------------------------------------


class TestEvidenceArtifact:
    ts = int(time.time())

    def test_valid_minimal(self) -> None:
        e = _valid_evidence()
        assert e.source_id == "src-1"
        assert e.authority_score == 0.0
        assert e.tenant_id == "default"

    def test_all_fields_set(self) -> None:
        e = _valid_evidence(
            authority_score=0.9,
            tenant_id="tenant-x",
            creator="alice",
            supersedes="src-0",
            retracted=False,
        )
        assert e.creator == "alice"
        assert e.supersedes == "src-0"

    def test_rejects_empty_source_id(self) -> None:
        with pytest.raises(ValueError, match="source_id"):
            _valid_evidence(source_id="")

    def test_rejects_empty_media_type(self) -> None:
        with pytest.raises(ValueError, match="media_type"):
            _valid_evidence(media_type="")

    def test_rejects_bad_sha256(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            _valid_evidence(sha256="bad")

    def test_rejects_empty_locators(self) -> None:
        with pytest.raises(ValueError, match="locator"):
            _valid_evidence(locators=())

    def test_rejects_authority_score_negative(self) -> None:
        with pytest.raises(ValueError, match="authority_score"):
            _valid_evidence(authority_score=-0.1)

    def test_rejects_authority_score_above_one(self) -> None:
        with pytest.raises(ValueError, match="authority_score"):
            _valid_evidence(authority_score=1.1)

    def test_retracted_requires_reason(self) -> None:
        with pytest.raises(ValueError, match="retraction_reason"):
            _valid_evidence(retracted=True, retraction_reason="")

    def test_retracted_requires_retracted_at(self) -> None:
        with pytest.raises(ValueError, match="retracted_at"):
            _valid_evidence(retracted=True, retraction_reason="wrong", retracted_at=None)

    def test_retracted_at_must_be_nonnegative(self) -> None:
        with pytest.raises(ValueError, match="retracted_at"):
            _valid_evidence(retracted=True, retraction_reason="wrong", retracted_at=-1)

    def test_valid_retracted(self) -> None:
        e = _valid_evidence(retracted=True, retraction_reason="outdated", retracted_at=self.ts)
        assert e.retracted is True
        assert e.retraction_reason == "outdated"

    def test_supersedes_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="supersedes"):
            _valid_evidence(supersedes="")

    def test_supersedes_none_is_ok(self) -> None:
        e = _valid_evidence(supersedes=None)
        assert e.supersedes is None


# ---------------------------------------------------------------------------
# ToolCandidate
# ---------------------------------------------------------------------------


class TestToolCandidate:
    def test_valid(self) -> None:
        t = ToolCandidate(
            capability_id="cap-1",
            name="MyTool",
            version="2.0",
            license="MIT",
            maintenance_score=0.8,
            security_score=0.7,
            task_fit_score=0.9,
            has_exit_strategy=True,
        )
        assert t.name == "MyTool"

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="name"):
            ToolCandidate(
                capability_id="c1",
                name="",
                version="1",
                license="MIT",
                maintenance_score=0.5,
                security_score=0.5,
                task_fit_score=0.5,
            )

    def test_rejects_empty_version(self) -> None:
        with pytest.raises(ValueError, match="version"):
            ToolCandidate(
                capability_id="c1",
                name="T",
                version="",
                license="MIT",
                maintenance_score=0.5,
                security_score=0.5,
                task_fit_score=0.5,
            )

    def test_rejects_empty_license(self) -> None:
        with pytest.raises(ValueError, match="license"):
            ToolCandidate(
                capability_id="c1",
                name="T",
                version="1",
                license="",
                maintenance_score=0.5,
                security_score=0.5,
                task_fit_score=0.5,
            )

    def test_rejects_maintenance_score_negative(self) -> None:
        with pytest.raises(ValueError, match="maintenance_score"):
            ToolCandidate(
                capability_id="c1",
                name="T",
                version="1",
                license="MIT",
                maintenance_score=-0.1,
                security_score=0.5,
                task_fit_score=0.5,
            )

    def test_rejects_security_score_above_one(self) -> None:
        with pytest.raises(ValueError, match="security_score"):
            ToolCandidate(
                capability_id="c1",
                name="T",
                version="1",
                license="MIT",
                maintenance_score=0.5,
                security_score=1.1,
                task_fit_score=0.5,
            )

    def test_rejects_task_fit_score_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="task_fit_score"):
            ToolCandidate(
                capability_id="c1",
                name="T",
                version="1",
                license="MIT",
                maintenance_score=0.5,
                security_score=0.5,
                task_fit_score=1.5,
            )

    def test_rejects_task_fit_score_negative(self) -> None:
        with pytest.raises(ValueError, match="task_fit_score"):
            ToolCandidate(
                capability_id="c1",
                name="T",
                version="1",
                license="MIT",
                maintenance_score=0.5,
                security_score=0.5,
                task_fit_score=-0.2,
            )

    def test_default_rejection_reason_empty(self) -> None:
        t = ToolCandidate(
            capability_id="c1",
            name="T",
            version="1",
            license="MIT",
            maintenance_score=0.5,
            security_score=0.5,
            task_fit_score=0.5,
        )
        assert t.rejection_reason == ""

    def test_score_boundaries_zero(self) -> None:
        t = ToolCandidate(
            capability_id="c1",
            name="T",
            version="1",
            license="MIT",
            maintenance_score=0.0,
            security_score=0.0,
            task_fit_score=0.0,
        )
        assert t.maintenance_score == 0.0

    def test_score_boundaries_one(self) -> None:
        t = ToolCandidate(
            capability_id="c1",
            name="T",
            version="1",
            license="MIT",
            maintenance_score=1.0,
            security_score=1.0,
            task_fit_score=1.0,
        )
        assert t.task_fit_score == 1.0


# ---------------------------------------------------------------------------
# ToolDecisionRecord
# ---------------------------------------------------------------------------


class TestToolDecisionRecord:
    def test_valid_empty(self) -> None:
        r = ToolDecisionRecord(need="image classification", selected=(), rejected_alternatives=())
        assert r.need == "image classification"
        assert r.selected == ()
        assert r.integration_spike_required is True

    def test_rejects_empty_need(self) -> None:
        with pytest.raises(ValueError, match="need"):
            ToolDecisionRecord(need="", selected=(), rejected_alternatives=())

    def test_valid_with_candidates(self) -> None:
        tool = ToolCandidate(
            capability_id="c1",
            name="T",
            version="1",
            license="MIT",
            maintenance_score=0.5,
            security_score=0.5,
            task_fit_score=0.5,
        )
        r = ToolDecisionRecord(need="nlp", selected=(tool,), rejected_alternatives=())
        assert r.selected == (tool,)


# ---------------------------------------------------------------------------
# RouterDecision
# ---------------------------------------------------------------------------


class TestRouterDecision:
    def test_valid_matched(self) -> None:
        d = RouterDecision(request_id="r-1", matched_roles=("research_answer",))
        assert d.matched_roles == ("research_answer",)
        assert d.refusal_reason is None

    def test_valid_refusal(self) -> None:
        d = RouterDecision(request_id="r-2", matched_roles=(), refusal_reason="offline constraint violation")
        assert d.refusal_reason == "offline constraint violation"

    def test_rejects_empty_request_id(self) -> None:
        with pytest.raises(ValueError, match="request_id"):
            RouterDecision(request_id="", matched_roles=())


# ---------------------------------------------------------------------------
# Enum exhaustiveness
# ---------------------------------------------------------------------------


class TestEnums:
    def test_expert_task_values(self) -> None:
        expected = {
            "question",
            "research",
            "dataset",
            "train",
            "distill",
            "speech",
            "vision",
            "image",
            "world_model",
            "simulate",
            "evaluate",
            "deploy",
        }
        assert {t.value for t in ExpertTask} == expected

    def test_result_status_values(self) -> None:
        expected = {"succeeded", "degraded", "refused", "failed", "awaiting_approval"}
        assert {s.value for s in ResultStatus} == expected

    def test_data_classification_values(self) -> None:
        expected = {"public", "internal", "confidential", "restricted"}
        assert {c.value for c in DataClassification} == expected

    def test_verification_status_values(self) -> None:
        expected = {"pass", "fail", "not_run"}
        assert {v.value for v in VerificationStatus} == expected
