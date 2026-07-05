"""E2E integration proof for G11 multi-agent consensus/debate.

These tests exercise the full ConsensusReviewer → LangGraphConsensusEngine →
TaskDecision pipeline, proving that parallel multi-agent debate actually converges
(or invokes the judge when it cannot).  All reviewer calls are mocked so the
tests exercise the real orchestration logic without live LLM calls.

A. 3 agents unanimously agree → consensus reached in 1 round
B. 3 agents disagree → multiple rounds until convergence
C. Max rounds exhausted → judge tiebreaker invoked
D. ConsensusReviewer integration: full review_return → TaskDecision flow
E. Parallel agent execution verified (all agents called in same round)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

from general_ludd.review.consensus_reviewer import ConsensusReviewer
from general_ludd.review.langgraph_consensus import LangGraphConsensusEngine
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_APPROVE = "approve\nlooks good"
_REJECT = "reject\nnot acceptable"
_NEEDS_CHANGES = "needs_changes\nneeds more work"


def _make_gateway(*responses: str) -> MagicMock:
    """Return a MagicMock whose call_model() returns each response in order."""
    idx = 0

    def _call_model(profile_id: str, messages: list[dict[str, str]], **kwargs: object) -> str:
        nonlocal idx
        r = responses[idx % len(responses)]
        idx += 1
        return r

    gw = MagicMock()
    gw.call_model.side_effect = _call_model
    return gw


def _make_task_return(**overrides: Any) -> TaskReturn:
    """Build a minimal TaskReturn for review_return()."""
    defaults: dict[str, Any] = {
        "return_id": "R1",
        "todo_id": "T1",
        "job_id": "J1",
        "playbook": "test.yml",
        "queue": "model",
        "work_type": "code",
        "exit_code": 0,
        "result_summary": "All tasks completed successfully.",
    }
    defaults.update(overrides)
    return TaskReturn(**defaults)


# ---------------------------------------------------------------------------
# A. Unanimous consensus — 1 round
# ---------------------------------------------------------------------------

class TestUnanimousConsensus:
    """3 agents unanimously agree → consensus reached in 1 round."""

    def test_all_approve_consensus_in_round_1(self):
        gateway = _make_gateway(_APPROVE)

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"
        assert decision.confidence == 1.0
        assert "reached" in decision.audit_notes[0]
        assert "in 1 rounds" in decision.audit_notes[0]
        assert "verdict=approve" in decision.audit_notes[0]
        assert gateway.call_model.call_count == 3

    def test_all_reject_consensus_in_round_1(self):
        gateway = _make_gateway(_REJECT)

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "needs_more_work"
        assert decision.confidence == 1.0
        assert "reached" in decision.audit_notes[0]
        assert "in 1 rounds" in decision.audit_notes[0]
        assert "verdict=reject" in decision.audit_notes[0]
        assert gateway.call_model.call_count == 3

    def test_all_needs_changes_consensus_in_round_1(self):
        gateway = _make_gateway(_NEEDS_CHANGES)

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "manual_hold"
        assert decision.confidence == 1.0
        assert "reached" in decision.audit_notes[0]
        assert "in 1 rounds" in decision.audit_notes[0]
        assert "verdict=needs_changes" in decision.audit_notes[0]
        assert gateway.call_model.call_count == 3

    def test_2_agents_unanimous_in_1_round(self):
        gateway = _make_gateway(_APPROVE)

        reviewer = ConsensusReviewer(gateway, num_agents=2, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"
        assert decision.confidence == 1.0
        assert gateway.call_model.call_count == 2


# ---------------------------------------------------------------------------
# B. Disagreement → convergence over multiple rounds
# ---------------------------------------------------------------------------

class TestDisagreementConvergence:
    """3 agents disagree in round 1, converge in round 2."""

    def test_dissent_then_converge_on_approve(self):
        gateway = _make_gateway(
            _APPROVE,
            _REJECT,
            _NEEDS_CHANGES,
            _APPROVE,
            _APPROVE,
            _APPROVE,
        )

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"
        assert decision.confidence == 1.0
        assert "reached" in decision.audit_notes[0]
        assert "in 2 rounds" in decision.audit_notes[0]
        assert decision.audit_notes[0].count("verdict=approve") == 1
        assert gateway.call_model.call_count == 6

    def test_dissent_then_converge_on_reject(self):
        gateway = _make_gateway(
            _APPROVE,
            _REJECT,
            _NEEDS_CHANGES,
            _REJECT,
            _REJECT,
            _REJECT,
        )

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "needs_more_work"
        assert "verdict=reject" in decision.audit_notes[0]
        assert gateway.call_model.call_count == 6

    def test_dissent_prompts_include_prior_round_info(self):
        gateway = _make_gateway(
            _APPROVE,
            _REJECT,
            _NEEDS_CHANGES,
            _APPROVE,
            _APPROVE,
            _APPROVE,
        )

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        reviewer.review_return(tr, [], [])

        round2_calls = gateway.call_model.call_args_list[3:]
        for call_obj in round2_calls:
            _, messages = call_obj[0]
            prompt = messages[0]["content"]
            assert "NOT unanimous" in prompt
            assert any(tag in prompt for tag in ("Agent 1:", "Agent 2:", "Agent 3:"))


# ---------------------------------------------------------------------------
# C. Max rounds exhausted → judge tiebreaker
# ---------------------------------------------------------------------------

class TestMaxRoundsJudgeTiebreaker:
    """When agents never converge within max_rounds, the judge breaks the tie."""

    def test_judge_tiebreaker_after_max_rounds(self):
        gateway = _make_gateway(
            _APPROVE, _REJECT, _NEEDS_CHANGES,
            _APPROVE, _REJECT, _NEEDS_CHANGES,
            _APPROVE,
        )

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=2, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "complete"
        assert "deadlocked" in decision.audit_notes[0]
        assert "in 2 rounds" in decision.audit_notes[0]
        assert "verdict=approve" in decision.audit_notes[0]
        assert gateway.call_model.call_count == 7

    def test_judge_tiebreaker_with_reject_verdict(self):
        gateway = _make_gateway(
            _APPROVE, _REJECT, _NEEDS_CHANGES,
            _APPROVE, _REJECT, _NEEDS_CHANGES,
            "reject\njudge says no",
        )

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=2, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "needs_more_work"
        assert "deadlocked" in decision.audit_notes[0]
        assert "verdict=reject" in decision.audit_notes[0]

    def test_judge_tiebreaker_with_needs_changes_verdict(self):
        gateway = _make_gateway(
            _APPROVE, _REJECT, _NEEDS_CHANGES,
            _APPROVE, _REJECT, _NEEDS_CHANGES,
            _NEEDS_CHANGES,
        )

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=2, use_langgraph=True)
        tr = _make_task_return()
        decision = reviewer.review_return(tr, [], [])

        assert decision.decision == "manual_hold"
        assert "verdict=needs_changes" in decision.audit_notes[0]


# ---------------------------------------------------------------------------
# D. ConsensusReviewer integration — full review_return → TaskDecision
# ---------------------------------------------------------------------------

class TestConsensusReviewerTaskDecisionFlow:
    """Full review_return() → TaskDecision pipeline for every verdict type."""

    def test_approve_yields_complete_decision(self):
        gateway = _make_gateway(_APPROVE)
        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return(return_id="R-A", todo_id="T-A", exit_code=0)

        decision = reviewer.review_return(tr, [{"id": 1}], ["artifact1.txt"])
        assert isinstance(decision, TaskDecision)
        assert decision.decision == "complete"
        assert decision.return_id == "R-A"
        assert decision.matched_todo_id == "T-A"
        assert decision.confidence == 1.0

    def test_reject_yields_needs_more_work_decision(self):
        gateway = _make_gateway(_REJECT)
        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return(return_id="R-R", exit_code=1)

        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "needs_more_work"
        assert decision.return_id == "R-R"

    def test_needs_changes_yields_manual_hold_decision(self):
        gateway = _make_gateway(_NEEDS_CHANGES)
        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return(return_id="R-NC")

        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "manual_hold"

    def test_audit_notes_include_rounds_and_agent_count(self):
        gateway = _make_gateway(_APPROVE)
        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()

        decision = reviewer.review_return(tr, [], [])
        assert len(decision.audit_notes) == 1
        assert "3 agents" in decision.audit_notes[0]
        assert "in 1 rounds" in decision.audit_notes[0]
        assert "confidence=1.00" in decision.audit_notes[0]

    def test_individual_agent_gateway_failure_graceful_degradation(self):
        """Per-agent gateway failure caught by the wrapper and treated as needs_changes.

        The ConsensusReviewer's internal _gateway_reviewer wrapper catches exceptions
        and returns "needs_changes\\nGateway call failed".  All 3 agents get the same
        fallback response, so they converge on needs_changes in round 1.
        """
        gateway = MagicMock()
        gateway.call_model.side_effect = RuntimeError("gateway down")

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return(return_id="R-ERR")

        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "manual_hold"
        assert decision.confidence == 1.0
        assert "verdict=needs_changes" in decision.audit_notes[0]
        assert "in 1 rounds" in decision.audit_notes[0]
        assert gateway.call_model.call_count == 3


# ---------------------------------------------------------------------------
# E. Parallel agent execution — all agents called in same round
# ---------------------------------------------------------------------------

class TestParallelAgentExecution:
    """Verify parallel fan-out: all agents in a round are called concurrently."""

    def test_all_agents_called_in_same_round(self):
        """All 3 agents receive distinct prompts in the first round.

        Uses max_rounds=1 — after the first round without consensus,
        the judge fires (a 4th call).  We verify that the first 3
        calls are the 3 distinct agent prompts.
        """
        gateway = _make_gateway(_APPROVE, _REJECT, _NEEDS_CHANGES)

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=1, use_langgraph=True)
        tr = _make_task_return()
        reviewer.review_return(tr, [], [])

        call_args = []
        for c in gateway.call_model.call_args_list:
            _, messages = c[0]
            call_args.append(messages[0]["content"])

        for i in range(3):
            label = f"reviewer agent {i + 1} of 3"
            assert any(label in arg for arg in call_args), (
                f"agent {i + 1} not found in any prompt call"
            )

    def test_parallel_execution_is_concurrent_not_serial(self):
        gateway = MagicMock()

        call_timestamps: list[float] = []

        def _slow_call(profile_id: str, messages: list[dict[str, str]], **kwargs: object) -> str:
            call_timestamps.append(time.monotonic())
            time.sleep(0.05)
            return _APPROVE

        gateway.call_model.side_effect = _slow_call

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return()
        reviewer.review_return(tr, [], [])

        assert len(call_timestamps) == 3
        spread = max(call_timestamps) - min(call_timestamps)
        serial_min = 3 * 0.05
        assert spread < serial_min, (
            f"Call timestamps spread={spread:.3f}s, but serial execution "
            f"would be ≥{serial_min:.3f}s → agents were NOT concurrent"
        )

    def test_engine_run_debate_parallel_fan_out(self):
        reviewer_mock = _make_gateway(_APPROVE)
        gateway = reviewer_mock

        def _callable(prompt: str) -> str:
            args, _ = gateway.call_model.call_args_list[gateway.call_model.call_count]
            return gateway.call_model(args[0], args[1])

        engine = LangGraphConsensusEngine(reviewer_callable=lambda p: _APPROVE)
        result = engine.run_debate("Is this correct?", context="ctx", num_agents=3)
        assert result["consensus"] is True
        assert result["rounds"] == 1
        assert len(result["agent_votes"]) == 3
        for v in result["agent_votes"]:
            assert v["round_num"] == 1

    def test_agent_prompts_contain_context(self):
        gateway = _make_gateway(_APPROVE)

        reviewer = ConsensusReviewer(gateway, num_agents=3, max_rounds=5, use_langgraph=True)
        tr = _make_task_return(result_summary="deployed the app")
        reviewer.review_return(tr, [{"id": 1}, {"id": 2}], ["out.txt"])

        for c in gateway.call_model.call_args_list:
            _, messages = c[0]
            prompt = messages[0]["content"]
            assert "deployed the app" in prompt
            assert "2 pending" in prompt or "Candidate todos" in prompt
            assert "1 available" in prompt or "Artifacts" in prompt

    def test_transcript_structure_with_disagreement(self):
        gateway = _make_gateway(
            _APPROVE, _REJECT, _NEEDS_CHANGES,
            _APPROVE, _APPROVE, _APPROVE,
        )

        def _reviewer(prompt: str) -> str:
            return gateway.call_model("p", [{"role": "user", "content": prompt}])

        engine = LangGraphConsensusEngine(reviewer_callable=_reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=5)

        assert len(result["transcript"]) == 2
        assert result["transcript"][0]["round"] == 1
        assert result["transcript"][1]["round"] == 2
        assert len(result["transcript"][0]["votes"]) == 3
        assert len(result["transcript"][1]["votes"]) == 3
        first_round_votes = [v["verdict"] for v in result["transcript"][0]["votes"]]
        assert set(first_round_votes) == {"approve", "reject", "needs_changes"}
        second_round_votes = [v["verdict"] for v in result["transcript"][1]["votes"]]
        assert second_round_votes == ["approve", "approve", "approve"]
