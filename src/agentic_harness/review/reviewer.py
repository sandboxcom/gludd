"""Return reviewer that uses model gateway to evaluate task returns."""

from __future__ import annotations

import json
import logging
from typing import Any

from agentic_harness.models.gateway import ModelGateway
from agentic_harness.models.router import ModelRouter
from agentic_harness.prompts.registry import PromptRegistry
from agentic_harness.review.conversation import Conversation
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)


class ReturnReviewer:
    def __init__(
        self,
        gateway: ModelGateway,
        prompt_registry: PromptRegistry,
        model_profile_id: str = "default",
        router: ModelRouter | None = None,
        conversations: dict[str, Conversation] | None = None,
    ) -> None:
        self._gateway = gateway
        self._registry = prompt_registry
        self._model_profile_id = model_profile_id
        self._router = router
        self._conversations: dict[str, Conversation] = conversations if conversations is not None else {}

    def get_conversations(self) -> dict[str, Conversation]:
        return dict(self._conversations)

    def review_return(
        self,
        task_return: TaskReturn,
        candidate_todos: list[dict[str, Any]],
        artifacts: list[str],
    ) -> TaskDecision:
        todo_id = task_return.todo_id or ""
        conv = self._conversations.get(todo_id)
        if conv is None:
            conv = Conversation(todo_id=todo_id, return_id=task_return.return_id)
            self._conversations[todo_id] = conv
        prior_context = ""
        if conv.messages:
            prior_context = "\n\n".join(
                f"[{m.role}]: {m.content}" for m in conv.get_context()
            )
        review_prompt_text = f"Review return {task_return.return_id} for todo {todo_id}"
        conv.add_message("user", review_prompt_text)
        prompt = self._registry.render(
            "return_review.md.j2",
            task_return=task_return.model_dump(mode="json"),
            candidate_todos=candidate_todos,
            artifacts=artifacts,
            conversation_context=prior_context,
        )
        raw_output = self._call_model(prompt)
        decision = self._parse_model_output(raw_output)
        if decision is not None:
            conv.add_message("assistant", json.dumps(decision.model_dump(mode="json")))
            return decision
        logger.warning(
            "Invalid model output for return %s, falling back", task_return.return_id
        )
        fallback = TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision="ignore_duplicate",
            confidence=0.0,
            audit_notes=["Model output was not valid JSON or did not match TaskDecision schema"],
        )
        conv.add_message("assistant", json.dumps(fallback.model_dump(mode="json")))
        return fallback

    def _call_model(self, prompt: str) -> str:
        if self._router is not None:
            profile_id = self._router.resolve_role("return_review")
            if profile_id is not None:
                self._model_profile_id = profile_id
        return str(prompt)

    def _parse_model_output(self, raw: Any) -> TaskDecision | None:
        if isinstance(raw, TaskDecision):
            return raw
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        try:
            return TaskDecision(**data)
        except (ValueError, TypeError):
            return None
