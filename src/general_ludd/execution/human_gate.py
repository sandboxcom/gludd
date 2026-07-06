"""Human-in-the-loop gate using LangGraph interrupt() + Command.

Provides synchronous pause/resume at the review decision boundary:
when ``config.review.human_in_the_loop`` is enabled, COMPLETE decisions
with confidence below a configurable threshold are paused via
``langgraph.types.interrupt()`` and await human approval through
``Command(resume=...)``.

The existing HumanTodo polling flow remains as fallback when LangGraph
is not available or the config is disabled.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict


class _GateState(TypedDict, total=False):
    message: str
    decision_id: str
    todo_id: str
    decision: str

logger = logging.getLogger(__name__)

_LANGGRAPH_AVAILABLE = False
try:
    from langgraph.graph import StateGraph
    from langgraph.types import Command, interrupt

    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False


_DEFAULT_CONFIDENCE_THRESHOLD = 0.7
_DEFAULT_CONFIG_KEY = "review.human_in_the_loop"


def _is_human_gate_enabled(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    review_cfg = config.get("review", {})
    if not isinstance(review_cfg, dict):
        return False
    return bool(review_cfg.get("human_in_the_loop", False))


def _confidence_threshold(config: dict[str, Any] | None) -> float:
    if not config or not isinstance(config, dict):
        return _DEFAULT_CONFIDENCE_THRESHOLD
    review_cfg = config.get("review", {})
    if not isinstance(review_cfg, dict):
        return _DEFAULT_CONFIDENCE_THRESHOLD
    try:
        return float(review_cfg.get("confidence_threshold", _DEFAULT_CONFIDENCE_THRESHOLD))
    except (ValueError, TypeError):
        return _DEFAULT_CONFIDENCE_THRESHOLD


def _build_gate_graph() -> Any:
    builder = StateGraph(_GateState)

    def gate_node(state: _GateState) -> dict[str, str]:
        message = state.get("message", "Human approval required")
        decision = interrupt(message)
        return {"decision": str(decision) if decision else "denied"}

    builder.add_node("gate", gate_node)
    builder.set_entry_point("gate")
    builder.set_finish_point("gate")
    return builder.compile()


class HumanGate:
    """LangGraph-backed human-in-the-loop gate for review decisions.

    When ``config.review.human_in_the_loop`` is enabled and a decision's
    confidence is below ``config.review.confidence_threshold``, the gate
    pauses execution via ``interrupt()`` and waits for a human to approve
    through ``Command(resume=...)``.

    Fallback: when LangGraph is not installed, ``await_approval`` logs a
    warning and returns ``None`` (no-op), so the existing HumanTodo polling
    path handles the review gating.

    Thread tracking: ``_pending`` maps ``thread_id`` → config dict so
    ``resume()`` can find and continue the paused graph run.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._pending: dict[str, dict[str, Any]] = {}  # thread_id → langgraph config
        self._graph: Any = None
        if _LANGGRAPH_AVAILABLE:
            try:
                self._graph = _build_gate_graph()
            except Exception as exc:
                logger.warning("HumanGate: failed to build gate graph: %s", exc)
                self._graph = None

    @property
    def available(self) -> bool:
        return self._graph is not None

    @property
    def enabled(self) -> bool:
        return _is_human_gate_enabled(self._config)

    @property
    def confidence_threshold(self) -> float:
        return _confidence_threshold(self._config)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def pending_thread_ids(self) -> list[str]:
        return list(self._pending.keys())

    def should_interrupt(self, confidence: float) -> bool:
        if not self.enabled:
            return False
        return confidence < self.confidence_threshold

    async def await_approval(
        self,
        thread_id: str,
        message: str,
        *,
        decision_id: str = "",
        todo_id: str = "",
        confidence: float = 0.0,
    ) -> str | None:
        """Pause the graph at the human review gate and wait for approval.

        When the condition is met (config enabled + confidence < threshold),
        invokes the gate graph which calls ``interrupt()`` to pause. The
        graph's ``ainvoke`` returns the interrupt data immediately (it does
        NOT block). The paused graph is stored keyed by ``thread_id`` for
        later ``resume()``.

        Returns the human's decision string when the graph is resumed via
        ``resume()``. Returns ``None`` when the gate is not enabled or not
        available — in that case the existing HumanTodo polling path handles
        the review gating.

        Args:
            thread_id: Unique identifier for this graph run (used to resume).
            message: Human-readable description of what needs approval.
            decision_id: The decision being reviewed (for audit).
            todo_id: The todo being decided on (for audit).
            confidence: The model's confidence in this decision.
        """
        if not self.should_interrupt(confidence):
            return None
        if not self.available:
            logger.warning(
                "HumanGate: human_in_the_loop enabled but langgraph not available; "
                "falling back to HumanTodo polling for decision_id=%s todo_id=%s",
                decision_id, todo_id,
            )
            return None

        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        self._pending[thread_id] = config

        full_message = (
            f"Review Approval Required\n\n"
            f"Decision: {decision_id}\n"
            f"Todo: {todo_id}\n"
            f"Confidence: {confidence:.2f}\n\n"
            f"{message}\n\n"
            f"Respond with: approved, denied, or needs_more_work"
        )

        try:
            result = await self._graph.ainvoke(
                {"message": full_message, "decision_id": decision_id, "todo_id": todo_id},
                config,
            )
            decision = result.get("decision", "denied") if isinstance(result, dict) else "denied"
            return str(decision) if decision is not None else "denied"
        except Exception as exc:
            logger.error(
                "HumanGate: graph invoke failed for thread %s: %s", thread_id, exc
            )
            self._pending.pop(thread_id, None)
            return None

    async def resume(self, thread_id: str, decision: str) -> bool:
        """Resume a paused gate graph with a human decision.

        Sends ``Command(resume=decision)`` to the graph identified by
        ``thread_id``. The ``interrupt()`` call inside the graph node
        returns this value as its result.

        Returns ``True`` if the resume succeeded, ``False`` if no pending
        gate was found for the given thread_id.

        Args:
            thread_id: The thread identifier from the original ``await_approval`` call.
            decision: The human's decision: ``approved``, ``denied``, or ``needs_more_work``.
        """
        if thread_id not in self._pending:
            logger.warning("HumanGate: no pending gate for thread %s", thread_id)
            return False
        if not self.available:
            logger.warning("HumanGate: resume called but langgraph not available")
            return False

        config = self._pending.pop(thread_id)
        try:
            await self._graph.ainvoke(Command(resume=decision), config)
            logger.info(
                "HumanGate: resumed thread %s with decision=%s", thread_id, decision
            )
            return True
        except Exception as exc:
            logger.error("HumanGate: resume failed for thread %s: %s", thread_id, exc)
            return False

    def cancel(self, thread_id: str) -> bool:
        """Remove a pending gate without resuming (human dismissed)."""
        if thread_id in self._pending:
            del self._pending[thread_id]
            logger.info("HumanGate: cancelled pending gate for thread %s", thread_id)
            return True
        return False
