"""Unit tests for the return review pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.db.repository import TodoRepository
from general_ludd.models.gateway import ModelGateway
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.review.decision_applier import apply_decision
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn


def _make_task_return(**overrides: object) -> TaskReturn:
    defaults = {
        "return_id": "RET-001",
        "todo_id": "TODO-001",
        "job_id": "JOB-001",
        "playbook": "test_playbook",
        "queue": "core",
        "result_summary": "All tests passed",
        "exit_code": 0,
        "artifacts": ["logs.txt", "coverage.xml"],
    }
    defaults.update(overrides)
    return TaskReturn(**defaults)


def _make_decision(**overrides: object) -> TaskDecision:
    defaults = {
        "return_id": "RET-001",
        "matched_todo_id": "TODO-001",
        "decision": "complete",
        "confidence": 0.95,
        "evidence_refs": ["coverage.xml", "test_results.xml"],
    }
    defaults.update(overrides)
    return TaskDecision(**defaults)


class TestReturnReviewer:
    def test_base_harness_aware_prompt_template_exists(self):
        reg = PromptRegistry(template_dir="templates/prompts")
        rendered = reg.render("base_harness_aware.md.j2")
        assert "agentic harness" in rendered.lower()

    def test_return_review_prompt_template_exists(self):
        reg = PromptRegistry(template_dir="templates/prompts")
        rendered = reg.render(
            "return_review.md.j2",
            task_return={"return_id": "RET-001", "result_summary": "done"},
            candidate_todos=[],
            artifacts=[],
        )
        assert "RET-001" in rendered

    def test_return_reviewer_renders_prompt_with_context(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)

        task_return = _make_task_return()
        candidate_todos = [{"todo_id": "TODO-001", "title": "Fix bug"}]
        artifacts = ["coverage.xml"]

        render_calls: list[str] = []
        original_render = registry.render

        def capture_render(name: str, **kwargs: object) -> str:
            render_calls.append(name)
            return original_render(name, **kwargs)

        with (
            patch.object(registry, "render", side_effect=capture_render),
            patch.object(reviewer, "_call_model", return_value=_make_decision()),
        ):
            reviewer.review_return(task_return, candidate_todos, artifacts)

        assert "return_review.md.j2" in render_calls

    def test_return_reviewer_calls_model_gateway(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)

        task_return = _make_task_return()
        expected_decision = _make_decision()

        with patch.object(reviewer, "_call_model", return_value=expected_decision) as mock_call:
            result = reviewer.review_return(task_return, [], [])

        mock_call.assert_called_once()
        assert result == expected_decision

    def test_return_reviewer_validates_task_decision_schema(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)

        valid_json = json.dumps({
            "return_id": "RET-001",
            "matched_todo_id": "TODO-001",
            "decision": "complete",
            "confidence": 0.9,
            "evidence_refs": ["cov.xml"],
        })

        decision = reviewer._parse_model_output(valid_json)

        assert decision is not None
        assert decision.decision == "complete"
        assert decision.confidence == 0.9

    def test_return_reviewer_handles_invalid_model_output(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)

        task_return = _make_task_return(return_id="RET-BAD")

        with patch.object(reviewer, "_call_model", return_value="not valid json"):
            decision = reviewer.review_return(task_return, [], [])

        assert decision.decision in ("ignore_duplicate", "failed")
        assert decision.return_id == "RET-BAD"


class TestDecisionApplier:
    @pytest.mark.asyncio
    async def test_decision_applier_complete_marks_todo_complete(self):
        decision = _make_decision(decision="complete", matched_todo_id="TODO-001")
        todo_repo = AsyncMock(spec=TodoRepository)
        mock_todo = MagicMock()
        mock_todo.todo_id = "TODO-001"
        mock_todo.status = "reviewing_return"
        mock_todo.version = 1
        todo_repo.get_by_id.return_value = mock_todo

        session = AsyncMock()

        await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_called_once_with(
            "TODO-001",
            pytest.approx("complete"),
            1,
        )

    @pytest.mark.asyncio
    async def test_decision_applier_needs_more_work_creates_child_todos(self):
        decision = _make_decision(
            decision="needs_more_work",
            matched_todo_id="TODO-001",
            child_todos=[
                {"title": "Fix failing tests", "description": "Tests in module X fail"},
                {"title": "Add coverage", "description": "Coverage below threshold"},
            ],
        )
        todo_repo = AsyncMock(spec=TodoRepository)
        mock_todo = MagicMock()
        mock_todo.todo_id = "TODO-001"
        mock_todo.status = "reviewing_return"
        mock_todo.version = 1
        todo_repo.get_by_id.return_value = mock_todo

        session = AsyncMock()

        await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_called_once_with(
            "TODO-001",
            "needs_more_work",
            1,
        )
        assert todo_repo.create.call_count == 2

    @pytest.mark.asyncio
    async def test_decision_applier_failed_marks_todo_failed(self):
        decision = _make_decision(decision="failed", matched_todo_id="TODO-001")
        todo_repo = AsyncMock(spec=TodoRepository)
        mock_todo = MagicMock()
        mock_todo.todo_id = "TODO-001"
        mock_todo.status = "reviewing_return"
        mock_todo.version = 1
        todo_repo.get_by_id.return_value = mock_todo

        session = AsyncMock()

        await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_called_once_with(
            "TODO-001",
            "failed",
            1,
        )

    @pytest.mark.asyncio
    async def test_decision_applier_blocks_complete_without_evidence(self):
        decision = _make_decision(
            decision="complete",
            matched_todo_id="TODO-001",
            evidence_refs=[],
        )
        todo_repo = AsyncMock(spec=TodoRepository)
        mock_todo = MagicMock()
        mock_todo.todo_id = "TODO-001"
        mock_todo.status = "reviewing_return"
        mock_todo.version = 1
        todo_repo.get_by_id.return_value = mock_todo

        session = AsyncMock()

        with pytest.raises(ValueError, match="evidence"):
            await apply_decision(decision, todo_repo, session)

    @pytest.mark.asyncio
    async def test_decision_applier_low_confidence_creates_validation_work(self):
        decision = _make_decision(
            decision="complete",
            matched_todo_id="TODO-001",
            confidence=0.3,
            evidence_refs=["cov.xml"],
            validation_requests=["validate_coverage"],
        )
        todo_repo = AsyncMock(spec=TodoRepository)
        mock_todo = MagicMock()
        mock_todo.todo_id = "TODO-001"
        mock_todo.status = "reviewing_return"
        mock_todo.version = 1
        todo_repo.get_by_id.return_value = mock_todo

        session = AsyncMock()

        await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_called()
        assert todo_repo.create.call_count >= 1
