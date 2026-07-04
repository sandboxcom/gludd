"""Tests for ConsensusReviewer adapter (G11 wiring)."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.review.consensus_reviewer import ConsensusReviewer
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


def _make_gateway(return_value: object) -> MagicMock:
    """Create a gateway mock whose call_model accepts (profile_id, messages)."""
    gw = MagicMock()

    def call_model(profile_id: str, messages: list[dict[str, str]], **kwargs: object) -> object:
        prompt = messages[0]["content"] if messages else ""
        if callable(return_value):
            return return_value(prompt)
        if isinstance(return_value, Exception):
            raise return_value
        return return_value

    gw.call_model.side_effect = call_model
    return gw


class TestConsensusReviewerReviewReturn:
    def test_review_return_consensus_approve(self) -> None:
        """When all agents approve, returns a complete decision."""
        gateway = _make_gateway("approve\nlooks good")

        reviewer = ConsensusReviewer(gateway, num_agents=2, max_rounds=1)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert isinstance(decision, TaskDecision)
        assert decision.decision == "complete"
        assert decision.confidence == 1.0
        assert "Consensus review: reached" in decision.audit_notes[0]

    def test_review_return_consensus_reject(self) -> None:
        """When all agents reject, returns a needs_more_work decision."""
        gateway = _make_gateway("reject\nnot acceptable")

        reviewer = ConsensusReviewer(gateway, num_agents=2, max_rounds=1)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "needs_more_work"
        assert decision.confidence == 1.0

    def test_review_return_consensus_needs_changes(self) -> None:
        """When all agents say needs_changes, returns manual_hold."""
        gateway = _make_gateway("needs_changes\nadd tests")

        reviewer = ConsensusReviewer(gateway, num_agents=2, max_rounds=1)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "manual_hold"
        assert decision.confidence == 1.0
        assert "verdict=needs_changes" in decision.audit_notes[0]

    def test_review_return_deadlock_falls_back(self) -> None:
        """Deadlocked debate results in manual_hold."""
        responses = [
            "approve\nok",
            "reject\nno",
            "needs_changes\nfix",
        ]

        def mixed(prompt: str) -> str:
            return responses.pop(0) if responses else "needs_changes"

        def call_model(profile_id: str, messages: list[dict[str, str]], **kw: object) -> str:
            p = messages[0]["content"] if messages else ""
            return mixed(p)

        gateway = MagicMock()
        gateway.call_model.side_effect = call_model

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=1)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "manual_hold"

    def test_review_return_handles_tuple_response(self) -> None:
        """Gateway returning a tuple is handled gracefully."""
        gateway = MagicMock()
        gateway.call_model.return_value = ("approve\nlooks good", {"usage": {}})

        reviewer = ConsensusReviewer(gateway, num_agents=2, max_rounds=1)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"

    def test_review_return_fallback_on_debate_exception(self) -> None:
        """Debate exception falls back to manual_hold (caught inside gateway reviewer)."""
        gateway = MagicMock()
        gateway.call_model.side_effect = RuntimeError("model down")

        reviewer = ConsensusReviewer(gateway)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "manual_hold"
        assert decision.confidence == 1.0
        assert "Consensus review: reached" in decision.audit_notes[0]

    def test_review_return_includes_task_context(self) -> None:
        """The debate question includes playbook, exit_code, and result_summary."""
        gateway = _make_gateway("approve\ngood")

        reviewer = ConsensusReviewer(gateway, num_agents=1, max_rounds=1)
        tr = _make_task_return(
            result_summary="Fixed the bug.",
            exit_code=0,
            playbook="bug_fix.yml",
        )
        reviewer.review_return(tr, [], [])

        # call_model(profile_id, messages) — messages[0]["content"] is the prompt
        messages = gateway.call_model.call_args_list[0].args[1]
        prompt = messages[0]["content"]
        assert "bug_fix.yml" in prompt
        assert "0" in prompt
        assert "Fixed the bug." in prompt

    def test_review_return_long_result_truncated(self) -> None:
        """Result summary longer than 2000 chars is truncated in the prompt."""
        gateway = _make_gateway("approve\ngood")

        reviewer = ConsensusReviewer(gateway, num_agents=1, max_rounds=1)
        long_summary = "x" * 5000
        tr = _make_task_return(result_summary=long_summary)
        reviewer.review_return(tr, [], [])

        messages = gateway.call_model.call_args_list[0].args[1]
        prompt = messages[0]["content"]
        truncated = long_summary[:2000]
        assert truncated in prompt
        assert long_summary not in prompt
