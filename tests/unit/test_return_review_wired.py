from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn


class TestReturnReviewWired:
    def _make_reviewer(self, mock_gateway=None, mock_registry=None):
        return ReturnReviewer(
            gateway=mock_gateway or MagicMock(),
            prompt_registry=mock_registry or MagicMock(),
        )

    def test_review_calls_model_and_produces_decision(self):
        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"decision": "complete", "confidence": 0.9, "evidence_refs": ["test_output.txt"]}'
        mock_gateway.call_model = MagicMock(return_value=mock_response)
        mock_registry = MagicMock()
        mock_registry.render = MagicMock(return_value="Review this task return")

        reviewer = self._make_reviewer(mock_gateway, mock_registry)

        tr = TaskReturn(
            return_id="RET-001",
            todo_id="TODO-001",
            job_id="JOB-001",
            playbook="code",
            queue="core",
            exit_code=0,
            result_summary="Tests passed",
        )

        decision = reviewer.review_return(tr, [], ["test_output.txt"])
        assert decision.decision == "complete"
        assert decision.confidence == 0.9
        assert decision.evidence_refs == ["test_output.txt"]

    def test_review_model_failure_is_explicit_failed(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(side_effect=RuntimeError("API unavailable"))
        mock_registry = MagicMock()
        mock_registry.render = MagicMock(return_value="Review this")

        reviewer = self._make_reviewer(mock_gateway, mock_registry)

        tr = TaskReturn(
            return_id="RET-002",
            todo_id="TODO-002",
            job_id="JOB-002",
            playbook="code",
            queue="core",
        )

        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "failed"
        assert decision.confidence == 0.0
        assert "API unavailable" in str(decision.audit_notes)

    def test_review_parse_failure_returns_failed(self):
        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "not valid json at all"
        mock_gateway.call_model = MagicMock(return_value=mock_response)
        mock_registry = MagicMock()
        mock_registry.render = MagicMock(return_value="Review this")

        reviewer = self._make_reviewer(mock_gateway, mock_registry)

        tr = TaskReturn(
            return_id="RET-003",
            todo_id="TODO-003",
            job_id="JOB-003",
            playbook="code",
            queue="core",
        )

        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "failed"
        assert decision.confidence == 0.0

    def test_conversation_tracked_across_reviews(self):
        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"decision": "complete", "confidence": 0.8}'
        mock_gateway.call_model = MagicMock(return_value=mock_response)
        mock_registry = MagicMock()
        mock_registry.render = MagicMock(return_value="Review")

        reviewer = self._make_reviewer(mock_gateway, mock_registry)

        tr1 = TaskReturn(
            return_id="RET-A", todo_id="TODO-X", job_id="JOB-A", playbook="code", queue="core",
        )
        reviewer.review_return(tr1, [], [])
        convos = reviewer.get_conversations()
        assert "TODO-X" in convos
        assert len(convos["TODO-X"].messages) == 2

    def test_reconcile_requires_evidence(self):
        decision = TaskDecision(
            return_id="RET-004",
            matched_todo_id="TODO-004",
            decision="complete",
            confidence=0.9,
            evidence_refs=[],
        )
        assert decision.evidence_refs == []
        assert decision.decision == "complete"

    def test_reconcile_applies_real_decision(self):
        decision = TaskDecision(
            return_id="RET-005",
            matched_todo_id="TODO-005",
            decision="needs_more_work",
            confidence=0.7,
            evidence_refs=["test.log"],
        )
        assert decision.decision == "needs_more_work"
        assert decision.evidence_refs == ["test.log"]
