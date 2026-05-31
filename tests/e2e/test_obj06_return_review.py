from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from agentic_harness.models.gateway import ModelGateway, ModelProfile
from agentic_harness.prompts.registry import PromptRegistry
from agentic_harness.review.reviewer import ReturnReviewer
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.todo import Todo, TodoStatus
from agentic_harness.secrets.env import EnvSecretsManager


class TestReturnReviewPipelineE2E:
    def test_prompt_templates_load_and_render(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        registry = PromptRegistry(template_dir=os.path.join(repo_root, "templates", "prompts"))
        rendered = registry.render(
            "return_review.md.j2",
            task_return={"return_id": "RET-1"},
            candidate_todos=[],
            artifacts=[],
        )
        assert "return" in rendered.lower()

        registry.register(
            "return_review_test",
            "Job {{ job_id }}, return_id={{ return_id }}",
        )
        rendered2 = registry.render(
            "return_review_test",
            job_id="JOB-E2E",
            return_id="RET-E2E",
        )
        assert "JOB-E2E" in rendered2
        assert "RET-E2E" in rendered2

    def test_reviewer_with_stub_model_produces_decision(self):
        profile = ModelProfile(
            model_profile_id="stub_reviewer",
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="stub",
            enabled=True,
        )
        secrets = EnvSecretsManager(overrides={"STUB_KEY": "test"})
        gateway = ModelGateway(profiles=[profile], secrets_manager=secrets)

        ReturnReviewer(gateway=gateway, prompt_registry=PromptRegistry())

        todo = Todo(title="Test task", description="E2E test", queue="core")

        decision = TaskDecision(
            return_id="RET-001",
            matched_todo_id=todo.todo_id,
            decision="complete",
            confidence=0.9,
            evidence_refs=["artifact://test.xml"],
        )
        assert decision.decision == "complete"
        assert decision.confidence > 0.8

    def test_decision_applier_complete_marks_todo(self):
        from unittest.mock import AsyncMock, MagicMock

        from agentic_harness.review.decision_applier import apply_decision

        todo = Todo(title="Test", description="E2E", queue="core")
        todo.status = TodoStatus.REVIEWING_RETURN

        mock_repo = MagicMock()
        mock_repo.get_by_id = AsyncMock(return_value=todo)
        mock_repo.transition = AsyncMock()
        mock_session = MagicMock()

        decision = TaskDecision(
            return_id="RET-001",
            matched_todo_id=todo.todo_id,
            decision="complete",
            confidence=0.95,
            evidence_refs=["test_result.xml"],
        )
        import asyncio
        asyncio.run(apply_decision(decision, mock_repo, mock_session))
        mock_repo.transition.assert_called_once()

    def test_decision_applier_needs_more_work_creates_children(self):
        from unittest.mock import AsyncMock, MagicMock

        from agentic_harness.review.decision_applier import apply_decision

        todo = Todo(title="Test", description="E2E", queue="core")
        todo.status = TodoStatus.REVIEWING_RETURN

        mock_repo = MagicMock()
        mock_repo.get_by_id = AsyncMock(return_value=todo)
        mock_repo.transition = AsyncMock()
        mock_repo.create = AsyncMock()
        mock_session = MagicMock()

        decision = TaskDecision(
            return_id="RET-001",
            matched_todo_id=todo.todo_id,
            decision="needs_more_work",
            confidence=0.7,
            child_todos=[
                {"title": "Fix test", "description": "Test fails on X"},
            ],
        )
        import asyncio
        asyncio.run(apply_decision(decision, mock_repo, mock_session))
        mock_repo.transition.assert_called_once()
        mock_repo.create.assert_called_once()

    def test_invalid_decision_schema_rejected(self):
        with pytest.raises(ValidationError):
            TaskDecision(
                return_id="RET-001",
                matched_todo_id="TODO-001",
                decision="invalid_decision_type",
                confidence=0.5,
            )
