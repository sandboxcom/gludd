"""E2E tests for the general_ludd.ai_ml expert collection.

Verifies the public API surface end-to-end: package imports, ExpertRequest
construction, routing through ExpertRouter, and the ExpertResult contract shape.
"""

from __future__ import annotations

import pytest


class TestAIMLPackageImports:
    """Verify the ai_ml package imports cleanly and exposes its API."""

    def test_package_imports_cleanly(self) -> None:
        import general_ludd.ai_ml as ai_ml

        assert ai_ml is not None

    def test_package_exports_request_and_result(self) -> None:
        from general_ludd.ai_ml import ExpertRequest, ExpertResult

        assert ExpertRequest is not None
        assert ExpertResult is not None

    def test_package_exports_router(self) -> None:
        from general_ludd.ai_ml import ExpertRouter

        assert ExpertRouter is not None


class TestExpertRequestConstruction:
    """Verify ExpertRequest builds and enforces its invariants."""

    def test_minimal_request_constructs(self) -> None:
        from general_ludd.ai_ml import ExpertRequest, ExpertTask

        req = ExpertRequest(
            request_id="req-001",
            tenant_id="tenant-a",
            task=ExpertTask.QUESTION,
            query="What is the recall of model X?",
        )
        assert req.request_id == "req-001"
        assert req.task == ExpertTask.QUESTION
        assert req.query == "What is the recall of model X?"

    def test_request_rejects_empty_request_id(self) -> None:
        from general_ludd.ai_ml import ExpertRequest, ExpertTask

        with pytest.raises(ValueError):
            ExpertRequest(
                request_id="",
                tenant_id="tenant-a",
                task=ExpertTask.QUESTION,
                query="some query",
            )

    def test_request_rejects_empty_query(self) -> None:
        from general_ludd.ai_ml import ExpertRequest, ExpertTask

        with pytest.raises(ValueError):
            ExpertRequest(
                request_id="req-002",
                tenant_id="tenant-a",
                task=ExpertTask.QUESTION,
                query="",
            )

    def test_constraints_default_to_public(self) -> None:
        from general_ludd.ai_ml import Constraints, ExpertRequest, ExpertTask

        req = ExpertRequest(
            request_id="req-003",
            tenant_id="tenant-a",
            task=ExpertTask.QUESTION,
            query="query",
        )
        assert req.constraints.data_classification.value == "public"


class TestExpertRouter:
    """Verify ExpertRouter.route() returns typed RouterDecision."""

    def test_route_question_returns_role(self) -> None:
        from general_ludd.ai_ml import ExpertRequest, ExpertRouter, ExpertTask

        router = ExpertRouter()
        req = ExpertRequest(
            request_id="req-001",
            tenant_id="tenant-a",
            task=ExpertTask.QUESTION,
            query="What is recall?",
        )
        decision = router.route(req)
        assert decision.request_id == "req-001"
        assert len(decision.matched_roles) > 0
        assert decision.refusal_reason is None

    def test_route_mutation_without_token_refused(self) -> None:
        from general_ludd.ai_ml import ExpertRequest, ExpertRouter, ExpertTask

        router = ExpertRouter()
        req = ExpertRequest(
            request_id="req-002",
            tenant_id="tenant-a",
            task=ExpertTask.TRAIN,
            query="Fine-tune model X",
        )
        decision = router.route(req)
        assert decision.refusal_reason is not None
        assert decision.matched_roles == ()

    def test_route_offline_question_refused(self) -> None:
        from general_ludd.ai_ml import Constraints, ExpertRequest, ExpertRouter, ExpertTask

        router = ExpertRouter()
        req = ExpertRequest(
            request_id="req-003",
            tenant_id="tenant-a",
            task=ExpertTask.QUESTION,
            query="research query",
            constraints=Constraints(offline=True),
        )
        decision = router.route(req)
        assert decision.refusal_reason is not None


class TestExpertResultContract:
    """Verify ExpertResult carries answer, citations, and uncertainty."""

    def test_result_has_required_structure(self) -> None:
        from general_ludd.ai_ml import ExpertResult, ResultStatus

        result = ExpertResult(
            request_id="req-001",
            run_id="run-001",
            status=ResultStatus.SUCCEEDED,
        )
        assert result.request_id == "req-001"
        assert result.run_id == "run-001"
        assert result.status.value == "succeeded"
        assert isinstance(result.citations, tuple)
        assert isinstance(result.verification, tuple)
        assert isinstance(result.errors, tuple)
        assert result.uncertainty is not None
        assert result.cost is not None
