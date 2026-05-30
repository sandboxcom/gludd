"""Return reviewer that uses model gateway to evaluate task returns."""

from __future__ import annotations

import json
import logging
from typing import Any

from agentic_harness.models.gateway import ModelGateway
from agentic_harness.prompts.registry import PromptRegistry
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)


class ReturnReviewer:
    def __init__(
        self,
        gateway: ModelGateway,
        prompt_registry: PromptRegistry,
        model_profile_id: str = "default",
    ) -> None:
        self._gateway = gateway
        self._registry = prompt_registry
        self._model_profile_id = model_profile_id

    def review_return(
        self,
        task_return: TaskReturn,
        candidate_todos: list[dict[str, Any]],
        artifacts: list[str],
    ) -> TaskDecision:
        prompt = self._registry.render(
            "return_review.md.j2",
            task_return=task_return.model_dump(mode="json"),
            candidate_todos=candidate_todos,
            artifacts=artifacts,
        )
        raw_output = self._call_model(prompt)
        decision = self._parse_model_output(raw_output)
        if decision is not None:
            return decision
        logger.warning(
            "Invalid model output for return %s, falling back", task_return.return_id
        )
        return TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision="ignore_duplicate",
            confidence=0.0,
            audit_notes=["Model output was not valid JSON or did not match TaskDecision schema"],
        )

    def _call_model(self, prompt: str) -> str:
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
