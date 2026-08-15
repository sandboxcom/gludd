"""Tests for LangGraphReflexiveReviewer — self-reflective review loop.

Covers: graph construction, routing, draft/revise nodes, evidence gathering,
JSON parsing, fallback behavior, and model validation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from general_ludd.review.langgraph_reviewer import (
    LangGraphReflexiveReviewer,
    ReviewerState,
    ReviewWithReflection,
)
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn


def _make_task_return(**overrides: object) -> TaskReturn:
    defaults: dict[str, object] = {
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
    return TaskReturn(**defaults)  # type: ignore[arg-type]


def _make_review_json(
    decision: str = "complete",
    confidence: float = 0.95,
    reflection: str = "Looks good",
    missing_evidence: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "confidence": confidence,
            "audit_notes": ["Test output verified"],
            "evidence_refs": ["test:unit"],
            "reflection": reflection,
            "missing_evidence": missing_evidence or [],
            "todo_updates": {},
            "child_todos": [],
            "validation_requests": [],
            "git_requests": [],
            "policy_flags": [],
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestReviewWithReflectionModel:
    def test_construct_with_minimal_fields(self):
        r = ReviewWithReflection(decision="complete", confidence=0.9)
        assert r.decision == "complete"
        assert r.confidence == 0.9
        assert r.reflection == ""
        assert r.missing_evidence == []

    def test_construct_with_all_fields(self):
        r = ReviewWithReflection(
            decision="needs_more_work",
            confidence=0.5,
            audit_notes=["Missing tests"],
            evidence_refs=["file:tests.py"],
            reflection="Uncertain about coverage",
            missing_evidence=["test:missing"],
            todo_updates={"status": "in_progress"},
            child_todos=[{"id": "C1"}],
            validation_requests=["check lint"],
            git_requests=["commit"],
            policy_flags=["review"],
        )
        assert r.decision == "needs_more_work"
        assert r.audit_notes == ["Missing tests"]
        assert r.todo_updates == {"status": "in_progress"}
        assert len(r.child_todos) == 1

    def test_confidence_clamped_to_range(self):
        with pytest.raises(ValueError):
            ReviewWithReflection(decision="complete", confidence=1.5)
        with pytest.raises(ValueError):
            ReviewWithReflection(decision="complete", confidence=-0.1)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_default_values(self):
        call_model = MagicMock()
        reviewer = LangGraphReflexiveReviewer(call_model)
        assert reviewer._max_iterations == 3
        assert reviewer._confidence_threshold == 0.8
        assert reviewer._graph is not None

    def test_custom_thresholds(self):
        call_model = MagicMock()
        reviewer = LangGraphReflexiveReviewer(
            call_model,
            max_iterations=5,
            confidence_threshold=0.9,
        )
        assert reviewer._max_iterations == 5
        assert reviewer._confidence_threshold == 0.9


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------


class TestGraphConstruction:
    def test_graph_has_required_nodes(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        graph = reviewer._build_graph()
        nodes = graph.get_graph().nodes
        assert "draft_review" in nodes
        assert "evidence_gather" in nodes
        assert "revise_review" in nodes

    def test_graph_compiles_without_error(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        graph = reviewer._graph
        assert graph is not None


# ---------------------------------------------------------------------------
# Routing: _should_continue
# ---------------------------------------------------------------------------


class TestShouldContinue:
    def test_returns_end_when_final_decision_set(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "final_decision": TaskDecision(
                return_id="R1",
                matched_todo_id="T1",
                decision="complete",
                confidence=0.9,
            ),
        }
        result = reviewer._should_continue(state)
        assert result == "END" or result == "__end__"

    def test_returns_end_when_max_iterations_reached(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock(), max_iterations=3)
        state: ReviewerState = {"iteration": 3, "max_iterations": 3}
        result = reviewer._should_continue(state)
        assert result == "END" or result == "__end__"

    def test_returns_evidence_gather_when_not_done(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock(), max_iterations=3)
        state: ReviewerState = {"iteration": 1, "max_iterations": 3}
        result = reviewer._should_continue(state)
        assert result == "evidence_gather"


# ---------------------------------------------------------------------------
# _draft_review node
# ---------------------------------------------------------------------------


class TestDraftReview:
    def test_high_confidence_sets_final_decision(self):
        call_model = MagicMock(return_value=_make_review_json(confidence=0.95))
        reviewer = LangGraphReflexiveReviewer(call_model)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 0,
            "max_iterations": 3,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._draft_review(state)
        assert result.get("final_decision") is not None
        assert result.get("iteration") == 1
        assert len(result.get("reflection_notes") or []) >= 1

    def test_low_confidence_does_not_set_final_decision(self):
        call_model = MagicMock(return_value=_make_review_json(confidence=0.5))
        reviewer = LangGraphReflexiveReviewer(call_model)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 0,
            "max_iterations": 3,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._draft_review(state)
        assert result.get("final_decision") is None
        assert result.get("iteration") == 1

    def test_max_iterations_reached_sets_fallback(self):
        call_model = MagicMock(return_value=_make_review_json(confidence=0.3))
        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=1)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 0,
            "max_iterations": 1,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._draft_review(state)
        assert result.get("final_decision") is not None

    def test_parse_failure_at_max_iterations_sets_fallback(self):
        call_model = MagicMock(return_value="not valid json at all {{{")
        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=1)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 0,
            "max_iterations": 1,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._draft_review(state)
        assert result.get("final_decision") is not None
        decision = result.get("final_decision")
        assert decision is not None and decision.decision == "manual_hold"

    def test_parse_failure_below_max_does_not_set_decision(self):
        call_model = MagicMock(return_value="garbage {{{")
        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=3)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 0,
            "max_iterations": 3,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._draft_review(state)
        assert result.get("final_decision") is None
        assert "Model output could not be parsed" in (result.get("reflection_notes") or [])

    def test_null_model_output(self):
        call_model = MagicMock(return_value=None)
        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=3)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 0,
            "max_iterations": 3,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._draft_review(state)
        assert result.get("final_decision") is None


# ---------------------------------------------------------------------------
# _evidence_gather node
# ---------------------------------------------------------------------------


class TestEvidenceGather:
    def test_no_missing_evidence_adds_note(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "messages": [],
            "reflection_notes": [],
            "missing_evidence": [],
            "artifacts": ["coverage.xml"],
        }
        result = reviewer._evidence_gather(state)
        assert any("no specific evidence" in n for n in (result.get("reflection_notes") or []))

    def test_found_evidence(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "messages": [],
            "reflection_notes": [],
            "missing_evidence": ["coverage.xml", "lint.log"],
            "artifacts": ["coverage.xml", "logs.txt"],
        }
        result = reviewer._evidence_gather(state)
        notes = result.get("reflection_notes") or []
        assert any("[found]" in n for n in notes)
        assert any("[missing]" in n for n in notes)
        assert any("coverage.xml" in n for n in notes)
        assert any("lint.log" in n for n in notes)

    def test_all_evidence_missing(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "messages": [],
            "reflection_notes": [],
            "missing_evidence": ["nonexistent.log"],
            "artifacts": ["other.txt"],
        }
        result = reviewer._evidence_gather(state)
        notes = result.get("reflection_notes") or []
        assert any("[missing]" in n for n in notes)

    def test_all_evidence_found(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "messages": [],
            "reflection_notes": [],
            "missing_evidence": ["coverage.xml"],
            "artifacts": ["coverage.xml"],
        }
        result = reviewer._evidence_gather(state)
        notes = result.get("reflection_notes") or []
        assert any("[found]" in n for n in notes)

    def test_case_insensitive_matching(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "messages": [],
            "reflection_notes": [],
            "missing_evidence": ["Coverage.XML"],
            "artifacts": ["coverage.xml"],
        }
        result = reviewer._evidence_gather(state)
        notes = result.get("reflection_notes") or []
        assert any("[found]" in n for n in notes)


# ---------------------------------------------------------------------------
# _revise_review node
# ---------------------------------------------------------------------------


class TestReviseReview:
    def test_high_confidence_sets_final_decision(self):
        call_model = MagicMock(return_value=_make_review_json(confidence=0.85))
        reviewer = LangGraphReflexiveReviewer(call_model)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 1,
            "max_iterations": 3,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._revise_review(state)
        assert result.get("final_decision") is not None
        assert result.get("iteration") == 2

    def test_parse_failure_at_max_iterations_sets_fallback(self):
        call_model = MagicMock(return_value="garbage")
        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2)
        state: ReviewerState = {
            "messages": [],
            "task_return_id": "R1",
            "todo_id": "T1",
            "iteration": 1,
            "max_iterations": 2,
            "confidence_threshold": 0.8,
            "final_decision": None,
            "reflection_notes": [],
            "missing_evidence": [],
            "result_summary": "tests passed",
            "playbook": "test",
            "exit_code": 0,
            "candidate_todos": [],
            "artifacts": [],
        }
        result = reviewer._revise_review(state)
        assert result.get("final_decision") is not None
        decision = result.get("final_decision")
        assert decision is not None and decision.decision == "manual_hold"


# ---------------------------------------------------------------------------
# _parse_reflection
# ---------------------------------------------------------------------------


class TestParseReflection:
    def test_parses_valid_json(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        json_str = _make_review_json(decision="complete", confidence=0.9)
        state: ReviewerState = {}
        result = reviewer._parse_reflection(json_str, state)
        assert result is not None
        assert result.decision == "complete"
        assert result.confidence == 0.9

    def test_parses_json_with_markdown_fence(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        raw = "```json\n" + _make_review_json(decision="failed", confidence=0.7) + "\n```"
        state: ReviewerState = {}
        result = reviewer._parse_reflection(raw, state)
        assert result is not None
        assert result.decision == "failed"

    def test_returns_none_for_invalid_json(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {}
        result = reviewer._parse_reflection("not json at all", state)
        assert result is None

    def test_returns_none_for_null_input(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {}
        result = reviewer._parse_reflection(None, state)
        assert result is None

    def test_returns_none_for_empty_string(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {}
        result = reviewer._parse_reflection("", state)
        assert result is None

    def test_missing_required_fields_returns_none(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {}
        result = reviewer._parse_reflection('{"extra": "field"}', state)
        assert result is None


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_extracts_plain_json(self):
        raw = '{"key": "value"}'
        result = LangGraphReflexiveReviewer._extract_json(raw)
        assert "key" in result

    def test_extracts_json_from_markdown_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = LangGraphReflexiveReviewer._extract_json(raw)
        assert "key" in result

    def test_extracts_json_with_leading_text(self):
        raw = 'Here is the output: {"key": "value"} thanks'
        result = LangGraphReflexiveReviewer._extract_json(raw)
        data = json.loads(result)
        assert data["key"] == "value"

    def test_handles_no_braces_text(self):
        raw = "plain text no json"
        result = LangGraphReflexiveReviewer._extract_json(raw)
        assert result == "plain text no json"


# ---------------------------------------------------------------------------
# _to_task_decision
# ---------------------------------------------------------------------------


class TestToTaskDecision:
    def test_constructs_task_decision_with_all_fields(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        review = ReviewWithReflection(
            decision="complete",
            confidence=0.95,
            audit_notes=["Test passed"],
            evidence_refs=["commit:abc123"],
            reflection="Looks correct",
            missing_evidence=[],
            todo_updates={"foo": "bar"},
            child_todos=[{"id": "C1"}],
            validation_requests=["lint"],
            git_requests=["push"],
            policy_flags=["review"],
        )
        state: ReviewerState = {
            "task_return_id": "RET-001",
            "todo_id": "TODO-001",
        }
        decision = reviewer._to_task_decision(review, state, 2)
        assert decision.return_id == "RET-001"
        assert decision.decision == "complete"
        assert decision.confidence == 0.95
        assert "commit:abc123" in decision.evidence_refs
        assert any("iter=2" in n for n in decision.audit_notes)
        assert any("reflection" in n.lower() for n in decision.audit_notes)
        assert decision.todo_updates == {"foo": "bar"}
        assert decision.child_todos == [{"id": "C1"}]
        assert decision.validation_requests == ["lint"]
        assert decision.git_requests == ["push"]
        assert decision.policy_flags == ["review"]


# ---------------------------------------------------------------------------
# Fallback Decisions
# ---------------------------------------------------------------------------


class TestFallbackDecisions:
    def test_make_fallback_decision_for_state(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "task_return_id": "RET-FB",
            "todo_id": "TODO-FB",
            "iteration": 3,
            "reflection_notes": ["Parse failed"],
        }
        decision = reviewer._make_fallback_decision_for_state(state)
        assert decision.decision == "manual_hold"
        assert decision.confidence == 0.0
        assert decision.return_id == "RET-FB"
        assert decision.matched_todo_id == "TODO-FB"
        assert any("Parse failed" in n for n in decision.audit_notes)
        assert any("Fallback after" in n for n in decision.audit_notes)

    def test_make_fallback_decision_from_task_return(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        tr = _make_task_return(return_id="RET-FB2", todo_id="TODO-FB2")
        decision = reviewer._make_fallback_decision(tr)
        assert decision.decision == "manual_hold"
        assert decision.confidence == 0.0
        assert decision.return_id == "RET-FB2"
        assert decision.matched_todo_id == "TODO-FB2"


# ---------------------------------------------------------------------------
# review_return integration
# ---------------------------------------------------------------------------


class TestReviewReturn:
    def test_successful_review_returns_decision(self):
        call_model = MagicMock(
            return_value=_make_review_json(
                decision="complete",
                confidence=0.95,
            )
        )
        reviewer = LangGraphReflexiveReviewer(call_model)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])
        assert isinstance(decision, TaskDecision)
        assert decision.decision == "complete"

    def test_graph_failure_returns_fallback(self):
        call_model = MagicMock(side_effect=RuntimeError("Model unavailable"))
        reviewer = LangGraphReflexiveReviewer(call_model)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])
        assert isinstance(decision, TaskDecision)
        assert decision.decision == "manual_hold"
        assert decision.confidence == 0.0
        assert any("Model unavailable" in n for n in decision.audit_notes)

    def test_review_with_candidate_todos_and_artifacts(self):
        call_model = MagicMock(
            return_value=_make_review_json(
                decision="needs_more_work",
                confidence=0.5,
            )
        )
        reviewer = LangGraphReflexiveReviewer(call_model)
        tr = _make_task_return()
        decision = reviewer.review_return(
            tr,
            candidate_todos=[{"id": "TODO-999"}],
            artifacts=["report.json"],
        )
        assert isinstance(decision, TaskDecision)
        assert decision.decision == "needs_more_work"

    def test_null_result_summary_handled(self):
        call_model = MagicMock(return_value=_make_review_json(confidence=0.85))
        reviewer = LangGraphReflexiveReviewer(call_model)
        tr = TaskReturn(
            return_id="R-NULL",
            todo_id="T-NULL",
            job_id="J001",
            playbook="test",
            queue="core",
            result_summary="(none)",
            exit_code=0,
            artifacts=[],
        )
        decision = reviewer.review_return(tr, [], [])
        assert isinstance(decision, TaskDecision)

    def test_graph_invoke_returns_no_final_decision(self):
        call_model = MagicMock(return_value=_make_review_json(confidence=0.3))
        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=1)
        reviewer._graph = MagicMock(invoke=MagicMock(return_value={}))
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])
        assert isinstance(decision, TaskDecision)
        assert decision.decision == "manual_hold"
        assert any("no final_decision" in n for n in decision.audit_notes)


# ---------------------------------------------------------------------------
# Prompt Builders
# ---------------------------------------------------------------------------


class TestPromptBuilders:
    def test_draft_prompt_includes_key_fields(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "task_return_id": "RET-P",
            "todo_id": "TODO-P",
            "result_summary": "Some output",
            "playbook": "test_playbook",
            "exit_code": 0,
        }
        prompt = reviewer._build_draft_prompt(state)
        assert "RET-P" in prompt
        assert "TODO-P" in prompt
        assert "test_playbook" in prompt
        assert "Some output" in prompt
        assert "INITIAL DRAFT" in prompt

    def test_revise_prompt_includes_prior_reflections(self):
        reviewer = LangGraphReflexiveReviewer(MagicMock())
        state: ReviewerState = {
            "task_return_id": "RET-P",
            "todo_id": "TODO-P",
            "result_summary": "Some output",
            "playbook": "test_playbook",
            "exit_code": 0,
            "reflection_notes": ["prior note 1", "prior note 2"],
            "missing_evidence": ["evidence_a.log"],
            "artifacts": ["artifact_1.txt"],
        }
        prompt = reviewer._build_revise_prompt(state)
        assert "prior note 1" in prompt
        assert "evidence_a.log" in prompt
        assert "artifact_1.txt" in prompt
        assert "REVISED review" in prompt
