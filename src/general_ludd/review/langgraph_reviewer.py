"""LangGraph Reflexive Reviewer — self-reflective review loop.

Replaces the single-pass ReturnReviewer with a langgraph StateGraph that
iterates: draft → self-critique → evidence → revise → repeat until a
confidence threshold is met (or max_iterations exhausted).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)


# ── Structured output models ────────────────────────────────────────────


class ReviewWithReflection(BaseModel):
    """Model output from each review pass, including self-reflection."""

    decision: str = Field(
        description="Task decision: complete, needs_more_work, failed, blocked, manual_hold, ignore_duplicate"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this decision (0.0-1.0)"
    )
    audit_notes: list[str] = Field(
        default_factory=list, description="Evidence-backed observations"
    )
    evidence_refs: list[str] = Field(
        default_factory=list, description="References to supporting artifacts"
    )
    reflection: str = Field(
        default="",
        description="Self-critique: what is uncertain, what evidence is missing",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="Specific evidence items needed to raise confidence",
    )
    todo_updates: dict[str, Any] = Field(default_factory=dict)
    child_todos: list[dict[str, Any]] = Field(default_factory=list)
    validation_requests: list[str] = Field(default_factory=list)
    git_requests: list[str] = Field(default_factory=list)
    policy_flags: list[str] = Field(default_factory=list)


# ── Graph state ─────────────────────────────────────────────────────────


class ReviewerState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    task_return_id: str
    todo_id: str
    iteration: int
    max_iterations: int
    confidence_threshold: float
    final_decision: TaskDecision | None
    reflection_notes: list[str]
    missing_evidence: list[str]
    result_summary: str
    playbook: str
    exit_code: int
    candidate_todos: list[dict[str, Any]]
    artifacts: list[str]


# ── Core class ──────────────────────────────────────────────────────────


class LangGraphReflexiveReviewer:
    """Self-reflective reviewer using langgraph StateGraph.

    Iterative loop:
      1. draft_review — calls the model to produce a ReviewWithReflection.
      2. If confidence >= threshold → save final_decision, go to END.
      3. If confidence < threshold → evidence_gather → revise_review → repeat.
      4. After max_iterations → accept the last output regardless of confidence.

    Preserves the same ``review_return(task_return, ...) → TaskDecision``
    interface as ``ReturnReviewer`` so it can be dropped into the event loop
    without changing any callers.
    """

    def __init__(
        self,
        call_model: Any,
        *,
        max_iterations: int = 3,
        confidence_threshold: float = 0.8,
    ) -> None:
        self._call_model = call_model
        self._max_iterations = max_iterations
        self._confidence_threshold = confidence_threshold
        self._graph = self._build_graph()

    # ── Public interface (ReturnReviewer-compatible) ─────────────────

    def review_return(
        self,
        task_return: TaskReturn,
        candidate_todos: list[dict[str, Any]],
        artifacts: list[str],
    ) -> TaskDecision:
        initial_state: ReviewerState = {
            "messages": [],
            "task_return_id": task_return.return_id,
            "todo_id": task_return.todo_id or "",
            "iteration": 0,
            "max_iterations": self._max_iterations,
            "confidence_threshold": self._confidence_threshold,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": task_return.result_summary or "(no output)",
            "playbook": task_return.playbook or "unknown",
            "exit_code": task_return.exit_code or 0,
            "candidate_todos": candidate_todos,
            "artifacts": artifacts,
        }
        try:
            result = self._graph.invoke(initial_state)
            decision = result.get("final_decision")
            if isinstance(decision, TaskDecision):
                return decision
            return self._make_fallback_decision(task_return)
        except Exception as exc:
            logger.warning("LangGraphReflexiveReviewer graph failed: %s", exc)
            return TaskDecision(
                return_id=task_return.return_id,
                matched_todo_id=task_return.todo_id,
                decision="manual_hold",
                confidence=0.0,
                audit_notes=[f"Reflexive review graph error: {exc}"],
            )

    # ── Graph construction ───────────────────────────────────────────

    def _build_graph(self) -> Any:
        builder = StateGraph(ReviewerState)

        builder.add_node("draft_review", self._draft_review)
        builder.add_node("evidence_gather", self._evidence_gather)
        builder.add_node("revise_review", self._revise_review)

        builder.set_entry_point("draft_review")

        builder.add_conditional_edges(
            "draft_review",
            self._should_continue,
            {
                "evidence_gather": "evidence_gather",
                END: END,
            },
        )
        builder.add_edge("evidence_gather", "revise_review")
        builder.add_conditional_edges(
            "revise_review",
            self._should_continue,
            {
                "evidence_gather": "evidence_gather",
                END: END,
            },
        )

        return builder.compile()

    # ── Routing ───────────────────────────────────────────────────────

    def _should_continue(self, state: ReviewerState) -> Any:
        final = state.get("final_decision")
        if final is not None:
            return END
        iteration = state.get("iteration", 0)
        if iteration >= state.get("max_iterations", 3):
            return END
        return "evidence_gather"

    # ── Node: draft_review ────────────────────────────────────────────

    def _draft_review(self, state: ReviewerState) -> ReviewerState:
        iteration = state.get("iteration", 0) + 1
        state["iteration"] = iteration

        prompt = self._build_draft_prompt(state)
        raw = self._call_model(prompt)
        parsed = self._parse_reflection(raw, state)

        if parsed is not None:
            confidence = parsed.confidence
            state["reflection_notes"].append(parsed.reflection)
            state["missing_evidence"] = list(parsed.missing_evidence)

            if confidence >= state.get("confidence_threshold", 0.8) or iteration >= state.get(
                "max_iterations", 3
            ):
                state["final_decision"] = self._to_task_decision(parsed, state, iteration)
        else:
            state["reflection_notes"].append("Model output could not be parsed")
            if iteration >= state.get("max_iterations", 3):
                state["final_decision"] = self._make_fallback_decision_for_state(state)

        return state

    # ── Node: evidence_gather ─────────────────────────────────────────

    def _evidence_gather(self, state: ReviewerState) -> ReviewerState:
        missing = state.get("missing_evidence", [])
        artifacts = state.get("artifacts", [])
        notes = state.get("reflection_notes", [])

        if not missing:
            notes.append("evidence_gather: no specific evidence requested")
            state["reflection_notes"] = notes
            return state

        gathered: list[str] = []
        for item in missing:
            if any(item.lower() in a.lower() for a in artifacts):
                gathered.append(f"[found] {item} — matched in artifacts")
            else:
                gathered.append(f"[missing] {item} — not found in artifacts")

        notes.append("evidence_gather: " + "; ".join(gathered))
        state["reflection_notes"] = notes
        return state

    # ── Node: revise_review ───────────────────────────────────────────

    def _revise_review(self, state: ReviewerState) -> ReviewerState:
        iteration = state.get("iteration", 0) + 1
        state["iteration"] = iteration

        prompt = self._build_revise_prompt(state)
        raw = self._call_model(prompt)
        parsed = self._parse_reflection(raw, state)

        if parsed is not None:
            confidence = parsed.confidence
            state["reflection_notes"].append(f"revise(iter={iteration}): {parsed.reflection}")
            state["missing_evidence"] = list(parsed.missing_evidence)

            if confidence >= state.get("confidence_threshold", 0.8) or iteration >= state.get(
                "max_iterations", 3
            ):
                state["final_decision"] = self._to_task_decision(parsed, state, iteration)
        else:
            state["reflection_notes"].append(f"revise(iter={iteration}): parse failed")
            if iteration >= state.get("max_iterations", 3):
                state["final_decision"] = self._make_fallback_decision_for_state(state)

        return state

    # ── Prompt builders ───────────────────────────────────────────────

    def _build_draft_prompt(self, state: ReviewerState) -> str:
        summary = state.get("result_summary", "")
        playbook = state.get("playbook", "")
        exit_code = state.get("exit_code", 0)
        return (
            "You are a task-return reviewer performing an INITIAL DRAFT review.\n\n"
            f"Task return ID: {state.get('task_return_id', '?')}\n"
            f"Todo ID: {state.get('todo_id', '?')}\n"
            f"Playbook: {playbook}\n"
            f"Exit code: {exit_code}\n"
            f"Result summary: {summary[:3000]}\n\n"
            "Produce a structured review in JSON format with these fields:\n"
            '  - decision: one of complete, needs_more_work, failed, blocked, manual_hold, ignore_duplicate\n'
            '  - confidence: float 0.0-1.0 reflecting how certain you are of the decision\n'
            '  - audit_notes: list of specific, evidence-backed observations\n'
            '  - evidence_refs: list of supporting references\n'
            '  - reflection: honest self-critique — what are you uncertain about? what gaps exist?\n'
            '  - missing_evidence: list of evidence items that would increase your confidence\n\n'
            "Be honest. If confidence is low, explain why in reflection and list what evidence is missing.\n\n"
            "Return ONLY valid JSON, no markdown fences, no trailing prose."
        )

    def _build_revise_prompt(self, state: ReviewerState) -> str:
        prior_reflections = state.get("reflection_notes", [])
        missing = state.get("missing_evidence", [])
        artifacts = state.get("artifacts", [])
        summary = state.get("result_summary", "")
        playbook = state.get("playbook", "")
        exit_code = state.get("exit_code", 0)

        prior_text = "\n".join(f"  - {r}" for r in prior_reflections[-5:])
        missing_text = "\n".join(f"  - {m}" for m in missing)
        artifacts_text = "\n".join(f"  - {a}" for a in artifacts[:20])

        return (
            "You are a task-return reviewer performing a REVISED review based on new evidence.\n\n"
            f"Task return ID: {state.get('task_return_id', '?')}\n"
            f"Todo ID: {state.get('todo_id', '?')}\n"
            f"Playbook: {playbook}\n"
            f"Exit code: {exit_code}\n"
            f"Result summary: {summary[:3000]}\n\n"
            "Prior reflections:\n"
            f"{prior_text}\n\n"
            "Missing evidence items requested:\n"
            f"{missing_text}\n\n"
            "Available artifacts:\n"
            f"{artifacts_text}\n\n"
            "Incorporate the evidence above, revise your confidence, and update "
            "reflection + missing_evidence accordingly.\n\n"
            "Return ONLY valid JSON with: decision, confidence, audit_notes, "
            "evidence_refs, reflection, missing_evidence.\n"
            "Return ONLY valid JSON, no markdown fences, no trailing prose."
        )

    # ── Parsing ────────────────────────────────────────────────────────

    def _parse_reflection(
        self, raw: str | None, state: ReviewerState
    ) -> ReviewWithReflection | None:
        if raw is None:
            return None
        try:
            cleaned = self._extract_json(raw)
            data = json.loads(cleaned)
            return ReviewWithReflection(**data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.debug("Failed to parse ReviewWithReflection: %s", exc)
            return None

    @staticmethod
    def _extract_json(text: str) -> str:
        import re

        fence_match = re.search(r"```(?:json)?\s*(\{.*?)\s*```", text, re.DOTALL)
        candidate = fence_match.group(1).strip() if fence_match else text
        start = candidate.find("{")
        if start == -1:
            return candidate
        try:
            _obj, end = json.JSONDecoder().raw_decode(candidate, start)
            return candidate[start:end]
        except json.JSONDecodeError:
            return candidate

    # ── Result construction ───────────────────────────────────────────

    def _to_task_decision(
        self,
        review: ReviewWithReflection,
        state: ReviewerState,
        iteration: int,
    ) -> TaskDecision:
        notes = list(review.audit_notes)
        notes.append(
            f"[reflexive-review] iter={iteration} confidence={review.confidence:.2f}"
        )
        if review.reflection:
            notes.append(f"[reflection] {review.reflection[:200]}")
        return TaskDecision(
            return_id=state.get("task_return_id", ""),
            matched_todo_id=state.get("todo_id"),
            decision=review.decision,
            confidence=review.confidence,
            evidence_refs=list(review.evidence_refs),
            audit_notes=notes,
            todo_updates=dict(review.todo_updates or {}),
            child_todos=list(review.child_todos or []),
            validation_requests=list(review.validation_requests or []),
            git_requests=list(review.git_requests or []),
            policy_flags=list(review.policy_flags or []),
        )

    def _make_fallback_decision_for_state(self, state: ReviewerState) -> TaskDecision:
        return TaskDecision(
            return_id=state.get("task_return_id", ""),
            matched_todo_id=state.get("todo_id"),
            decision="manual_hold",
            confidence=0.0,
            audit_notes=[
                *list(state.get("reflection_notes", [])),
                f"Fallback after {state.get('iteration', 0)} iterations",
            ],
        )

    def _make_fallback_decision(self, task_return: TaskReturn) -> TaskDecision:
        return TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision="manual_hold",
            confidence=0.0,
            audit_notes=["Graph invoke returned no final_decision"],
        )
