"""Unit tests for the ai_ml expert service (AIML-001/002/003/007/018).

Covers the top-five capabilities from docs/specs/FEATURE_AI_ML_EXPERT.md:
  - AIML-001 Expert router
  - AIML-002 Research discovery (evidence pipeline input shape)
  - AIML-003 Evidence store (immutable, deduped, citation-addressable)
  - AIML-007 Reasoning / answer (cited, uncertainty, independent check)
  - AIML-018 Tool discovery (decision record with rejected alternatives)

Pins acceptance criteria AIML-AT-001 (contract validation), AIML-AT-002
(deterministic dedupe), AIML-AT-007 (failed check never succeeds), and
AIML-AT-018 (decision record contents).
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.ai_ml import (
    EvidenceStore,
    ExpertRouter,
    answer_question,
    discover_tools,
)
from general_ludd.ai_ml.schemas import (
    ArtifactInput,
    Constraints,
    DataClassification,
    ExpertRequest,
    ExpertResult,
    ExpertTask,
    ResultStatus,
    ToolCandidate,
    Verification,
    VerificationStatus,
)


def _valid_request(**overrides: Any) -> ExpertRequest:
    """Return a minimally-valid ExpertRequest with optional overrides."""
    base: dict[str, Any] = {
        "request_id": "req-001",
        "tenant_id": "tenant-a",
        "task": ExpertTask.QUESTION,
        "query": "What is recall@10 of BM25 on MS MARCO?",
    }
    base.update(overrides)
    return ExpertRequest(**base)


# ---------------------------------------------------------------------------
# AIML-AT-001 — schema contract validation
# ---------------------------------------------------------------------------


class TestSchemaContracts:
    def test_valid_expert_request_constructs(self) -> None:
        req = _valid_request()
        assert req.schema_version == "1.0"
        assert req.task is ExpertTask.QUESTION
        assert req.constraints.deadline_s == 300
        assert req.constraints.budget_usd == 0
        assert req.constraints.data_classification is DataClassification.PUBLIC

    def test_invalid_task_enum_rejected(self) -> None:
        with pytest.raises(ValueError, match="task"):
            _valid_request(task="not_a_real_task")

    def test_invalid_data_classification_rejected(self) -> None:
        with pytest.raises(ValueError, match="data_classification"):
            Constraints(data_classification="top_secret")  # type: ignore[arg-type]

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="budget_usd"):
            Constraints(budget_usd=-0.01)

    def test_negative_gpu_hours_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_gpu_hours"):
            Constraints(max_gpu_hours=-1)

    def test_missing_digest_rejected(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ArtifactInput(uri="artifact://x", media_type="text/plain", sha256="")

    def test_invalid_sha256_format_rejected(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ArtifactInput(uri="artifact://x", media_type="text/plain", sha256="nothex")

    def test_empty_query_rejected(self) -> None:
        with pytest.raises(ValueError, match="query"):
            _valid_request(query="")

    def test_empty_tenant_rejected(self) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            _valid_request(tenant_id="")

    def test_invalid_result_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            ExpertResult(
                request_id="r",
                run_id="u",
                status="ok",  # type: ignore[arg-type]
            )

    def test_uncertainty_score_bounds(self) -> None:
        from general_ludd.ai_ml.schemas import Uncertainty

        Uncertainty(score=0.0, method="test")
        Uncertainty(score=1.0, method="test")
        with pytest.raises(ValueError, match="score"):
            Uncertainty(score=1.5, method="test")
        with pytest.raises(ValueError, match="score"):
            Uncertainty(score=-0.1, method="test")


# ---------------------------------------------------------------------------
# AIML-001 — Expert router
# ---------------------------------------------------------------------------


class TestExpertRouter:
    def test_question_routes_to_research_answer(self) -> None:
        router = ExpertRouter()
        decision = router.route(_valid_request())
        assert "research_answer" in decision.matched_roles
        assert decision.refusal_reason is None

    def test_research_routes_to_refresh_and_answer(self) -> None:
        router = ExpertRouter()
        decision = router.route(_valid_request(task=ExpertTask.RESEARCH))
        assert "research_refresh" in decision.matched_roles
        assert "research_answer" in decision.matched_roles

    def test_router_returns_smallest_qualified_role_set(self) -> None:
        router = ExpertRouter()
        decision = router.route(_valid_request())
        # A pure question routes only to research_answer, not the full set.
        assert decision.matched_roles == ("research_answer",)

    def test_router_refuses_unsatisfiable_constraint(self) -> None:
        # offline=True + a research/question task is unsatisfiable: the
        # answer cannot be grounded without online retrieval (spec §11).
        router = ExpertRouter()
        req = _valid_request(constraints=Constraints(offline=True))
        decision = router.route(req)
        assert decision.matched_roles == ()
        assert decision.refusal_reason is not None
        assert "offline" in decision.refusal_reason.lower()

    def test_router_refuses_mutation_without_approval_token(self) -> None:
        router = ExpertRouter()
        req = _valid_request(
            task=ExpertTask.TRAIN,
            approval_token=None,
        )
        decision = router.route(req)
        # train is a mutation task: missing approval_token => refused.
        assert decision.matched_roles == ()
        assert decision.refusal_reason is not None
        assert "approval" in decision.refusal_reason.lower()

    def test_router_accepts_mutation_with_approval_token(self) -> None:
        router = ExpertRouter()
        req = _valid_request(
            task=ExpertTask.TRAIN,
            approval_token="approval-xyz",
        )
        decision = router.route(req)
        assert "adapter_train" in decision.matched_roles
        assert decision.refusal_reason is None

    def test_router_request_id_echoed(self) -> None:
        router = ExpertRouter()
        decision = router.route(_valid_request())
        assert decision.request_id == "req-001"


# ---------------------------------------------------------------------------
# AIML-002 / AIML-003 — Evidence store
# ---------------------------------------------------------------------------


class TestEvidenceStore:
    def test_ingest_returns_immutable_record_with_locator(self) -> None:
        store = EvidenceStore()
        ev = store.ingest(
            content=b"hello world",
            media_type="text/plain",
            license="MIT",
            locator="https://example.com/paper#section-2",
        )
        assert ev.source_id.startswith("evd-")
        assert len(ev.sha256) == 64
        assert ev.license == "MIT"
        assert ev.locators == ("https://example.com/paper#section-2",)
        assert ev.fetched_at > 0

    def test_ingest_dedupes_by_sha256_one_artifact_many_locators(self, capsys: pytest.CaptureFixture[str]) -> None:
        # AIML-AT-002: duplicate content creates one artifact and multiple
        # source locators.
        store = EvidenceStore()
        content = b"identical bytes"
        ev1 = store.ingest(content=content, media_type="text/plain", license="CC-BY-4.0", locator="loc-A")
        ev2 = store.ingest(content=content, media_type="text/plain", license="CC-BY-4.0", locator="loc-B")
        assert ev1.sha256 == ev2.sha256
        assert ev1.source_id == ev2.source_id
        # The store exposes all known locators for the deduped artifact.
        record = store.get(ev1.source_id)
        assert set(record.locators) == {"loc-A", "loc-B"}
        assert len(store.list_all()) == 1

    def test_get_unknown_source_id_returns_none(self) -> None:
        store = EvidenceStore()
        assert store.get("evd-nonexistent") is None

    def test_store_is_tenant_scoped(self) -> None:
        store = EvidenceStore()
        store.ingest(
            content=b"x",
            media_type="text/plain",
            license="MIT",
            locator="loc",
            tenant_id="tenant-A",
        )
        # tenant-B cannot see tenant-A's evidence.
        assert store.list_all(tenant_id="tenant-B") == []
        assert len(store.list_all(tenant_id="tenant-A")) == 1


# ---------------------------------------------------------------------------
# AIML-007 — Reasoning / answer
# ---------------------------------------------------------------------------


class TestAnswerQuestion:
    def test_answer_includes_citation_and_uncertainty(self) -> None:
        store = EvidenceStore()
        ev = store.ingest(
            content=b"BM25 recall@10 on MS MARCO is ~0.76",
            media_type="text/plain",
            license="MIT",
            locator="https://example.com/bm25#results",
        )
        req = _valid_request(tenant_id=ev.tenant_id)
        # The query contains "recall" -> numerical claim requiring an
        # independent check. Supply a passing one so the answer may be
        # marked SUCCEEDED (spec §6.3).
        passing_check = Verification(
            check="independent numerical verification",
            status=VerificationStatus.PASS,
            artifact_uri="artifact://check",
        )
        result = answer_question(req, store, extra_verifications=[passing_check])
        assert result.status is ResultStatus.SUCCEEDED
        assert result.answer is not None
        assert len(result.citations) >= 1
        assert result.citations[0].source_id == ev.source_id
        assert 0.0 <= result.uncertainty.score <= 1.0
        assert result.uncertainty.method

    def test_failed_verification_never_succeeds(self) -> None:
        # AIML-AT-007: failed checks produce degraded/failed, never succeeded.
        store = EvidenceStore()
        ev = store.ingest(
            content=b"some claim",
            media_type="text/plain",
            license="MIT",
            locator="loc",
        )
        req = _valid_request(tenant_id=ev.tenant_id)
        # Inject a failing independent check.
        failing_check = Verification(
            check="independent numerical verification",
            status=VerificationStatus.FAIL,
            artifact_uri="artifact://check",
        )
        result = answer_question(req, store, extra_verifications=[failing_check])
        assert result.status is not ResultStatus.SUCCEEDED
        assert result.status in (ResultStatus.DEGRADED, ResultStatus.FAILED)

    def test_answer_without_evidence_refuses_or_degrades(self) -> None:
        store = EvidenceStore()
        req = _valid_request()
        result = answer_question(req, store)
        # No evidence to ground an answer: never succeeded.
        assert result.status is not ResultStatus.SUCCEEDED
        assert result.status in (ResultStatus.REFUSED, ResultStatus.DEGRADED)


# ---------------------------------------------------------------------------
# AIML-018 — Tool discovery
# ---------------------------------------------------------------------------


class TestDiscoverTools:
    def test_discover_returns_decision_record_with_rejected_alternatives(self) -> None:
        # AIML-AT-018: decision record includes maintenance, license,
        # security, forum-issue, and exit-strategy evidence; rejected
        # alternatives are retained.
        registry = [
            ToolCandidate(
                capability_id="vector.ann",
                name="GoodIndex",
                version="1.2.0",
                license="Apache-2.0",
                maintenance_score=0.9,
                security_score=0.8,
                task_fit_score=0.85,
                has_exit_strategy=True,
            ),
            ToolCandidate(
                capability_id="vector.ann",
                name="StaleIndex",
                version="0.1.0",
                license="GPL-3.0-only",
                maintenance_score=0.1,
                security_score=0.3,
                task_fit_score=0.5,
                has_exit_strategy=False,
            ),
        ]
        record = discover_tools("hybrid vector retrieval", registry)
        assert any(c.name == "GoodIndex" for c in record.selected)
        assert any(c.name == "StaleIndex" for c in record.rejected_alternatives)
        assert record.integration_spike_required is True

    def test_discover_rejects_low_maintenance_even_if_popular(self) -> None:
        # Spec §9: popularity alone is not a selection criterion.
        registry = [
            ToolCandidate(
                capability_id="vector.ann",
                name="PopularButStale",
                version="9.9.9",
                license="MIT",
                maintenance_score=0.2,
                security_score=0.9,
                task_fit_score=0.95,
                has_exit_strategy=True,
            ),
        ]
        record = discover_tools("vector retrieval", registry, min_maintenance_score=0.5)
        assert record.selected == ()
        assert len(record.rejected_alternatives) == 1
        assert "maintenance" in record.rejected_alternatives[0].rejection_reason.lower()
