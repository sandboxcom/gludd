"""Unit tests for conversation wiring into ReturnReviewer."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.models.gateway import ModelGateway
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.review.conversation import Conversation
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn


def _make_task_return(**overrides: object) -> TaskReturn:
    defaults = {
        "return_id": "RET-001", "todo_id": "TODO-001",
        "job_id": "JOB-001", "playbook": "test_playbook",
        "queue": "core", "result_summary": "All tests passed",
        "exit_code": 0, "artifacts": ["logs.txt"],
    }
    defaults.update(overrides)
    return TaskReturn(**defaults)


def _make_decision(**overrides: object) -> TaskDecision:
    defaults = {
        "return_id": "RET-001", "matched_todo_id": "TODO-001",
        "decision": "complete", "confidence": 0.95,
        "evidence_refs": ["coverage.xml"],
    }
    defaults.update(overrides)
    return TaskDecision(**defaults)


class TestConversationWiring:
    def test_reviewer_loads_existing_conversation(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("system", "Previous review context")
        conversations = {"TODO-001": conv}
        reviewer = ReturnReviewer(
            gateway=gateway, prompt_registry=registry, conversations=conversations,
        )
        result = reviewer.get_conversations()
        assert "TODO-001" in result
        assert result["TODO-001"].message_count() == 1

    def test_reviewer_adds_messages_to_conversation(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conversations = {"TODO-001": conv}
        reviewer = ReturnReviewer(
            gateway=gateway, prompt_registry=registry, conversations=conversations,
        )
        task_return = _make_task_return()
        decision = _make_decision()
        with patch.object(reviewer, "_call_model", return_value=(decision.model_dump_json(), None)):
            reviewer.review_return(task_return, [], [])
        assert conv.message_count() == 2

    def test_reviewer_includes_conversation_context_in_prompt(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        conv = Conversation(todo_id="TODO-001", return_id="RET-001")
        conv.add_message("assistant", "Previous review noted missing tests")
        conversations = {"TODO-001": conv}
        reviewer = ReturnReviewer(
            gateway=gateway, prompt_registry=registry, conversations=conversations,
        )
        task_return = _make_task_return()
        decision = _make_decision()
        with patch.object(reviewer, "_call_model", return_value=(decision.model_dump_json(), None)):
            reviewer.review_return(task_return, [], [])
        assert conv.message_count() == 2

    def test_new_conversation_created_when_none_exists(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(
            gateway=gateway, prompt_registry=registry,
        )
        task_return = _make_task_return()
        decision = _make_decision()
        with patch.object(reviewer, "_call_model", return_value=(decision.model_dump_json(), None)):
            reviewer.review_return(task_return, [], [])
        convs = reviewer.get_conversations()
        assert "TODO-001" in convs
        assert convs["TODO-001"].message_count() >= 1
