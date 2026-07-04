"""Consensus-based reviewer wrapping ConsensusEngine as a ReturnReviewer-compatible adapter.

G11: Multi-agent debate consensus wired as an alternative review path.
Implements the same review_return() interface as ReturnReviewer so it
can be dropped into the event loop review phase without changing callers.
"""

from __future__ import annotations

import logging
from typing import Any

from general_ludd.models.gateway import ModelGateway
from general_ludd.review.consensus import ConsensusEngine
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)


class ConsensusReviewer:
    """Adapter that uses ConsensusEngine to perform a multi-agent review.

    Instead of a single model call (ReturnReviewer), this runs a
    mini-debate among *num_agents* simulated reviewers, converging
    on a consensus verdict.  Falls back to conventional review when
    consensus cannot be reached (tie / no reviewer configured).
    """

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        num_agents: int = 3,
        max_rounds: int = 3,
        model_profile_id: str = "default",
    ) -> None:
        self._gateway = gateway
        self._num_agents = num_agents
        self._max_rounds = max_rounds
        self._model_profile_id = model_profile_id

        def _gateway_reviewer(prompt: str) -> str:
            try:
                messages: list[dict[str, str]] = [
                    {"role": "user", "content": prompt},
                ]
                result = self._gateway.call_model(
                    self._model_profile_id,
                    messages,
                )
                if isinstance(result, tuple):
                    return str(result[0])
                text = getattr(result, "text", "") or getattr(result, "content", "") or str(result)
                return text
            except Exception as exc:
                logger.warning("Consensus agent call failed: %s", exc)
                return "needs_changes\nGateway call failed"

        self._engine = ConsensusEngine(
            reviewer=_gateway_reviewer,
            judge=_gateway_reviewer,
        )

    def review_return(
        self,
        task_return: TaskReturn,
        candidate_todos: list[dict[str, Any]],
        artifacts: list[str],
    ) -> TaskDecision:
        result_summary = task_return.result_summary or "(no output)"
        playbook = task_return.playbook or "unknown"
        exit_code = task_return.exit_code or 0

        question = (
            f"Review this task return and decide: approve, reject, or needs_changes.\n\n"
            f"Playbook: {playbook}\n"
            f"Exit code: {exit_code}\n"
            f"Result summary: {result_summary[:2000]}"
        )
        context = (
            f"Candidate todos: {len(candidate_todos)} pending. "
            f"Artifacts: {len(artifacts)} available."
        )

        try:
            result = self._engine.run_debate(
                question=question,
                context=context,
                num_agents=self._num_agents,
                max_rounds=self._max_rounds,
            )
        except Exception as exc:
            logger.error("Consensus debate failed: %s", exc)
            return TaskDecision(
                return_id=task_return.return_id,
                matched_todo_id=task_return.todo_id,
                decision="manual_hold",
                confidence=0.0,
                audit_notes=[f"Consensus engine error: {exc}"],
            )

        verdict = result.get("verdict", "needs_changes")
        confidence = float(result.get("confidence", 0.0))
        consensus_reached = bool(result.get("consensus", False))
        rounds = int(result.get("rounds", 0))

        decision_map = {
            "approve": "complete",
            "reject": "needs_more_work",
            "needs_changes": "manual_hold",
            "tie": "manual_hold",
            "error": "failed",
        }

        return TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision=decision_map.get(verdict, "manual_hold"),
            confidence=confidence,
            audit_notes=[
                f"Consensus review: {'reached' if consensus_reached else 'deadlocked'} "
                f"in {rounds} rounds with {self._num_agents} agents, "
                f"verdict={verdict}, confidence={confidence:.2f}",
            ],
        )
