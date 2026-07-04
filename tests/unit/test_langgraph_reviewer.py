"""Unit tests for LangGraphReflexiveReviewer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from general_ludd.review.langgraph_reviewer import LangGraphReflexiveReviewer
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn


def _make_task_return(
    return_id: str = "R1",
    todo_id: str = "T1",
    result_summary: str = "All tests passed.",
    exit_code: int = 0,
    playbook: str = "implementation.yml",
) -> TaskReturn:
    return TaskReturn(
        return_id=return_id,
        todo_id=todo_id,
        job_id=f"JOB-{return_id}",
        playbook=playbook,
        queue="model",
        work_type="code",
        exit_code=exit_code,
        result_summary=result_summary,
    )


def _high_confidence_response(decision: str = "complete") -> str:
    """Return JSON for a high-confidence review."""
    return json.dumps({
        "decision": decision,
        "confidence": 0.95,
        "audit_notes": ["Tests pass", "Coverage adequate"],
        "evidence_refs": ["coverage.xml", "test_output.log"],
        "reflection": "All evidence is consistent, no gaps.",
        "missing_evidence": [],
        "todo_updates": {},
        "child_todos": [],
        "validation_requests": [],
        "git_requests": [],
        "policy_flags": [],
    })


def _low_confidence_then_high() -> tuple[str, str, str]:
    """Low confidence first, then evidence gather, then high confidence revise."""
    draft = json.dumps({
        "decision": "needs_more_work",
        "confidence": 0.45,
        "audit_notes": ["Test output unclear"],
        "evidence_refs": [],
        "reflection": "Cannot verify test results without seeing the output file.",
        "missing_evidence": ["test_output.log", "coverage report"],
        "todo_updates": {},
        "child_todos": [],
        "validation_requests": [],
        "git_requests": [],
        "policy_flags": [],
    })
    revise = json.dumps({
        "decision": "complete",
        "confidence": 0.92,
        "audit_notes": ["Found test_output.log — all tests pass", "coverage report shows 85%"],
        "evidence_refs": ["test_output.log", "coverage.xml"],
        "reflection": "Evidence now supports completion. Confidence increased.",
        "missing_evidence": [],
        "todo_updates": {},
        "child_todos": [],
        "validation_requests": [],
        "git_requests": [],
        "policy_flags": [],
    })
    return draft, "", revise


# ── Tests ───────────────────────────────────────────────────────────────


class TestLangGraphReflexiveReviewer:
    """Tests for the langgraph-based reflexive reviewer."""

    def test_high_confidence_single_pass(self) -> None:
        """Mock model returns structured review with high confidence → single pass."""
        call_model = MagicMock(return_value=_high_confidence_response("complete"))

        reviewer = LangGraphReflexiveReviewer(
            call_model,
            max_iterations=3,
            confidence_threshold=0.8,
        )
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], ["test_output.log", "coverage.xml"])

        assert isinstance(decision, TaskDecision)
        assert decision.decision == "complete"
        assert decision.confidence == 0.95
        assert any("[reflexive-review]" in n for n in decision.audit_notes)
        assert call_model.call_count == 1

    def test_high_confidence_decision_needs_more_work(self) -> None:
        """High confidence for a non-complete decision still exits after one pass."""
        call_model = MagicMock(return_value=_high_confidence_response("needs_more_work"))

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=3, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "needs_more_work"
        assert decision.confidence == 0.95
        assert call_model.call_count == 1

    def test_low_confidence_iterates_through_evidence_and_revise(self) -> None:
        """Low confidence first → evidence gather → revise with high confidence."""
        draft, _, revise = _low_confidence_then_high()
        call_model = MagicMock(side_effect=[draft, revise])

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=3, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], ["test_output.log", "coverage.xml"])

        assert decision.decision == "complete"
        assert decision.confidence == 0.92
        assert call_model.call_count >= 2  # draft + at least one revise

    def test_confidence_threshold_gating(self) -> None:
        """A review with confidence below threshold but above some lower bound iterates."""
        below_threshold = json.dumps({
            "decision": "complete",
            "confidence": 0.65,
            "audit_notes": ["Mostly confident"],
            "evidence_refs": [],
            "reflection": "Could use more evidence.",
            "missing_evidence": ["test_output.log"],
        })
        above_threshold = json.dumps({
            "decision": "complete",
            "confidence": 0.90,
            "audit_notes": ["Confirmed"],
            "evidence_refs": ["test_output.log"],
            "reflection": "All good now.",
            "missing_evidence": [],
        })
        call_model = MagicMock(side_effect=[below_threshold, above_threshold])

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=3, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], ["test_output.log"])

        assert decision.decision == "complete"
        assert decision.confidence == 0.90
        assert call_model.call_count >= 2

    def test_max_iterations_cap(self) -> None:
        """After max_iterations, accept the final output regardless of confidence."""
        low = json.dumps({
            "decision": "needs_more_work",
            "confidence": 0.30,
            "audit_notes": ["Still unclear"],
            "evidence_refs": [],
            "reflection": "Not enough data.",
            "missing_evidence": ["more_data"],
        })
        call_model = MagicMock(return_value=low)

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "needs_more_work"
        assert decision.confidence == 0.30
        assert call_model.call_count <= 2 + 1  # max_iterations drafts/revises

    def test_persistently_low_confidence_exhausts_iterations(self) -> None:
        """When all iterations produce low confidence, accept the final output."""
        low = json.dumps({
            "decision": "needs_more_work",
            "confidence": 0.25,
            "audit_notes": ["Insufficient evidence"],
            "evidence_refs": [],
            "reflection": "Critical files missing.",
            "missing_evidence": ["coverage.xml", "lint_output.log"],
        })
        call_model = MagicMock(return_value=low)

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=3, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision is not None
        assert decision.decision == "needs_more_work"
        assert "reflexive-review" in " ".join(decision.audit_notes)

    def test_model_call_failure_produces_fallback(self) -> None:
        """When every model call returns None, produce a manual_hold fallback."""
        call_model = MagicMock(return_value=None)

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "manual_hold"
        assert decision.confidence == 0.0

    def test_graph_exception_triggers_fallback(self) -> None:
        """Exceptions during graph invoke produce a manual_hold fallback."""
        call_model = MagicMock(side_effect=RuntimeError("model unavailable"))

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "manual_hold"
        assert any("Reflexive review graph error" in n for n in decision.audit_notes)

    def test_review_return_includes_task_context(self) -> None:
        """The draft prompt includes playbook, exit_code, and result_summary."""
        call_model = MagicMock(return_value=_high_confidence_response("complete"))

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return(
            result_summary="Fixed the bug.",
            exit_code=0,
            playbook="bug_fix.yml",
        )
        reviewer.review_return(tr, [], [])

        prompt = call_model.call_args_list[0].args[0]
        assert "bug_fix.yml" in prompt
        assert "Fixed the bug." in prompt

    def test_all_fields_preserved_in_task_decision(self) -> None:
        """All fields from ReviewWithReflection propagate to TaskDecision."""
        full = json.dumps({
            "decision": "complete",
            "confidence": 0.88,
            "audit_notes": ["note 1", "note 2"],
            "evidence_refs": ["artifact_a", "artifact_b"],
            "reflection": "Thorough review.",
            "missing_evidence": [],
            "todo_updates": {"status": "done"},
            "child_todos": [{"title": "follow-up"}],
            "validation_requests": ["validate_security"],
            "git_requests": ["create_branch"],
            "policy_flags": ["security_ok"],
        })
        call_model = MagicMock(return_value=full)

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"
        assert decision.confidence == 0.88
        assert decision.todo_updates == {"status": "done"}
        assert decision.child_todos == [{"title": "follow-up"}]
        assert decision.validation_requests == ["validate_security"]
        assert decision.git_requests == ["create_branch"]
        assert decision.policy_flags == ["security_ok"]

    def test_long_result_summary_not_crashing(self) -> None:
        """A result_summary longer than 3000 chars does not crash."""
        long_summary = "x" * 10000
        call_model = MagicMock(return_value=_high_confidence_response("complete"))

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return(result_summary=long_summary)
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"
        call_model.assert_called()
        prompt = call_model.call_args_list[0].args[0]
        assert long_summary[:3000] in prompt

    def test_invalid_model_json_is_handled(self) -> None:
        """When the model returns invalid JSON, the reviewer handles it gracefully."""
        call_model = MagicMock(return_value="not valid json at all {{{{{")

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision is not None
        assert decision.decision == "manual_hold"

    def test_json_with_fence_trimmed(self) -> None:
        """Model output inside markdown code fences is parsed correctly."""
        raw = (
            "```json\n"
            + json.dumps({
                "decision": "complete",
                "confidence": 0.91,
                "audit_notes": ["good"],
                "reflection": "fine",
                "missing_evidence": [],
            })
            + "\n```"
        )
        call_model = MagicMock(return_value=raw)

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=2, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"
        assert decision.confidence == 0.91

    def test_evidence_gather_finds_artifacts(self) -> None:
        """When missing_evidence items match existing artifacts, they are marked found."""
        draft = json.dumps({
            "decision": "needs_more_work",
            "confidence": 0.40,
            "audit_notes": ["unclear"],
            "evidence_refs": [],
            "reflection": "Need to see test output.",
            "missing_evidence": ["test_output.log", "lint_results.txt"],
        })
        revise = json.dumps({
            "decision": "complete",
            "confidence": 0.85,
            "audit_notes": ["tests pass, lint clean"],
            "evidence_refs": ["test_output.log", "lint_results.txt"],
            "reflection": "Evidence gathered, confidence raised.",
            "missing_evidence": [],
        })
        call_model = MagicMock(side_effect=[draft, revise])

        reviewer = LangGraphReflexiveReviewer(call_model, max_iterations=3, confidence_threshold=0.8)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], ["test_output.log", "lint_results.txt"])

        assert decision.decision == "complete"
        assert call_model.call_count >= 2
