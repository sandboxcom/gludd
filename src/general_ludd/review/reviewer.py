"""Return reviewer that uses model gateway to evaluate task returns."""

from __future__ import annotations

import json
import logging
from typing import Any

from general_ludd.models.gateway import ModelGateway
from general_ludd.models.router import ModelRouter
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.review.conversation import Conversation
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn

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
        self._conversations: dict[str, Conversation] = (
            conversations if conversations is not None else {}
        )

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
        review_prompt_text = (
            f"Review return {task_return.return_id} for todo {todo_id}"
        )
        conv.add_message("user", review_prompt_text)
        prompt = self._registry.render(
            "return_review.md.j2",
            task_return=task_return.model_dump(mode="json"),
            candidate_todos=candidate_todos,
            artifacts=artifacts,
            conversation_context=prior_context,
        )
        raw_output, error_msg = self._call_model(prompt)
        if raw_output is None:
            audit = ["Model call failed"]
            if error_msg:
                audit.append(f"Error: {error_msg}")
            decision = TaskDecision(
                return_id=task_return.return_id,
                matched_todo_id=task_return.todo_id,
                decision="failed",
                confidence=0.0,
                audit_notes=audit,
            )
            conv.add_message(
                "assistant", json.dumps(decision.model_dump(mode="json"))
            )
            return decision
        parsed = self._parse_model_output(raw_output, task_return)
        if parsed is not None:
            conv.add_message(
                "assistant", json.dumps(parsed.model_dump(mode="json"))
            )
            return parsed
        logger.warning(
            "Invalid model output for return %s, falling back to failed",
            task_return.return_id,
        )
        fallback = TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision="failed",
            confidence=0.0,
            audit_notes=[
                "Model output was not valid JSON or did not match TaskDecision schema"
            ],
        )
        conv.add_message(
            "assistant", json.dumps(fallback.model_dump(mode="json"))
        )
        return fallback

    def _call_model(self, prompt: str) -> tuple[str | None, str | None]:
        if self._router is not None:
            profile_id = self._router.resolve_role("return_review")
            if profile_id is not None:
                self._model_profile_id = profile_id
        try:
            response = self._gateway.call_model(
                self._model_profile_id,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content, None
        except Exception as exc:
            logger.warning(
                "Model call failed for profile %s: %s",
                self._model_profile_id,
                exc,
            )
            return None, str(exc)

    def _parse_model_output(
        self, raw: Any, task_return: TaskReturn
    ) -> TaskDecision | None:
        if isinstance(raw, TaskDecision):
            return raw
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("return_id", task_return.return_id)
        data.setdefault("matched_todo_id", task_return.todo_id)
        try:
            return TaskDecision(**data)
        except (ValueError, TypeError):
            return None
